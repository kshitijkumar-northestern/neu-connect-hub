import {
  CAPACITY_MAX,
  MAX_ROLES,
  MAX_ROLE_SKILLS,
  OPEN_SLOTS_MAX,
  ROLE_TITLE_MAX,
  SUMMARY_MAX,
  TITLE_MAX,
} from "@/features/projects/api";

/** A role as the create/edit form edits it. */
export interface RoleDraft {
  /** Present when editing an existing role; omitted for one the student just added. */
  id?: string;
  title: string;
  open_slots: number;
  /** Required skill slugs. */
  skills: string[];
  /** Members currently holding this role (edit only; 0 for a brand new role). The form can't
   * drop `open_slots` below this, or remove the role while it's positive. */
  holders: number;
}

export interface ProjectFormValues {
  title: string;
  summary: string;
  capacity: number;
  roles: RoleDraft[];
}

export interface RoleFormErrors {
  title?: string;
  open_slots?: string;
  skills?: string;
}

export interface ProjectFormErrors {
  title?: string;
  summary?: string;
  capacity?: string;
  /** A problem with the roles list as a whole (e.g. too many roles). */
  roles?: string;
  /** Per-role errors, aligned by index with the roles array. */
  roleErrors?: RoleFormErrors[];
  form?: string;
}

/** The top-level fields the API reports with their own name, matching how submitErrors()
 * maps a 422's field locations back onto this form. */
export const PROJECT_FORM_FIELDS = ["title", "summary", "capacity"] as const;

const MULTILINE = /[\r\n]/;

/** Client-side checks mirroring the API's own validation (apps/api/app/modules/teams/schemas.py).
 * The server still re-validates and remains the source of truth. */
export function validateProjectForm(values: ProjectFormValues, minCapacity = 1): ProjectFormErrors {
  const errors: ProjectFormErrors = {};

  const title = values.title.trim();
  if (!title) errors.title = "Enter a title.";
  else if (MULTILINE.test(values.title)) errors.title = "The title must fit on one line.";
  else if (title.length > TITLE_MAX) errors.title = `Use at most ${TITLE_MAX} characters.`;

  if (values.summary.length > SUMMARY_MAX) errors.summary = `Use at most ${SUMMARY_MAX} characters.`;

  if (!Number.isInteger(values.capacity) || values.capacity < 1 || values.capacity > CAPACITY_MAX) {
    errors.capacity = `Choose a team size from 1 to ${CAPACITY_MAX}.`;
  } else if (values.capacity < minCapacity) {
    errors.capacity = "The team size can't be smaller than the team already is.";
  }

  if (values.roles.length > MAX_ROLES) errors.roles = `Use at most ${MAX_ROLES} roles.`;

  const roleErrors: RoleFormErrors[] = values.roles.map((role) => {
    const roleError: RoleFormErrors = {};
    const roleTitle = role.title.trim();
    if (!roleTitle) roleError.title = "Enter a title.";
    else if (MULTILINE.test(role.title)) roleError.title = "The title must fit on one line.";
    else if (roleTitle.length > ROLE_TITLE_MAX) roleError.title = `Use at most ${ROLE_TITLE_MAX} characters.`;

    if (!Number.isInteger(role.open_slots) || role.open_slots < 1 || role.open_slots > OPEN_SLOTS_MAX) {
      roleError.open_slots = `Choose places from 1 to ${OPEN_SLOTS_MAX}.`;
    } else if (role.open_slots < role.holders) {
      roleError.open_slots = "This role can't have fewer places than the students already in it.";
    }

    if (role.skills.length > MAX_ROLE_SKILLS) roleError.skills = `Choose at most ${MAX_ROLE_SKILLS} skills.`;

    return roleError;
  });
  if (roleErrors.some((roleError) => Object.keys(roleError).length)) errors.roleErrors = roleErrors;

  return errors;
}

export function hasFormErrors(errors: ProjectFormErrors): boolean {
  return Boolean(
    errors.title ||
      errors.summary ||
      errors.capacity ||
      errors.roles ||
      errors.roleErrors?.some((roleError) => Object.keys(roleError).length),
  );
}
