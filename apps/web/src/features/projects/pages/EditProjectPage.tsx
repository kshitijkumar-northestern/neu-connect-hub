import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { useAuth } from "@/features/auth/AuthProvider";
import { profilesApi, type SkillOption } from "@/features/profiles/api";
import { projectsApi, type ProjectDetail } from "@/features/projects/api";
import { ProjectForm } from "@/features/projects/components/ProjectForm";
import type { ProjectFormValues } from "@/features/projects/validation";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "not-found" | "forbidden" | "error";

function toFormValues(project: ProjectDetail): ProjectFormValues {
  return {
    title: project.title,
    summary: project.summary,
    capacity: project.capacity,
    roles: project.roles.map((role) => ({
      id: role.id,
      title: role.title,
      open_slots: role.open_slots,
      skills: role.skills.map((skill) => skill.slug),
      holders: role.open_slots - role.remaining_slots,
    })),
  };
}

/** The owner edits their project's title, summary, team size and roles. Anyone else is turned
 * away before the form ever renders. */
export function EditProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const { state } = useAuth();
  const viewerId = state.status === "signed-in" ? state.user.id : undefined;

  const [load, setLoad] = useState<LoadState>("loading");
  const [project, setProject] = useState<ProjectDetail>();
  const [skillOptions, setSkillOptions] = useState<SkillOption[]>([]);

  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    Promise.all([projectsApi.get(projectId), profilesApi.skills()])
      .then(([result, skills]) => {
        if (cancelled) return;
        setSkillOptions(skills);
        if (result.owner.user_id !== viewerId) {
          setLoad("forbidden");
          return;
        }
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
  }, [projectId, viewerId]);

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

  if (load === "forbidden") {
    return <p role="alert">Only the project's owner can edit it.</p>;
  }

  if (load === "error" || !project) {
    return <p role="alert">Can't load this project right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Edit project</h1>
      <ProjectForm
        initialValues={toFormValues(project)}
        minCapacity={project.member_count}
        skillOptions={skillOptions}
        submitLabel="Save changes"
        onSubmit={async (input) => {
          await projectsApi.update(project.id, {
            title: input.title,
            summary: input.summary,
            capacity: input.capacity,
            roles: input.roles,
          });
          navigate(`/projects/${project.id}`);
        }}
      />
    </section>
  );
}
