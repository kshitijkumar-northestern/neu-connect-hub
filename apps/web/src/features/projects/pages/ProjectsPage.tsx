import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PAGE_SIZE, projectsApi, type ProjectSummary } from "@/features/projects/api";
import { ProjectStatusBadge } from "@/features/projects/components/ProjectStatusBadge";
import { placesLeftLabel } from "@/features/projects/format";

type LoadState = "loading" | "ready" | "error";

function ProjectCard({ project, showStatus = false }: { project: ProjectSummary; showStatus?: boolean }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-3">
          <Link to={`/projects/${project.id}`} className="underline-offset-4 hover:underline">
            {project.title}
          </Link>
          {showStatus ? <ProjectStatusBadge status={project.status} /> : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">{project.owner.display_name ?? "A student"}</p>
        {project.summary ? <p className="line-clamp-3 text-sm whitespace-pre-line">{project.summary}</p> : null}
        <p className="text-sm text-muted-foreground">
          {project.member_count} of {project.capacity} on the team
        </p>
        {project.roles.length ? (
          <ul className="flex flex-wrap gap-2">
            {project.roles.map((role) => (
              <li key={role.id} className="rounded-2xl border px-3 py-1 text-xs text-muted-foreground">
                {role.title} · {placesLeftLabel(role.remaining_slots)}
              </li>
            ))}
          </ul>
        ) : null}
      </CardContent>
    </Card>
  );
}

/** Projects the student leads or belongs to, any status - the public list only shows open
 * projects, so without this an owner can't find their own full or closed project. Hidden
 * entirely while empty (or if it fails to load - the public list above still works). */
function MyProjectsSection() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    projectsApi
      .myProjects({ limit: PAGE_SIZE, offset: 0 })
      .then((page) => {
        if (cancelled) return;
        setProjects(page.items);
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
    if (nextOffset === null) return;
    setLoadingMore(true);
    try {
      const page = await projectsApi.myProjects({ limit: PAGE_SIZE, offset: nextOffset });
      setProjects((current) => [...current, ...page.items]);
      setNextOffset(page.next_offset);
    } catch {
      // Keep the projects already shown; the student can try again.
    } finally {
      setLoadingMore(false);
    }
  }

  if (load !== "ready" || !projects.length) return null;

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-medium">Your projects</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        {projects.map((project) => (
          <ProjectCard key={project.id} project={project} showStatus />
        ))}
      </div>
      {nextOffset !== null ? (
        <Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()} className="self-start">
          Load more of your projects
        </Button>
      ) : null}
    </div>
  );
}

/** Open projects looking for teammates, newest first, paged with "Load more". */
export function ProjectsPage() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    projectsApi
      .list({ limit: PAGE_SIZE, offset: 0 })
      .then((page) => {
        if (cancelled) return;
        setProjects(page.items);
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
    if (nextOffset === null) return;
    setLoadingMore(true);
    try {
      const page = await projectsApi.list({ limit: PAGE_SIZE, offset: nextOffset });
      setProjects((current) => [...current, ...page.items]);
      setNextOffset(page.next_offset);
    } catch {
      // Keep the projects already shown; the student can try "Load more" again.
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
    return <p role="alert">Can't load projects right now. Check your connection and try again.</p>;
  }

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
        <div className="flex items-center gap-4">
          <Link className="text-sm underline-offset-4 hover:underline" to="/applications">
            My applications
          </Link>
          <Button asChild>
            <Link to="/projects/new">New project</Link>
          </Button>
        </div>
      </div>

      <MyProjectsSection />

      {projects.length ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {projects.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      ) : (
        <p className="text-muted-foreground">No projects are looking for teammates right now.</p>
      )}

      {nextOffset !== null ? (
        <Button variant="outline" disabled={loadingMore} onClick={() => void loadMore()} className="self-start">
          Load more
        </Button>
      ) : null}
    </section>
  );
}
