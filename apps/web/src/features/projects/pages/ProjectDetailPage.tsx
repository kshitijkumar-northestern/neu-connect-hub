import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/features/auth/AuthProvider";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { projectsApi, type ProjectDetail } from "@/features/projects/api";
import { ProjectStatusBadge } from "@/features/projects/components/ProjectStatusBadge";
import { placesLeftLabel } from "@/features/projects/format";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "not-found" | "error";

const ANY_ROLE = "any";

const APPLICATION_STATUS_LABELS: Record<"rejected" | "withdrawn", string> = {
  rejected: "rejected",
  withdrawn: "withdrawn",
};

function OwnerActions({
  project,
  busy,
  onToggleStatus,
}: {
  project: ProjectDetail;
  busy: boolean;
  onToggleStatus: (status: "open" | "closed") => void;
}) {
  const isOpen = project.status === "open";
  const canReopen = project.member_count < project.capacity;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-3">
        <Button asChild variant="outline">
          <Link to={`/projects/${project.id}/edit`}>Edit project</Link>
        </Button>
        <Button asChild variant="outline">
          <Link to={`/projects/${project.id}/applications`}>Applications</Link>
        </Button>
        {project.conversation_id ? (
          <Button asChild variant="outline">
            <Link to={`/messages/${project.conversation_id}`}>Team chat</Link>
          </Button>
        ) : null}
        <Button
          variant="outline"
          disabled={busy || (!isOpen && !canReopen)}
          onClick={() => onToggleStatus(isOpen ? "closed" : "open")}
        >
          {isOpen ? "Close project" : "Reopen project"}
        </Button>
      </div>
      {!isOpen && !canReopen ? (
        <p className="text-sm text-muted-foreground">Raise the team size to reopen it.</p>
      ) : null}
    </div>
  );
}

/** A project's title, status, summary, roles, missing skills and team, plus whatever the viewer
 * can do about it: lead it, chat with the team, see an application in flight, or apply. */
