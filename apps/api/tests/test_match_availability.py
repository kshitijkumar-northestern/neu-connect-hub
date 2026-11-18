"""`weekly_overlap`: timezone-aware, DST-exact, no database and no implicit clock."""

from datetime import UTC, datetime

import pytest

from app.ml.availability import overlap_with, viewer_week, weekly_overlap
from app.ml.scoring import Overlap, OverlapWindow, WeeklySlot

NY = "America/New_York"


def test_same_zone_simple_overlap() -> None:
    # Monday 2026-06-01, no DST transition nearby: plain wall-clock arithmetic in one zone.
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    viewer = (WeeklySlot(weekday=0, start_min=600, end_min=720),)  # 10:00-12:00
    candidate = (WeeklySlot(weekday=0, start_min=660, end_min=780),)  # 11:00-13:00

    overlap = weekly_overlap(viewer, NY, candidate, NY, now=now)

    assert overlap.minutes == 60
    assert overlap.longest == OverlapWindow(weekday=0, start_min=660, end_min=720)


def test_no_overlap() -> None:
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    viewer = (WeeklySlot(weekday=0, start_min=600, end_min=660),)  # 10:00-11:00
    candidate = (WeeklySlot(weekday=0, start_min=700, end_min=760),)  # 11:40-12:40

    overlap = weekly_overlap(viewer, NY, candidate, NY, now=now)

    assert overlap == Overlap(minutes=0, longest=None)


def test_full_containment() -> None:
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    viewer = (WeeklySlot(weekday=0, start_min=540, end_min=900),)  # 9:00-15:00
    candidate = (WeeklySlot(weekday=0, start_min=600, end_min=660),)  # 10:00-11:00

    overlap = weekly_overlap(viewer, NY, candidate, NY, now=now)

    assert overlap.minutes == 60
    assert overlap.longest == OverlapWindow(weekday=0, start_min=600, end_min=660)


def test_end_min_1440_touching_next_day_zero_merges_into_one_stretch() -> None:
    # Both students: Monday 23:00-24:00 then Tuesday 00:00-01:00. Without merging the touching
    # boundary, this would score as two 60-minute windows instead of one continuous 120.
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    slots = (
        WeeklySlot(weekday=0, start_min=1380, end_min=1440),
        WeeklySlot(weekday=1, start_min=0, end_min=60),
    )

    overlap = weekly_overlap(slots, NY, slots, NY, now=now)

    assert overlap.minutes == 120
    # Clipped at the viewer's next local midnight, even though the real overlap runs past it.
    assert overlap.longest == OverlapWindow(weekday=0, start_min=1380, end_min=1440)


def test_new_york_london_gap_is_four_hours_before_either_fallback() -> None:
    # Week of 2026-10-26: UK already fell back (2026-10-25); NY has not (falls back 2026-11-01).
    # NY 13:00 EDT (UTC-4) lands exactly on London's 17:00 GMT (UTC+0): full 120-minute overlap.
    now = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    viewer = (WeeklySlot(weekday=0, start_min=780, end_min=900),)  # NY Mon 13:00-15:00
    candidate = (WeeklySlot(weekday=0, start_min=1020, end_min=1140),)  # London Mon 17:00-19:00

    overlap = weekly_overlap(viewer, NY, candidate, "Europe/London", now=now)

    assert overlap.minutes == 120
    assert overlap.longest == OverlapWindow(weekday=0, start_min=780, end_min=900)


def test_new_york_london_gap_is_five_hours_after_both_fallbacks() -> None:
    # Week of 2026-11-09: both zones have fallen back. NY 13:00 EST (UTC-5) is now an hour later
    # in UTC than before, so only the last hour of London's slot overlaps: 60 minutes.
    now = datetime(2026, 11, 8, 0, 0, tzinfo=UTC)
    viewer = (WeeklySlot(weekday=0, start_min=780, end_min=900),)  # NY Mon 13:00-15:00
    candidate = (WeeklySlot(weekday=0, start_min=1020, end_min=1140),)  # London Mon 17:00-19:00

    overlap = weekly_overlap(viewer, NY, candidate, "Europe/London", now=now)

    assert overlap.minutes == 60
    assert overlap.longest == OverlapWindow(weekday=0, start_min=780, end_min=840)


def test_week_crossing_new_yorks_fall_back_counts_the_extra_hour() -> None:
    # Sunday 2026-11-01: NY's local day is 25 real hours long (clocks repeat 1:00-2:00am). A
    # full-day slot on both sides should pick up all 25 hours, not a flat 1440.
    now = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    slots = (WeeklySlot(weekday=6, start_min=0, end_min=1440),)  # Sunday, all day

    overlap = weekly_overlap(slots, NY, slots, NY, now=now)

    assert overlap.minutes == 25 * 60
    # The window format always fits a single weekday, so it's still reported as 0-1440.
    assert overlap.longest == OverlapWindow(weekday=6, start_min=0, end_min=1440)


