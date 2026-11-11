import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import { profilesApi, type SkillOption } from "@/features/profiles/api";
import { projectsApi } from "@/features/projects/api";
import { ProjectForm } from "@/features/projects/components/ProjectForm";

type LoadState = "loading" | "ready" | "error";

/** Start a project: you lead it, and a team chat is created for it right away. */
export function NewProjectPage() {
  const navigate = useNavigate();
  const [load, setLoad] = useState<LoadState>("loading");
  const [skillOptions, setSkillOptions] = useState<SkillOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    profilesApi
      .skills()
      .then((skills) => {
        if (cancelled) return;
        setSkillOptions(skills);
        setLoad("ready");
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "error") {
    return <p role="alert">Can't load the skills list right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">New project</h1>
      <ProjectForm
        initialValues={{ title: "", summary: "", capacity: 2, roles: [] }}
        skillOptions={skillOptions}
        submitLabel="Create project"
        onSubmit={async (input) => {
          const project = await projectsApi.create(input);
          navigate(`/projects/${project.id}`);
        }}
      />
    </section>
  );
}
