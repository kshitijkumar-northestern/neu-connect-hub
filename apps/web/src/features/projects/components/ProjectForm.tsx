import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { TextField } from "@/features/auth/components/TextField";
import { submitErrors } from "@/features/auth/errors";
import { FormError } from "@/features/auth/components/FormError";
import { CAPACITY_MAX, SUMMARY_MAX, TITLE_MAX, type ProjectInput } from "@/features/projects/api";
import { RoleFields } from "@/features/projects/components/RoleFields";
import {
  PROJECT_FORM_FIELDS,
  hasFormErrors,
  validateProjectForm,
  type ProjectFormErrors,
  type ProjectFormValues,
  type RoleDraft,
} from "@/features/projects/validation";
import type { SkillOption } from "@/features/profiles/api";
import { TextareaField } from "@/features/profiles/components/TextareaField";

/** The create and edit forms share this: title, summary, team size and roles. The caller supplies
 * the starting values, the team-size floor (the current member count in edit, 1 in create) and
 * what to do with a validated submission - it may throw, and the API's 422 renders the same way a
 * client-side mistake does. */
export function ProjectForm({
  initialValues,
  minCapacity = 1,
  skillOptions,
  submitLabel,
  onSubmit,
}: {
  initialValues: ProjectFormValues;
  minCapacity?: number;
  skillOptions: readonly SkillOption[];
  submitLabel: string;
  onSubmit: (input: ProjectInput) => Promise<void>;
}) {
  const [title, setTitle] = useState(initialValues.title);
  const [summary, setSummary] = useState(initialValues.summary);
  const [capacity, setCapacity] = useState(initialValues.capacity);
  const [roles, setRoles] = useState<RoleDraft[]>(initialValues.roles);
  const [errors, setErrors] = useState<ProjectFormErrors>({});
  const [saving, setSaving] = useState(false);

  function buildInput(): ProjectInput {
    return {
      title: title.trim(),
      summary: summary.trim(),
      capacity,
      roles: roles.map((role) => ({
        ...(role.id ? { id: role.id } : {}),
        title: role.title.trim(),
        open_slots: role.open_slots,
        skills: role.skills,
      })),
    };
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const values: ProjectFormValues = { title, summary, capacity, roles };
    const clientErrors = validateProjectForm(values, minCapacity);
    if (hasFormErrors(clientErrors)) {
      setErrors(clientErrors);
      return;
    }
    setErrors({});
    setSaving(true);
    try {
      await onSubmit(buildInput());
    } catch (failure) {
      setErrors(submitErrors(failure, PROJECT_FORM_FIELDS));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={(event) => void handleSubmit(event)} noValidate aria-busy={saving} className="max-w-2xl">
      <FieldGroup>
        <FormError>{errors.form}</FormError>
        <TextField
          label="Title"
          required
          maxLength={TITLE_MAX}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          error={errors.title}
        />
        <TextareaField
          label="Summary (optional)"
          maxLength={SUMMARY_MAX}
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          error={errors.summary}
        />
        <TextField
          label="Team size"
          type="number"
          required
          min={minCapacity}
          max={CAPACITY_MAX}
          value={capacity}
          onChange={(event) => setCapacity(Number(event.target.value))}
          description="Includes you. Count the whole team, not just the teammates you're looking for."
          error={errors.capacity}
        />
        {capacity === 1 ? (
          <p className="text-sm text-muted-foreground">A team of 1 is full straight away, so nobody can apply.</p>
        ) : null}
        <RoleFields roles={roles} onChange={setRoles} skillOptions={skillOptions} errors={errors.roleErrors} />
        {errors.roles ? <p role="alert">{errors.roles}</p> : null}
        <Button type="submit" disabled={saving} className="self-start">
          {submitLabel}
        </Button>
      </FieldGroup>
    </form>
  );
}
