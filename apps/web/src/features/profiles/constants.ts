import type { Goal, ReportReason, SkillLevel } from "@/features/profiles/api";

// Mirrors the limits in apps/api/app/modules/profiles/models.py and schemas.py.
export const DISPLAY_NAME_MAX = 80;
export const HEADLINE_MAX = 120;
export const BIO_MAX = 1000;
export const REPORT_DETAILS_MAX = 1000;
export const MAX_SKILLS = 20;
export const MAX_INTERESTS = 15;
export const MAX_SLOTS = 42;

export const LEVEL_LABELS: Record<SkillLevel, string> = {
  beginner: "Beginner",
  intermediate: "Intermediate",
  advanced: "Advanced",
};

export const LEVELS: readonly SkillLevel[] = ["beginner", "intermediate", "advanced"];

/** Action-oriented labels for each goal (never naively title-cased from the raw enum). */
export const GOAL_LABELS: Record<Goal, string> = {
  studying: "Studying together",
  project_development: "Building a project",
  research: "Doing research",
  mentorship: "Mentorship",
};

export const GOALS: readonly Goal[] = ["studying", "project_development", "research", "mentorship"];

/** Monday (0) through Sunday (6), matching the API's weekday numbering. */
export const WEEKDAYS: readonly string[] = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
];

export const REPORT_REASON_LABELS: Record<ReportReason, string> = {
  spam: "Spam",
  harassment: "Harassment",
  inappropriate_content: "Inappropriate content",
  impersonation: "Impersonation",
  other: "Something else",
};

export const REPORT_REASONS: readonly ReportReason[] = [
  "spam",
  "harassment",
  "inappropriate_content",
  "impersonation",
  "other",
];
