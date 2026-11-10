// Availability times are wall-clock minutes-since-midnight in the profile's own timezone
// (mirrors ProfileInput.availability in apps/api/app/modules/profiles/schemas.py).
// An end of 1440 means midnight at the end of the day, distinct from a start of 0.

function parseTime(time: string): { hours: number; minutes: number } {
  const [hours, minutes] = time.split(":").map(Number);
  return { hours: hours ?? 0, minutes: minutes ?? 0 };
}

/** HH:MM -> minutes since midnight, for a range's start. */
export function timeToStartMinutes(time: string): number {
  const { hours, minutes } = parseTime(time);
  return hours * 60 + minutes;
}

/** HH:MM -> minutes since midnight, for a range's end. 00:00 means midnight (1440). */
export function timeToEndMinutes(time: string): number {
  if (time === "00:00") return 1440;
  return timeToStartMinutes(time);
}

/** Minutes since midnight -> HH:MM for a <input type="time">. 1440 (midnight) displays as 00:00. */
export function minutesToTime(minutes: number): string {
  const wrapped = minutes % 1440;
  const hours = Math.floor(wrapped / 60);
  const mins = wrapped % 60;
  return `${String(hours).padStart(2, "0")}:${String(mins).padStart(2, "0")}`;
}

function formatClock(minutes: number): string {
  const wrapped = minutes % 1440;
  const hours24 = Math.floor(wrapped / 60);
  const mins = wrapped % 60;
  const period = hours24 >= 12 ? "pm" : "am";
  const hours12 = hours24 % 12 === 0 ? 12 : hours24 % 12;
  return `${hours12}:${String(mins).padStart(2, "0")}${period}`;
}

/** Readable range for the public profile view, e.g. "9:30am–5:00pm". */
export function formatTimeRange(startMin: number, endMin: number): string {
  return `${formatClock(startMin)}–${formatClock(endMin)}`;
}
