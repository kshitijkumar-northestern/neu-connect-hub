import type { ProjectStatus } from "@/features/projects/api";

// Action-oriented labels for each project status (never naively title-cased from the raw enum).
export const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
  open: "Open",
  full: "Full",
  closed: "Closed",
};
