import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { Button } from "@/components/ui/button";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { relativeTime } from "@/features/messages/time";
import { ApplicationStatusBadge } from "@/features/projects/components/ApplicationStatusBadge";
import { projectsApi, type Application, type ApplicationStatus, type ProjectDetail } from "@/features/projects/api";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "forbidden" | "not-found" | "error";

// Every status besides "pending", fetched together for the Decided section.
const DECIDED_STATUSES: readonly ApplicationStatus[] = ["accepted", "rejected", "withdrawn"];

function roleLabel(role: Application["role"]): string {
  return role?.title ?? "Any role";
}

function placesLeft(remaining: number): string {
  return remaining === 1 ? "1 place left" : `${remaining} places left`;
}

/** Appends `incoming` rows not already present by id. Accept/reject can shift a server page's
 * rows forward between one "Load more" click and the next (the owner's own decision, or another
 * owner session), so a row already on screen can reappear in the next page. */
function mergeById(current: Application[], incoming: Application[]): Application[] {
  const ids = new Set(current.map((item) => item.id));
  return [...current, ...incoming.filter((item) => !ids.has(item.id))];
}

/** Puts `item` first, dropping any earlier row with the same id (a fresh decision replaces a
 * stale copy of the same application rather than appearing twice). */
function prependById(current: Application[], item: Application): Application[] {
  return [item, ...current.filter((existing) => existing.id !== item.id)];
}

function ApplicationRow({
  application,
  busy,
  onAccept,
  onReject,
}: {
  application: Application;
  busy?: boolean;
  onAccept?: (application: Application) => void;
  onReject?: (application: Application) => void;
}) {
  const name = application.applicant.display_name ?? "A student";
  return (
    <li className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border px-4 py-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        <Link
          to={`/people/${application.applicant.user_id}`}
          className="font-medium break-words underline-offset-4 hover:underline"
        >
          {name}
        </Link>
        <span className="text-sm text-muted-foreground">{roleLabel(application.role)}</span>
        <span className="text-xs text-muted-foreground">Applied {relativeTime(application.created_at)}</span>
      </div>
      {onAccept && onReject ? (
        <div className="flex gap-2">
          <Button size="sm" disabled={busy} aria-label={`Accept ${name}`} onClick={() => onAccept(application)}>
            Accept
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            aria-label={`Reject ${name}`}
            onClick={() => onReject(application)}
          >
            Reject
          </Button>
        </div>
      ) : (
        <ApplicationStatusBadge status={application.status} />
      )}
    </li>
  );
}

/** The owner's review queue: pending applications with Accept/Reject, and a Decided section
 * below. Accept/reject update the row in place; a 409 (someone else just filled that place)
 * reloads the project header and the queue so the counts stay true. */
