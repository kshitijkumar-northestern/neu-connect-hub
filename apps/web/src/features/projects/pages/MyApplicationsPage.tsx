import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { relativeTime } from "@/features/messages/time";
import { ApplicationStatusBadge } from "@/features/projects/components/ApplicationStatusBadge";
import { projectsApi, type MyApplication } from "@/features/projects/api";

type LoadState = "loading" | "ready" | "error";

function roleLabel(role: MyApplication["role"]): string {
  return role?.title ?? "Any role";
}

function MyApplicationRow({
  application,
  busy,
  onWithdraw,
}: {
  application: MyApplication;
  busy: boolean;
  onWithdraw: (application: MyApplication) => void;
}) {
  return (
    <li className="flex items-center justify-between gap-4 rounded-2xl border px-4 py-3">
      <div className="flex flex-col gap-0.5">
        <Link to={`/projects/${application.project.id}`} className="font-medium underline-offset-4 hover:underline">
          {application.project.title}
        </Link>
        <span className="text-sm text-muted-foreground">{roleLabel(application.role)}</span>
        <span className="text-xs text-muted-foreground">Applied {relativeTime(application.created_at)}</span>
      </div>
      <div className="flex items-center gap-2">
        <ApplicationStatusBadge status={application.status} />
        {application.status === "pending" ? (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            aria-label={`Withdraw application to ${application.project.title}`}
            onClick={() => onWithdraw(application)}
          >
            Withdraw
          </Button>
        ) : null}
      </div>
    </li>
  );
}

/** The signed-in student's own applications, newest first, with a Withdraw action on the ones
 * still pending. Updates come from the server's response, not an optimistic local edit. */
export function MyApplicationsPage() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [applications, setApplications] = useState<MyApplication[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string>();
  const [busyId, setBusyId] = useState<string>();

  useEffect(() => {
    let cancelled = false;
    projectsApi
      .mine()
      .then((page) => {
        if (cancelled) return;
        setApplications(page.items);
        setNextOffset(page.next_offset);
        setLoad("ready");
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadMore() {
    if (nextOffset === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await projectsApi.mine({ offset: nextOffset });
      setApplications((current) => [...current, ...page.items]);
      setNextOffset(page.next_offset);
    } catch {
      // Leave the button in place so the student can try again.
    } finally {
      setLoadingMore(false);
    }
  }

  function withdraw(application: MyApplication) {
    setBusyId(application.id);
    setError(undefined);
    projectsApi
      .withdraw(application.project_id, application.id)
      .then(() => {
        setApplications((current) =>
          current.map((item) => (item.id === application.id ? { ...item, status: "withdrawn" } : item)),
        );
      })
      .catch((failure: unknown) => setError(errorMessage(failure)))
      .finally(() => setBusyId(undefined));
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error") {
    return <p role="alert">Can't load your applications right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">My applications</h1>
      <FormError>{error}</FormError>

      {applications.length ? (
        <ul className="flex flex-col gap-2">
          {applications.map((application) => (
            <MyApplicationRow
              key={application.id}
              application={application}
              busy={busyId === application.id}
              onWithdraw={withdraw}
            />
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">
          You haven't applied to any projects yet.{" "}
          <Link className="underline underline-offset-4" to="/projects">
            Browse projects
          </Link>
        </p>
      )}

      {nextOffset !== null ? (
        <Button variant="outline" size="sm" className="self-start" disabled={loadingMore} onClick={() => void loadMore()}>
          {loadingMore ? "Loading…" : "Load more"}
        </Button>
      ) : null}
    </section>
  );
}