export function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const { state } = useAuth();
  const viewerId = state.status === "signed-in" ? state.user.id : undefined;

  const [load, setLoad] = useState<LoadState>("loading");
  const [project, setProject] = useState<ProjectDetail>();
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string>();
  const [selectedRoleId, setSelectedRoleId] = useState<string>(ANY_ROLE);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    projectsApi
      .get(projectId)
      .then((result) => {
        if (cancelled) return;
        setProject(result);
        setLoad("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoad(error instanceof ApiError && error.status === 404 ? "not-found" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  async function refreshProject() {
    if (!projectId) return;
    try {
      const result = await projectsApi.get(projectId);
      setProject(result);
    } catch {
      // The action's own error message already explains the failure; leave the view as it is.
    }
  }

  async function runAction(action: () => Promise<void>) {
    setBusy(true);
    setActionError(undefined);
    try {
      await action();
    } catch (failure) {
      setActionError(errorMessage(failure));
      if (failure instanceof ApiError && failure.status === 409) await refreshProject();
    } finally {
      setBusy(false);
    }
  }

  function toggleStatus(status: "open" | "closed") {
    if (!project) return;
    void runAction(async () => {
      const result = await projectsApi.update(project.id, { status });
      setProject(result);
    });
  }

  function apply() {
    if (!project) return;
    void runAction(async () => {
      await projectsApi.apply(project.id, selectedRoleId === ANY_ROLE ? null : selectedRoleId);
      await refreshProject();
    });
  }

  function withdraw(applicationId: string) {
    if (!project) return;
    void runAction(async () => {
      await projectsApi.withdraw(project.id, applicationId);
      await refreshProject();
    });
  }

  if (!projectId) {
    return <p>This project isn't available.</p>;
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "not-found") {
    return <p>This project isn't available.</p>;
  }

  if (load === "error" || !project) {
    return <p role="alert">Can't load this project right now. Check your connection and try again.</p>;
  }

  const isOwner = project.owner.user_id === viewerId;
  const isMember = !isOwner && Boolean(project.conversation_id);
  const viewerApplication = project.viewer_application;
  const openRoles = project.roles.filter((role) => role.remaining_slots > 0);

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">{project.title}</h1>
          <ProjectStatusBadge status={project.status} />
        </div>
        <p className="text-sm text-muted-foreground">Led by {project.owner.display_name ?? "A student"}</p>
      </div>

      {project.summary ? <p className="whitespace-pre-line">{project.summary}</p> : null}

      <p className="text-sm text-muted-foreground">
        {project.member_count} of {project.capacity} on the team
      </p>

      {project.roles.length ? (
        <div>
          <h2 className="text-sm font-medium">Roles</h2>
          <ul className="mt-2 flex flex-col gap-2">
            {project.roles.map((role) => (
              <li key={role.id} className="rounded-2xl border px-4 py-3">
                <div className="flex items-center justify-between gap-4">
                  <span className="font-medium">{role.title}</span>
                  <span className="text-sm text-muted-foreground">{placesLeftLabel(role.remaining_slots)}</span>
                </div>
                {role.skills.length ? (
                  <ul className="mt-2 flex flex-wrap gap-2">
                    {role.skills.map((skill) => (
                      <li key={skill.slug}>
                        <Badge variant="outline">{skill.name}</Badge>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {project.missing_skills.length ? (
        <div>
          <h2 className="text-sm font-medium">Skills the team still needs</h2>
          <ul className="mt-2 flex flex-wrap gap-2">
            {project.missing_skills.map((skill) => (
              <li key={skill.slug}>
                <Badge variant="outline">{skill.name}</Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <h2 className="text-sm font-medium">Team</h2>
        <ul className="mt-2 flex flex-col gap-2">
          {project.members.map((member) => (
            <li key={member.user_id} className="flex items-center justify-between gap-4 rounded-2xl border px-4 py-2">
              <Link to={`/people/${member.user_id}`} className="font-medium underline-offset-4 hover:underline">
                {member.display_name ?? "A student"}
              </Link>
              <span className="text-sm text-muted-foreground">
                {member.member_role === "owner" ? "Lead" : (member.role?.title ?? "Member")}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-3">
        {isOwner ? (
          <>
            <FormError>{actionError}</FormError>
            <OwnerActions project={project} busy={busy} onToggleStatus={toggleStatus} />
          </>
        ) : isMember ? (
          <div className="flex flex-wrap items-center gap-3">
            <p role="status" className="text-sm text-muted-foreground">
              You're on this team
            </p>
            {project.conversation_id ? (
              <Button asChild variant="outline">
                <Link to={`/messages/${project.conversation_id}`}>Team chat</Link>
              </Button>
            ) : null}
          </div>
        ) : viewerApplication?.status === "pending" ? (
          <div className="flex flex-col gap-3">
            <p role="status" className="text-sm text-muted-foreground">
              Application sent
              {viewerApplication.role_id
                ? ` for ${project.roles.find((role) => role.id === viewerApplication.role_id)?.title ?? "a role"}`
                : ""}
            </p>
            <FormError>{actionError}</FormError>
            <Button variant="outline" disabled={busy} onClick={() => withdraw(viewerApplication.id)} className="self-start">
              Withdraw application
            </Button>
          </div>
        ) : project.status === "open" ? (
          <div className="flex flex-col gap-3">
            {viewerApplication && (viewerApplication.status === "rejected" || viewerApplication.status === "withdrawn") ? (
              <p className="text-sm text-muted-foreground">
                Your application was {APPLICATION_STATUS_LABELS[viewerApplication.status]}.
              </p>
            ) : null}
            <FormError>{actionError}</FormError>
            <div className="flex flex-wrap items-end gap-3">
              <Field className="w-48">
                <FieldLabel htmlFor="apply-role">Role</FieldLabel>
                <Select value={selectedRoleId} onValueChange={setSelectedRoleId}>
                  <SelectTrigger id="apply-role" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY_ROLE}>Any role</SelectItem>
                    {openRoles.map((role) => (
                      <SelectItem key={role.id} value={role.id}>
                        {role.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Button disabled={busy} onClick={() => apply()}>
                {viewerApplication ? "Apply again" : "Apply"}
              </Button>
            </div>
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            {project.status === "full" ? "This team is full." : "This project isn't taking new members."}
          </p>
        )}
      </div>
    </section>
  );
}
