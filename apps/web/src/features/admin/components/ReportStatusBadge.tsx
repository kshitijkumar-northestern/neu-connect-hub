import { Badge } from "@/components/ui/badge";
import type { ReportStatus } from "@/features/admin/api";

// Every ReportStatus the API can send, mapped to a label an admin understands at a glance (never
// a naive title-case of the raw enum).
const LABELS: Record<ReportStatus, string> = {
  open: "Open",
  resolved: "Resolved",
  dismissed: "Dismissed",
};

const VARIANTS: Record<ReportStatus, "default" | "secondary" | "outline"> = {
  open: "default",
  resolved: "secondary",
  dismissed: "outline",
};

export function ReportStatusBadge({ status }: { status: ReportStatus }) {
  return (
    <Badge variant={VARIANTS[status]} role="status">
      {LABELS[status]}
    </Badge>
  );
}
