import type { Goal, NamedItem } from "@/features/profiles/api";
import { apiFetch } from "@/lib/http";

// Mirrors apps/api/app/modules/discovery/schemas.py.

/** The longest stretch you're both free in the coming week, already in YOUR timezone: weekday 0
 * is Monday, minutes from your local midnight, end_min at most 1440 (midnight). */
export interface OverlapWindow {
  weekday: number;
  start_min: number;
  end_min: number;
}

/** Why a student was suggested. Each kind is drawn from one field they show on their public
 * profile. There is never a score or a percentage. Narrow on `kind`. */
export type MatchReason =
  | { kind: "skills_covered"; items: NamedItem[] }
  | { kind: "shared_interests"; items: NamedItem[] }
  | { kind: "availability_overlap"; window: OverlapWindow }
  | { kind: "shared_goals"; goals: Goal[] };

export interface Match {
  user_id: string;
  display_name: string;
  headline: string;
  /** At least one, in a fixed order: skills, interests, availability, goals. */
  reasons: MatchReason[];
}

export interface Discovery {
  /** At most five, best first. Empty when nobody suitable is around yet. */
  matches: Match[];
}

export type FeedbackAction = "connected" | "dismissed";

export const discoveryApi = {
  /** 403 `discovery_opt_in_required` until your profile is discoverable. With `projectId` (a
   * project you own; anything else is 404), matches favour that project's missing skills. */
  matches: (projectId?: string) =>
    apiFetch<Discovery>(projectId ? `/discovery?project_id=${encodeURIComponent(projectId)}` : "/discovery"),
  /** Always 204. Recorded only if you consented to feedback; otherwise nothing is stored. */
  feedback: (candidateId: string, action: FeedbackAction) =>
    apiFetch<void>("/discovery/feedback", { method: "POST", json: { candidate_id: candidateId, action } }),
};
