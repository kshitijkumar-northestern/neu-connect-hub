import { GOAL_LABELS, WEEKDAYS } from "@/features/profiles/constants";
import { formatTimeRange } from "@/features/profiles/time";
import type { MatchReason } from "@/features/discovery/api";

// How many names a card leads with before folding the rest into "+N more".
const MAX_NAMES = 3;

/** Up to three names, then "+N more": how cards list skills and interests. */
export function listNames(items: readonly { name: string }[]): string {
  const names = items.map((item) => item.name);
  if (names.length <= MAX_NAMES) return names.join(", ");
  const shown = names.slice(0, MAX_NAMES);
  return `${shown.join(", ")} +${names.length - MAX_NAMES} more`;
}

/** Plain-language rationale for one reason a student was suggested. Pure and unit-tested on its
 * own: never a score or anything that looks like a percentage. */
export function describeReason(reason: MatchReason): string {
  switch (reason.kind) {
    case "skills_covered":
      return `Can help with ${listNames(reason.items)}`;
    case "shared_interests":
      return `Also into ${listNames(reason.items)}`;
    case "availability_overlap": {
      const { weekday, start_min, end_min } = reason.window;
      const day = WEEKDAYS[weekday];
      const range = formatTimeRange(start_min, end_min);
      // The API only sends weekdays 0-6; anything else drops the day rather than guessing one.
      if (!day) return `You're both free ${range} (your time)`;
      if (start_min === 0 && end_min === 1440) return `You're both free all day ${day}s (your time)`;
      return `You're both free ${day}s ${range} (your time)`;
    }
    case "shared_goals": {
      const labels = reason.goals.map((goal) => GOAL_LABELS[goal]).join(", ");
      return reason.goals.length === 1 ? `Shares your goal: ${labels}` : `Shares your goals: ${labels}`;
    }
  }
}
