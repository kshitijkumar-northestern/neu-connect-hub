import { Badge } from "@/components/ui/badge";
import type { ProjectStatus } from "@/features/projects/api";
import { PROJECT_STATUS_LABELS } from "@/features/projects/status";

const VARIANTS: Record<ProjectStatus, "default" | "secondary" | "outline"> = {
  open: "default",
  full: "secondary",
  closed: "outline",
};

export function ProjectStatusBadge({ status }: { status: ProjectStatus }) {
  return (
    <Badge variant={VARIANTS[status]} role="status">
      {PROJECT_STATUS_LABELS[status]}
    </Badge>
  );
}