export function ProjectApplicationsPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [load, setLoad] = useState<LoadState>("loading");
  const [project, setProject] = useState<ProjectDetail>();
  const [pending, setPending] = useState<Application[]>([]);
  const [pendingNextOffset, setPendingNextOffset] = useState<number | null>(null);
  const [loadingMorePending, setLoadingMorePending] = useState(false);
  const [decided, setDecided] = useState<Application[]>([]);
  const [decidedNextOffset, setDecidedNextOffset] = useState<number | null>(null);
  const [loadingMoreDecided, setLoadingMoreDecided] = useState(false);
  const [error, setError] = useState<string>();
  const [busyId, setBusyId] = useState<string>();

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    Promise.all([
      projectsApi.get(projectId),
      projectsApi.applications(projectId, { status: "pending" }),
      projectsApi.applications(projectId, { status: DECIDED_STATUSES }),
    ])
      .then(([projectResult, pendingResult, decidedResult]) => {
        if (cancelled) return;
        setProject(projectResult);
        setPending(pendingResult.items);
        setPendingNextOffset(pendingResult.next_offset);
        setDecided(decidedResult.items);
        setDecidedNextOffset(decidedResult.next_offset);
        setLoad("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (failure instanceof ApiError && failure.status === 403) setLoad("forbidden");
        else if (failure instanceof ApiError && failure.status === 404) setLoad("not-found");
        else setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  /** Re-fetches the project header and the pending queue after a 409, leaving the current rows
   * in place if the reload itself fails. */
  async function reloadAfterConflict() {
    if (!projectId) return;
    try {
      const [projectResult, pendingResult] = await Promise.all([
        projectsApi.get(projectId),
        projectsApi.applications(projectId, { status: "pending" }),
      ]);
      setProject(projectResult);
      setPending(pendingResult.items);
      setPendingNextOffset(pendingResult.next_offset);
    } catch {
      // The alert from the action that triggered this already explains the failure.
    }
  }

  /** Re-fetches just the project header after a successful accept, so "N of M members" and each
   * role's places-left reflect the new member right away. A reject never changes either count. */
  async function reloadProjectHeader() {
    if (!projectId) return;
    try {
      setProject(await projectsApi.get(projectId));
    } catch {
      // Leave the stale header in place rather than replacing it with an error state.
    }
  }

  async function runAction(
    application: Application,
    action: () => Promise<Application>,
    { refreshHeaderOnSuccess = false }: { refreshHeaderOnSuccess?: boolean } = {},
  ) {
    setBusyId(application.id);
    setError(undefined);
    try {
      const updated = await action();
      setPending((current) => current.filter((item) => item.id !== application.id));
      setDecided((current) => prependById(current, updated));
      if (refreshHeaderOnSuccess) await reloadProjectHeader();
    } catch (failure) {
      setError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status === 409) await reloadAfterConflict();
    } finally {
      setBusyId(undefined);
    }
  }

  function accept(application: Application) {
    if (!projectId) return;
    void runAction(application, () => projectsApi.accept(projectId, application.id), { refreshHeaderOnSuccess: true });
  }

  function reject(application: Application) {
    if (!projectId) return;
    void runAction(application, () => projectsApi.reject(projectId, application.id));
  }

  /** Pages from the count of pending rows still on screen, not the server's original
   * `next_offset`: deciding a row removes it from `pending`, which shifts every later row
   * forward, so paging from the stale offset would skip exactly as many rows as were decided. */
  async function loadMorePending() {
    if (!projectId || pendingNextOffset === null || loadingMorePending) return;
    setLoadingMorePending(true);
    try {
      const page = await projectsApi.applications(projectId, { status: "pending", offset: pending.length });
      setPending((current) => mergeById(current, page.items));
      setPendingNextOffset(page.next_offset);
    } catch {
      // Leave the button in place so the owner can try again.
    } finally {
      setLoadingMorePending(false);
    }
  }

  /** Same offset-from-length approach as `loadMorePending`: a row decided this session is
   * prepended locally (see `runAction`), which would otherwise throw off a server-offset page. */
  async function loadMoreDecided() {
    if (!projectId || decidedNextOffset === null || loadingMoreDecided) return;
    setLoadingMoreDecided(true);
    try {
      const page = await projectsApi.applications(projectId, { status: DECIDED_STATUSES, offset: decided.length });
      setDecided((current) => mergeById(current, page.items));
      setDecidedNextOffset(page.next_offset);
    } catch {
      // Leave the button in place so the owner can try again.
    } finally {
      setLoadingMoreDecided(false);
    }
  }

  if (!projectId || load === "not-found") {
    return <p>This project isn't available.</p>;
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "forbidden") {
    return <p>Only the project's owner can see its applications.</p>;
  }

  if (load === "error" || !project) {
    return <p role="alert">Can't load this project's applications right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-8">
      <h1 className="text-2xl font-semibold tracking-tight">Applications</h1>
      <FormError>{error}</FormError>

      <header className="flex flex-col gap-2">
        <Link to={`/projects/${project.id}`} className="text-lg font-medium underline-offset-4 hover:underline">
          {project.title}
        </Link>
        <p className="text-sm text-muted-foreground">
          {project.member_count} of {project.capacity} members
        </p>
        {project.roles.length ? (
          <ul className="flex flex-wrap gap-2">
            {project.roles.map((role) => (
              <li key={role.id} className="rounded-2xl border px-3 py-1 text-sm text-muted-foreground">
                {role.title}: {placesLeft(role.remaining_slots)}
              </li>
            ))}
          </ul>
        ) : null}
      </header>

      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Pending</h2>
        {pending.length ? (
          <ul className="flex flex-col gap-2">
            {pending.map((application) => (
              <ApplicationRow
                key={application.id}
                application={application}
                busy={busyId === application.id}
                onAccept={accept}
                onReject={reject}
              />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">No applications waiting.</p>
        )}
        {pendingNextOffset !== null ? (
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            disabled={loadingMorePending}
            onClick={() => void loadMorePending()}
          >
            {loadingMorePending ? "Loading…" : "Load more"}
          </Button>
        ) : null}
      </div>

      <div className="flex flex-col gap-4">
        <h2 className="text-lg font-medium">Decided</h2>
        {decided.length ? (
          <ul className="flex flex-col gap-2">
            {decided.map((application) => (
              <ApplicationRow key={application.id} application={application} />
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground">No decisions yet.</p>
        )}
        {decidedNextOffset !== null ? (
          <Button
            variant="outline"
            size="sm"
            className="self-start"
            aria-label="Load more decided applications"
            disabled={loadingMoreDecided}
            onClick={() => void loadMoreDecided()}
          >
            {loadingMoreDecided ? "Loading…" : "Load more"}
          </Button>
        ) : null}
      </div>
    </section>
  );
}
