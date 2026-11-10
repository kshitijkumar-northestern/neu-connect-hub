// Relative timestamps for the conversation list and thread ("2m ago", "Yesterday", ...).
// Uses Intl so it isn't an ad-hoc conversion that breaks on locale or timezone.
const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "Just now", "5m ago", "3h ago", "Yesterday", or a locale date for anything older than a week. */
export function relativeTime(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  const diffMs = now.getTime() - then.getTime();

  if (diffMs < MINUTE) return "Just now";
  if (diffMs < HOUR) return RELATIVE.format(-Math.round(diffMs / MINUTE), "minute");
  if (diffMs < DAY) return RELATIVE.format(-Math.round(diffMs / HOUR), "hour");
  if (diffMs < 2 * DAY) return "Yesterday";
  if (diffMs < 7 * DAY) return RELATIVE.format(-Math.round(diffMs / DAY), "day");
  return then.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** A short clock time for a message bubble, e.g. "9:30 AM". */
export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
