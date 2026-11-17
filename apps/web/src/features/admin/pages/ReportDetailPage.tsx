import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { useAdminGate } from "@/features/admin/components/AdminGate";
import { adminApi, type AdminReport, type AdminReportDetail } from "@/features/admin/api";
import { ReportStatusBadge } from "@/features/admin/components/ReportStatusBadge";
import { ReportSuspendedBadge } from "@/features/admin/components/ReportSuspendedBadge";
import { isMfaRequired, reporterName, targetName } from "@/features/admin/people";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { relativeTime } from "@/features/messages/time";
import { REPORT_REASON_LABELS } from "@/features/profiles/constants";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "not-found" | "error";

function isNotFound(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 404;
}

// A date the way SharePage formats a code's expiry: never an ad-hoc conversion that breaks on
// locale or timezone.
function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** A report's full detail: reason, details, who reported whom, the target's context, its status,
 * and for an open report, Dismiss or Suspend (the latter behind a confirm naming the target). */
export function ReportDetailPage() {
  const { reportId } = useParams<{ reportId: string }>();
  const { reverify } = useAdminGate();
  const [load, setLoad] = useState<LoadState>("loading");
  const [report, setReport] = useState<AdminReportDetail>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    if (!reportId) return;
    let cancelled = false;
    adminApi
      .report(reportId)
      .then((result) => {
        if (cancelled) return;
        setReport(result);
        setLoad("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (isNotFound(failure)) {
          setLoad("not-found");
          return;
        }
        if (isMfaRequired(failure)) {
          reverify();
          return;
        }
        setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, [reportId, reverify]);

  /** Re-fetches the full detail after a 409 (someone else just dismissed or suspended this
   * report), leaving the stale view in place if the reload itself fails. */
  async function reloadAfterConflict() {
    if (!reportId) return;
    try {
      setReport(await adminApi.report(reportId));
    } catch {
      // The action's own error message already explains the failure.
    }
  }

  /** Re-fetches after a 404 (the report, or the student it names, is gone): the admin's own stale
   * copy must not linger as if it were still actionable, so a fresh 404 here swaps the whole page
   * to the not-available state rather than just updating one field. */
  async function reloadAfterNotFound() {
    if (!reportId) return;
    try {
      setReport(await adminApi.report(reportId));
      setLoad("ready");
    } catch (failure) {
      if (isNotFound(failure)) setLoad("not-found");
      // Otherwise leave the stale report and error in view; the admin can retry the action.
    }
  }

  async function runAction(action: () => Promise<AdminReport>) {
    setBusy(true);
    setError(undefined);
    try {
      const result = await action();
      // `result` is an AdminReport, a subset of AdminReportDetail: spreading it over the current
      // detail updates status/target/etc. while keeping the already-loaded target_context.
      setReport((current) => (current ? { ...current, ...result } : current));
      setConfirmOpen(false);
    } catch (failure) {
      setConfirmOpen(false);
      if (isMfaRequired(failure)) {
        reverify();
      } else if (isNotFound(failure)) {
        setError("This report isn't available anymore.");
        await reloadAfterNotFound();
      } else {
        setError(errorMessage(failure));
        if (failure instanceof ApiError && failure.status === 409) await reloadAfterConflict();
      }
    } finally {
      setBusy(false);
    }
  }

  function dismiss() {
    if (!reportId) return;
    void runAction(() => adminApi.dismiss(reportId));
  }

  function suspend() {
    if (!reportId) return;
    void runAction(() => adminApi.suspend(reportId));
  }

  if (!reportId || load === "not-found") {
    return <p>This report isn't available.</p>;
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error" || !report) {
    return <p role="alert">Can't load this report right now. Check your connection and try again.</p>;
  }

  const name = targetName(report);

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Report</h1>
      <FormError>{error}</FormError>

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-medium">{REPORT_REASON_LABELS[report.reason]}</span>
        <ReportStatusBadge status={report.status} />
        {report.target.suspended ? <ReportSuspendedBadge /> : null}
      </div>

      <p className="whitespace-pre-line">{report.details}</p>

      <dl className="grid gap-3 text-sm">
        <div>
          <dt className="text-muted-foreground">When</dt>
          <dd>{relativeTime(report.created_at)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Reporter</dt>
          <dd>{reporterName(report)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Target</dt>
          <dd>{name}</dd>
        </div>
        {report.target_context.headline ? (
          <div>
            <dt className="text-muted-foreground">Headline</dt>
            <dd>{report.target_context.headline}</dd>
          </div>
        ) : null}
        <div>
          <dt className="text-muted-foreground">Member since</dt>
          <dd>{formatDate(report.target_context.member_since)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Verified</dt>
          <dd>{report.target_context.verified ? "Yes" : "No"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Reports on this student</dt>
          <dd>
            {report.target_context.open_reports} open of {report.target_context.total_reports} total
          </dd>
        </div>
      </dl>

      {report.status === "open" ? (
        <div className="flex flex-wrap gap-3">
          <Button variant="outline" disabled={busy} onClick={dismiss}>
            Dismiss
          </Button>
          <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
            <AlertDialogTrigger asChild>
              <Button variant="destructive" disabled={busy}>
                Suspend account
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Suspend {name}?</AlertDialogTitle>
                <AlertDialogDescription>They're signed out everywhere and can't sign in again.</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={busy}>Cancel</AlertDialogCancel>
                {/* A plain button, not AlertDialogAction: that primitive always closes the dialog
                    on click, which would hide a 409/403 error. */}
                <Button variant="destructive" disabled={busy} onClick={suspend}>
                  Suspend account
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      ) : null}

      <Link to="/admin" className="self-start text-sm underline-offset-4 hover:underline">
        Back to reports
      </Link>
    </section>
  );
}
