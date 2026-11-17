import { Badge } from "@/components/ui/badge";

/** Marks a report's target as already suspended (from this report or another one on the same
 * student): dismiss/suspend elsewhere can resolve a report out from under the row showing it. */
export function ReportSuspendedBadge() {
  return <Badge variant="destructive">Suspended</Badge>;
}
