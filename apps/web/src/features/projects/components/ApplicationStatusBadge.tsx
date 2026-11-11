import { Badge } from "@/components/ui/badge";
import type { ApplicationStatus } from "@/features/projects/api";

// Every ApplicationStatus the API can send, mapped to an action-oriented label a student
// understands at a glance (never a naive title-case of the raw enum).
const LABELS: Record<ApplicationStatus, string> = {
  pending: "Waiting",
  accepted: "Accepted",
  rejected: "Not accepted",
  withdrawn: "Withdrawn",
};

const VARIANTS: Record<ApplicationStatus, "default" | "secondary" | "outline" | "destructive"> = {
  pending: "secondary",
  accepted: "default",
  rejected: "destructive",
  withdrawn: "outline",
};

export function ApplicationStatusBadge({ status }: { status: ApplicationStatus }) {
  return <Badge variant={VARIANTS[status]}>{LABELS[status]}</Badge>;
}
