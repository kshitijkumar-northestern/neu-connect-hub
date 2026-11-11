import { apiFetch } from "@/lib/http";
import type { NamedItem } from "@/features/profiles/api";

// Mirrors the Literal unions in apps/api/app/modules/teams/schemas.py.
export type ProjectStatus = "open" | "full" | "closed";
export type MemberRole = "owner" | "member";
export type ApplicationStatus = "pending" | "accepted" | "rejected" | "withdrawn";

// Mirrors the limits in apps/api/app/modules/teams/models.py.
export const TITLE_MAX = 140;
export const SUMMARY_MAX = 2000;
export const ROLE_TITLE_MAX = 80;
export const CAPACITY_MAX = 50;
export const OPEN_SLOTS_MAX = 50;
export const MAX_ROLES = 10;
export const MAX_ROLE_SKILLS = 10;
export const PAGE_SIZE = 20;

export interface Person {
  user_id: string;
  /** null when the student never created a profile. */
  display_name: string | null;
}

export interface RoleRef {
  id: string;
  title: string;
}

export interface RoleSummary extends RoleRef {
  open_slots: number;
  /** open_slots minus the members holding the role, never below 0. */
  remaining_slots: number;
}

export interface RoleDetail extends RoleSummary {
  skills: NamedItem[];
}

export interface ProjectSummary {
  id: string;
  title: string;
  summary: string;
  /** The team size, including the owner. */
  capacity: number;
  status: ProjectStatus;
  member_count: number;
  owner: Person;
  roles: RoleSummary[];
  created_at: string;
}

export interface ProjectMember extends Person {
  member_role: MemberRole;
  role: RoleRef | null;
  joined_at: string;
}

export interface ViewerApplication {
  id: string;
  status: ApplicationStatus;
  role_id: string | null;
}

export interface ProjectDetail {
  id: string;
  title: string;
  summary: string;
  capacity: number;
  status: ProjectStatus;
  member_count: number;
  created_at: string;
  updated_at: string;
  owner: Person;
  roles: RoleDetail[];
  /** Owner first. Students blocked with the viewer are left out but still counted. */
  members: ProjectMember[];
  /** Skills required by roles with places left that no member has yet. */
  missing_skills: NamedItem[];
  /** The team chat; only members get it. */
  conversation_id: string | null;
  /** The viewer's newest application to this project, if any. */
  viewer_application: ViewerApplication | null;
}

export interface Application {
  id: string;
  project_id: string;
  applicant: Person;
  role: RoleRef | null;
  status: ApplicationStatus;
  created_at: string;
  decided_at: string | null;
}

export interface MyApplication extends Application {
  project: { id: string; title: string };
}

export interface Page<T> {
  items: T[];
  /** Pass as `offset` for the next page; null when there is none. */
  next_offset: number | null;
}

export interface RoleInput {
  /** Present when editing an existing role; omitted for a new one. */
  id?: string;
  title: string;
  open_slots: number;
  /** Skill slugs from GET /skills. */
  skills: string[];
}

export interface ProjectInput {
  title: string;
  summary: string;
  capacity: number;
  roles: RoleInput[];
}

/** PATCH body: any subset. `roles` is the full list; a role left out is removed. */
export interface ProjectUpdate {
  title?: string;
  summary?: string;
  capacity?: number;
  status?: "open" | "closed";
  roles?: RoleInput[];
}

export interface PageQuery {
  limit?: number;
  offset?: number;
}

function query(params: Record<string, string | number | readonly string[] | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    // Repeated keys (status=accepted&status=rejected) select several values.
    if (typeof value === "object") for (const item of value) search.append(key, item);
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function projectPath(projectId: string, rest = ""): string {
  return `/projects/${encodeURIComponent(projectId)}${rest}`;
}

function applicationPath(projectId: string, applicationId: string, action: string): string {
  return projectPath(projectId, `/applications/${encodeURIComponent(applicationId)}/${action}`);
}

export const projectsApi = {
  /** Open projects, newest first. */
  list: ({ limit, offset }: PageQuery = {}) => apiFetch<Page<ProjectSummary>>(`/projects${query({ limit, offset })}`),
  get: (projectId: string) => apiFetch<ProjectDetail>(projectPath(projectId)),
  create: (input: ProjectInput) => apiFetch<ProjectDetail>("/projects", { method: "POST", json: input }),
  update: (projectId: string, changes: ProjectUpdate) =>
    apiFetch<ProjectDetail>(projectPath(projectId), { method: "PATCH", json: changes }),
  /** Applying again while one is pending returns that same application. */
  apply: (projectId: string, roleId: string | null) =>
    apiFetch<Application>(projectPath(projectId, "/apply"), { method: "POST", json: { role_id: roleId } }),
  /** Owner only. Newest first. `status` narrows to one or more statuses. */
  applications: (
    projectId: string,
    { status, limit, offset }: PageQuery & { status?: ApplicationStatus | readonly ApplicationStatus[] } = {},
  ) =>
    apiFetch<Page<Application>>(projectPath(projectId, `/applications${query({ status, limit, offset })}`)),
  accept: (projectId: string, applicationId: string) =>
    apiFetch<Application>(applicationPath(projectId, applicationId, "accept"), { method: "POST" }),
  reject: (projectId: string, applicationId: string) =>
    apiFetch<Application>(applicationPath(projectId, applicationId, "reject"), { method: "POST" }),
  /** Applicant only. */
  withdraw: (projectId: string, applicationId: string) =>
    apiFetch<void>(applicationPath(projectId, applicationId, "withdraw"), { method: "POST" }),
  /** Projects the signed-in student leads or is a member of, whatever their status, newest first. */
  myProjects: ({ limit, offset }: PageQuery = {}) =>
    apiFetch<Page<ProjectSummary>>(`/me/projects${query({ limit, offset })}`),
  /** The signed-in student's own applications, newest first. */
  mine: ({ limit, offset }: PageQuery = {}) =>
    apiFetch<Page<MyApplication>>(`/me/applications${query({ limit, offset })}`),
};
