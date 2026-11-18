"""Timezone-aware weekly overlap: how much of a viewer's coming local week two students are both
free. `weekly_overlap` is the one-shot entry point; `viewer_week` + `overlap_with` split it in two
so a viewer can be scored against many candidates without rebuilding their half each time.

Each student's weekly slots are wall-clock times in their own IANA timezone. Both schedules are
laid onto real instants over the window from the viewer's local midnight on `now`'s date to that
same local midnight seven days later (wall-clock arithmetic, so a week crossing a DST change is
167 or 169 real hours, not always 168), via `zoneinfo`. The window is anchored to the viewer's
local day boundaries, not to the live clock, so a given slot is counted in full exactly once and
the reported window is stable for every request made during that local day.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ml.scoring import Overlap, OverlapWindow, WeeklySlot

_Interval = tuple[datetime, datetime]

_ONE_DAY = timedelta(days=1)
_ONE_WEEK = timedelta(days=7)


@dataclass(frozen=True)
class ViewerWeek:
    """A viewer's half of a weekly overlap, built once and reused across many candidates: the
    viewer's timezone (for reporting the longest window), the window's absolute UTC edges, and
    the viewer's own merged availability as UTC intervals clipped to that window."""

    timezone: str
    window_start: datetime
    window_end: datetime
    intervals: tuple[_Interval, ...]


def weekly_overlap(
    viewer_slots: Sequence[WeeklySlot],
    viewer_timezone: str,
    candidate_slots: Sequence[WeeklySlot],
    candidate_timezone: str,
    *,
    now: datetime,
) -> Overlap:
    """Shared minutes in the viewer's coming local week, and the longest shared stretch
    expressed in the viewer's timezone. `now` must be timezone-aware. A thin wrapper over
    `viewer_week` + `overlap_with`, for when there's only one candidate to score."""
    week = viewer_week(viewer_slots, viewer_timezone, now=now)
    return overlap_with(week, candidate_slots, candidate_timezone)


def viewer_week(slots: Sequence[WeeklySlot], timezone: str, *, now: datetime) -> ViewerWeek:
    """Precomputes the viewer's half of a weekly overlap: the window edges and the viewer's own
    merged availability. `now` must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    window_start, window_end = _viewer_window(timezone, now)
    intervals = _weekly_intervals(slots, timezone, window_start, window_end)
    return ViewerWeek(
        timezone=timezone,
        window_start=window_start,
        window_end=window_end,
        intervals=tuple(intervals),
    )


def overlap_with(
    week: ViewerWeek, candidate_slots: Sequence[WeeklySlot], candidate_timezone: str
) -> Overlap:
    """Shared minutes between a precomputed viewer week and one candidate, and the longest
    shared stretch expressed in the viewer's timezone."""
    candidate_intervals = _weekly_intervals(
        candidate_slots, candidate_timezone, week.window_start, week.window_end
    )
    shared = _intersect(list(week.intervals), candidate_intervals)

    if not shared:
        return Overlap(minutes=0, longest=None)

    total_seconds = sum((end - start).total_seconds() for start, end in shared)
    minutes = math.floor(total_seconds / 60)
    longest = max(shared, key=lambda interval: interval[1] - interval[0])
    return Overlap(minutes=minutes, longest=_window_in_viewer_tz(longest, week.timezone))


def _viewer_window(viewer_timezone: str, now: datetime) -> tuple[datetime, datetime]:
    """The viewer's coming local week, as absolute UTC instants: from the viewer's local
    midnight on `now`'s date to the same local midnight seven days later. Wall-clock arithmetic,
    so a week that crosses a DST change is 167 or 169 real hours, never a flat 168."""
    tz = ZoneInfo(viewer_timezone)
    local_date = now.astimezone(tz).date()
    window_start_local = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
    window_end_local = window_start_local + _ONE_WEEK
    return window_start_local.astimezone(UTC), window_end_local.astimezone(UTC)


def _weekly_intervals(
    slots: Sequence[WeeklySlot], tz_name: str, window_start: datetime, window_end: datetime
) -> list[_Interval]:
    """Every slot occurrence that falls (even partly) inside [window_start, window_end), as
    merged, non-overlapping UTC intervals."""
    if not slots:
        return []
    tz = ZoneInfo(tz_name)
    local_start_date = window_start.astimezone(tz).date()
    local_end_date = window_end.astimezone(tz).date()

    intervals: list[_Interval] = []
    date = local_start_date - _ONE_DAY
    last_date = local_end_date + _ONE_DAY
    while date <= last_date:
        weekday = date.weekday()
        for slot in slots:
            if slot.weekday != weekday:
                continue
            local_start = datetime(date.year, date.month, date.day, tzinfo=tz) + timedelta(
                minutes=slot.start_min
            )
            local_end = datetime(date.year, date.month, date.day, tzinfo=tz) + timedelta(
                minutes=slot.end_min
            )
            start = max(local_start.astimezone(UTC), window_start)
            end = min(local_end.astimezone(UTC), window_end)
            if start < end:
                intervals.append((start, end))
        date += _ONE_DAY

    return _merge(intervals)


def _merge(intervals: list[_Interval]) -> list[_Interval]:
    """Sorted, non-overlapping intervals; touching ones (end == next start) count as one
    continuous stretch."""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda interval: interval[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _intersect(a: list[_Interval], b: list[_Interval]) -> list[_Interval]:
    """The overlap of two sorted, non-overlapping interval lists, itself sorted and
    non-overlapping."""
    result: list[_Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if start < end:
            result.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return result


def _window_in_viewer_tz(interval: _Interval, viewer_timezone: str) -> OverlapWindow:
    """The interval, expressed in the viewer's timezone and clipped to end no later than the
    viewer's next local midnight (so it always fits a single weekday)."""
    tz = ZoneInfo(viewer_timezone)
    start, end = interval
    local_start = start.astimezone(tz)
    next_midnight = (
        datetime(local_start.year, local_start.month, local_start.day, tzinfo=tz) + _ONE_DAY
    )
    clipped_end = min(end, next_midnight.astimezone(UTC))
    local_end = clipped_end.astimezone(tz)

    start_min = local_start.hour * 60 + local_start.minute
    if local_end.date() > local_start.date():
        end_min = 1440
    else:
        end_min = local_end.hour * 60 + local_end.minute
    return OverlapWindow(weekday=local_start.weekday(), start_min=start_min, end_min=end_min)
