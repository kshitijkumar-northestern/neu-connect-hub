import { apiFetch } from "@/lib/http";
import type { ReportReason } from "@/features/profiles/api";

// Mirrors apps/api/app/modules/admin/{mfa,reports}_schemas.py. Reasons are the ones students
// choose when they report someone, so the type is shared with the profiles feature.
export type { ReportReason };
export type ReportStatus = "open" | "resolved" | "dismissed";

export interface MfaStatus {
  /** True once the admin has confirmed an authenticator app. */
  enrolled: boolean;
  /** True when this session passed MFA recently enough for the admin routes. */
  verified: boolean;
  /** Unused recovery codes left (0 before enrolment). */
  recovery_codes_left: number;
}

export interface MfaEnrolment {
  /** Base32, for typing into an authenticator app by hand. Shown once. */
  secret: string;
  /** otpauth://totp/... for the QR code. Shown once. */
  otpauth_url: string;
}

export interface RecoveryCodes {
  /** Ten single-use codes, shown exactly once. */
  recovery_codes: string[];
}

export interface Person {
  user_id: string;
  /** null when the student never created a profile. */
  display_name: string | null;
}

export interface AdminReport {
  id: string;
  reason: ReportReason;
  details: string;
  status: ReportStatus;
  created_at: string;
  resolved_at: string | null;
  /** null once the reporter deleted their account. */
  reporter: Person | null;
  target: Person & { suspended: boolean };
}

/** The minimum an admin needs to decide: never message bodies or private profile fields. */
export interface AdminReportDetail extends AdminReport {
  target_context: {
    headline: string;
    member_since: string;
    verified: boolean;
    open_reports: number;
    total_reports: number;
  };
}

export interface Page<T> {
  items: T[];
  next_offset: number | null;
}

/** Exactly one of `code` (6 digits from the app) or `recovery_code`. */
export type MfaProof = { code: string } | { recovery_code: string };

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function reportPath(reportId: string, rest = ""): string {
  return `/admin/reports/${encodeURIComponent(reportId)}${rest}`;
}

export const adminApi = {
  mfaStatus: () => apiFetch<MfaStatus>("/admin/mfa"),
  /** Starts (or restarts) enrolment; 409 `mfa_already_enrolled` once confirmed. */
  enroll: () => apiFetch<MfaEnrolment>("/admin/mfa/enroll", { method: "POST" }),
  /** Confirms enrolment with a code from the app; returns the recovery codes once. */
  confirm: (code: string) => apiFetch<RecoveryCodes>("/admin/mfa/confirm", { method: "POST", json: { code } }),
  /** Steps this session up with a code or a recovery code. */
  verify: (proof: MfaProof) => apiFetch<MfaStatus>("/admin/mfa/verify", { method: "POST", json: proof }),

  reports: ({ status, limit, offset }: { status?: ReportStatus; limit?: number; offset?: number } = {}) =>
    apiFetch<Page<AdminReport>>(`/admin/reports${query({ status, limit, offset })}`),
  /** Viewing a report writes an audit row. */
  report: (reportId: string) => apiFetch<AdminReportDetail>(reportPath(reportId)),
  dismiss: (reportId: string) => apiFetch<AdminReport>(reportPath(reportId, "/dismiss"), { method: "POST" }),
  /** Suspends the reported account, signs it out everywhere and resolves its open reports. */
  suspend: (reportId: string) => apiFetch<AdminReport>(reportPath(reportId, "/suspend"), { method: "POST" }),
};
