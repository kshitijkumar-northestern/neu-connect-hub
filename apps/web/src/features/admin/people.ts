import type { AdminReport } from "@/features/admin/api";
import { ApiError } from "@/lib/http";

/** The target's name as an admin sees it: never the raw user id, never blank. */
export function targetName(report: AdminReport): string {
  return report.target.display_name ?? "A student";
}

/** The reporter's name, or "Deleted account" once they've removed their own account (the
 * reporter is `null` at that point, distinct from a student who never set a display name). */
export function reporterName(report: AdminReport): string {
  if (!report.reporter) return "Deleted account";
  return report.reporter.display_name ?? "A student";
}

/** True for the 403 the admin routes send once this session's MFA has aged out. */
export function isMfaRequired(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 403 && failure.code === "mfa_required";
}
