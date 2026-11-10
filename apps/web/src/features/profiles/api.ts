import { apiFetch } from "@/lib/http";

// Mirrors the Literal unions in apps/api/app/modules/profiles/schemas.py.
export type Goal = "studying" | "project_development" | "research" | "mentorship";
export type SkillLevel = "beginner" | "intermediate" | "advanced";
export type ReportReason = "spam" | "harassment" | "inappropriate_content" | "impersonation" | "other";

export interface SkillOption {
  slug: string;
  name: string;
  category: string;
}

export interface InterestOption {
  slug: string;
  name: string;
}

export interface SkillInput {
  slug: string;
  level: SkillLevel;
}

export interface AvailabilitySlot {
  /** 0 = Monday ... 6 = Sunday. */
  weekday: number;
  start_min: number;
  /** 1440 means midnight at the end of the day. */
  end_min: number;
}

/** Mirrors ProfileInput in apps/api/app/modules/profiles/schemas.py: a full replace, so every
 * field is sent on every save. Extra keys are rejected by the API. */
export interface ProfileInput {
  display_name: string;
  headline: string;
  bio: string;
  goals: Goal[];
  timezone: string;
  skills: SkillInput[];
  seeking_skills: string[];
  interests: string[];
  availability: AvailabilitySlot[];
  discoverable: boolean;
  feedback_consent: boolean;
}

export interface NamedSkill {
  slug: string;
  name: string;
  level: SkillLevel;
}

export interface NamedItem {
  slug: string;
  name: string;
}

export interface PublicProfile {
  user_id: string;
  display_name: string;
  headline: string;
  bio: string;
  goals: Goal[];
  timezone: string;
  skills: NamedSkill[];
  seeking_skills: NamedItem[];
  interests: NamedItem[];
  availability: AvailabilitySlot[];
}

export interface OwnProfile extends PublicProfile {
  discoverable: boolean;
  feedback_consent: boolean;
  updated_at: string;
}

export interface BlockedUser {
  user_id: string;
  /** null when the blocked student never created a profile. */
  display_name: string | null;
  blocked_at: string;
}

export const profilesApi = {
  mine: () => apiFetch<OwnProfile>("/me/profile"),
  save: (input: ProfileInput) => apiFetch<OwnProfile>("/me/profile", { method: "PUT", json: input }),
  skills: () => apiFetch<SkillOption[]>("/skills"),
  interests: () => apiFetch<InterestOption[]>("/interests"),
  get: (userId: string) => apiFetch<PublicProfile>(`/profiles/${userId}`),
};

export const trustApi = {
  block: (userId: string) => apiFetch<void>("/blocks", { method: "POST", json: { user_id: userId } }),
  unblock: (userId: string) => apiFetch<void>(`/blocks/${userId}`, { method: "DELETE" }),
  listBlocks: () => apiFetch<BlockedUser[]>("/blocks"),
  report: (userId: string, reason: ReportReason, details: string) =>
    apiFetch<{ status: string }>("/reports", { method: "POST", json: { user_id: userId, reason, details } }),
};
