import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { useAdminGate } from "@/features/admin/components/AdminGate";
import { ReportSuspendedBadge } from "@/features/admin/components/ReportSuspendedBadge";
import { adminApi, type AdminReport, type ReportStatus } from "@/features/admin/api";
import { isMfaRequired, reporterName, targetName } from "@/features/admin/people";
import { relativeTime } from "@/features/messages/time";
import { REPORT_REASON_LABELS } from "@/features/profiles/constants";

type LoadState = "loading" | "ready" | "error";

const FILTERS: ReadonlyArray<{ value: ReportStatus; label: string }> = [
  { value: "open", label: "Open" },
  { value: "resolved", label: "Resolved" },
  { value: "dismissed", label: "Dismissed" },
];

// Every ReportStatus the API can send, mapped to what an admin sees when a filter has nothing in
// it (never a naive title-case of the raw enum).
const EMPTY_MESSAGES: Record<ReportStatus, string> = {
  open: "No open reports.",
  resolved: "No resolved reports.",
  dismissed: "No dismissed reports.",
};

/** Appends `incoming` rows not already present by id: a dismiss or suspend elsewhere between one
 * "Load more" click and the next can shift a server page's rows forward, so a row already on
 * screen can reappear in the next page. */
function mergeById(current: AdminReport[], incoming: AdminReport[]): AdminReport[] {
  const ids = new Set(current.map((item) => item.id));
  return [...current, ...incoming.filter((item) => !ids.has(item.id))];
}

function ReportRow({ report }: { report: AdminReport }) {
  return (
    <li className="rounded-2xl border px-4 py-3">
      <Link to={`/admin/reports/${report.id}`} className="flex flex-col gap-1">
        <span className="flex flex-wrap items-center gap-2 font-medium">
          {REPORT_REASON_LABELS[report.reason]}
          {report.target.suspended ? <ReportSuspendedBadge /> : null}
        </span>
        <span className="text-sm text-muted-foreground">
          {targetName(report)} reported by {reporterName(report)}
        </span>
        <span className="text-xs text-muted-foreground">{relativeTime(report.created_at)}</span>
      </Link>
    </li>
  );
}

/** One filter's rows, keyed by `filter` from the parent so switching tabs remounts this instead
 * of needing to reset its load state imperatively (the same trick the message thread uses when
 * its route param changes). */
function ReportsList({ filter }: { filter: ReportStatus }) {
  const { reverify } = useAdminGate();
  const [load, setLoad] = useState<LoadState>("loading");
  const [items, setItems] = useState<AdminReport[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    adminApi
      .reports({ status: filter })
      .then((page) => {
        if (cancelled) return;
        setItems(page.items);
        setNextOffset(page.next_offset);
        setLoad("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (isMfaRequired(failure)) {
          reverify();
          return;
        }
        setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, [filter, reverify]);

  async function loadMore() {
    if (nextOffset === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await adminApi.reports({ status: filter, offset: items.length });
      setItems((current) => mergeById(current, page.items));
      setNextOffset(page.next_offset);
    } catch (failure) {
      if (isMfaRequired(failure)) reverify();
      // Otherwise leave the button in place so the admin can try again.
    } finally {
      setLoadingMore(false);
    }
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error") {
    return <p role="alert">Can't load reports right now. Check your connection and try again.</p>;
  }

  if (!items.length) {
    return <p className="text-muted-foreground">{EMPTY_MESSAGES[filter]}</p>;
  }

  return (
    <>
      <ul className="flex flex-col gap-2">
        {items.map((report) => (
          <ReportRow key={report.id} report={report} />
        ))}
      </ul>

      {nextOffset !== null ? (
        <Button variant="outline" size="sm" className="self-start" disabled={loadingMore} onClick={() => void loadMore()}>
          {loadingMore ? "Loading…" : "Load more"}
        </Button>
      ) : null}
    </>
  );
}

/** The admin's report queue: an Open/Resolved/Dismissed filter (tabs, single-select), rows that
 * link to the detail, and paging from the rows still shown rather than the server's original
 * `next_offset` (see the applications queue for why that distinction matters once rows move). */
export function ReportsPage() {
  const [filter, setFilter] = useState<ReportStatus>("open");

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Reports</h1>

      <div role="tablist" aria-label="Filter reports" className="flex gap-2">
        {FILTERS.map((option) => (
          <Button
            key={option.value}
            type="button"
            role="tab"
            size="sm"
            variant={filter === option.value ? "default" : "outline"}
            aria-selected={filter === option.value}
            onClick={() => setFilter(option.value)}
          >
            {option.label}
          </Button>
        ))}
      </div>

      <ReportsList key={filter} filter={filter} />
    </section>
  );
}