def test_week_crossing_new_yorks_spring_forward_gap_slot_gives_zero() -> None:
    # Sunday 2027-03-14: NY clocks jump from 2:00am straight to 3:00am. A 02:00-03:00 slot is
    # entirely inside the gap and, under zoneinfo's default fold=0, collapses to zero duration.
    now = datetime(2027, 3, 11, 0, 0, tzinfo=UTC)
    slots = (WeeklySlot(weekday=6, start_min=120, end_min=180),)  # Sunday 02:00-03:00

    overlap = weekly_overlap(slots, NY, slots, NY, now=now)

    assert overlap == Overlap(minutes=0, longest=None)


def test_window_is_the_viewers_whole_local_day_regardless_of_the_time_of_day() -> None:
    # The window is anchored to the viewer's local midnight on `now`'s date, not to the live
    # clock, so a Monday slot is counted whole exactly once whatever time of day `now` lands on
    # (mid-morning or a minute before midnight) -- the reported window never shifts on repeat
    # requests through the same local day.
    slots = (WeeklySlot(weekday=0, start_min=600, end_min=720),)  # Mon 10:00-12:00
    mid_morning = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)  # Mon 11:00 America/New_York
    almost_midnight = datetime(2026, 6, 2, 3, 59, tzinfo=UTC)  # Mon 23:59 America/New_York

    for now in (mid_morning, almost_midnight):
        overlap = weekly_overlap(slots, NY, slots, NY, now=now)
        assert overlap.minutes == 120
        assert overlap.longest == OverlapWindow(weekday=0, start_min=600, end_min=720)


def test_asia_kolkata_half_hour_offset() -> None:
    now = datetime(2026, 5, 30, 0, 0, tzinfo=UTC)
    viewer = tuple(WeeklySlot(weekday=day, start_min=0, end_min=1440) for day in range(7))
    candidate = (WeeklySlot(weekday=0, start_min=0, end_min=60),)  # UTC Monday 00:00-01:00

    overlap = weekly_overlap(viewer, "Asia/Kolkata", candidate, "UTC", now=now)

    assert overlap.minutes == 60
    # UTC 00:00-01:00 Monday is Kolkata (UTC+5:30) 05:30-06:30, same Monday.
    assert overlap.longest == OverlapWindow(weekday=0, start_min=330, end_min=390)


def test_longest_window_weekday_can_shift_when_converted_to_the_viewer_zone() -> None:
    # The candidate's slot is "Monday" in Tokyo, but once converted to the viewer's zone (Los
    # Angeles, far enough west) the same instants fall on Sunday evening local time.
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    viewer = tuple(
        WeeklySlot(weekday=day, start_min=0, end_min=1440) for day in range(7)
    )  # available every day, all day
    candidate = (WeeklySlot(weekday=0, start_min=0, end_min=60),)  # Tokyo Monday 00:00-01:00

    overlap = weekly_overlap(viewer, "America/Los_Angeles", candidate, "Asia/Tokyo", now=now)

    assert overlap.minutes == 60
    assert overlap.longest == OverlapWindow(weekday=6, start_min=480, end_min=540)


def test_naive_now_raises_value_error() -> None:
    with pytest.raises(ValueError, match="aware"):
        weekly_overlap((), NY, (), NY, now=datetime(2026, 6, 1))


def test_empty_slots_either_side_gives_no_overlap() -> None:
    now = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
    some_slots = (WeeklySlot(weekday=0, start_min=0, end_min=60),)

    assert weekly_overlap((), NY, some_slots, NY, now=now) == Overlap(minutes=0, longest=None)
    assert weekly_overlap(some_slots, NY, (), NY, now=now) == Overlap(minutes=0, longest=None)
    assert weekly_overlap((), NY, (), NY, now=now) == Overlap(minutes=0, longest=None)


def test_overlap_with_a_precomputed_viewer_week_matches_weekly_overlap() -> None:
    # The precompute split (viewer_week + overlap_with) must agree exactly with the one-shot
    # weekly_overlap, since the ranker scores many candidates against one precomputed viewer.
    viewer = (
        WeeklySlot(weekday=0, start_min=600, end_min=720),
        WeeklySlot(weekday=0, start_min=1380, end_min=1440),
        WeeklySlot(weekday=1, start_min=0, end_min=60),
    )
    cases = [
        # (now, candidate_slots, candidate_timezone)
        (
            datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            (WeeklySlot(weekday=0, start_min=660, end_min=780),),
            NY,
        ),
        (
            datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            (),
            NY,
        ),
        (
            datetime(2026, 10, 25, 0, 0, tzinfo=UTC),
            (WeeklySlot(weekday=0, start_min=1020, end_min=1140),),
            "Europe/London",
        ),
    ]

    for now, candidate_slots, candidate_timezone in cases:
        week = viewer_week(viewer, NY, now=now)
        assert overlap_with(week, candidate_slots, candidate_timezone) == weekly_overlap(
            viewer, NY, candidate_slots, candidate_timezone, now=now
        )
