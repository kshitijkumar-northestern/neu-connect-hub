import { Button } from "@/components/ui/button";
import { FieldDescription, FieldLegend, FieldSet } from "@/components/ui/field";
import { MAX_ROLES, OPEN_SLOTS_MAX, ROLE_TITLE_MAX } from "@/features/projects/api";
import { SkillField } from "@/features/profiles/components/SkillField";
import { TextField } from "@/features/auth/components/TextField";
import type { SkillOption } from "@/features/profiles/api";
import type { RoleDraft, RoleFormErrors } from "@/features/projects/validation";

/** The roles editor shared by the create and edit forms: 0-10 roles, each with a title, a number
 * of places and required skills (no levels - a role just needs a skill or it doesn't). A role
 * someone already holds can't be removed, and its places can't drop below its holders. */
export function RoleFields({
  roles,
  onChange,
  skillOptions,
  errors,
}: {
  roles: readonly RoleDraft[];
  onChange: (next: RoleDraft[]) => void;
  skillOptions: readonly SkillOption[];
  errors?: readonly RoleFormErrors[];
}) {
  function update(index: number, changes: Partial<RoleDraft>) {
    onChange(roles.map((role, candidate) => (candidate === index ? { ...role, ...changes } : role)));
  }

  function remove(index: number) {
    onChange(roles.filter((_, candidate) => candidate !== index));
  }

  function add() {
    onChange([...roles, { title: "", open_slots: 1, skills: [], holders: 0 }]);
  }

  return (
    <FieldSet>
      <FieldLegend variant="label">Roles (optional)</FieldLegend>
      <FieldDescription>Add a role for each kind of teammate you're looking for.</FieldDescription>
      <div className="flex flex-col gap-4">
        {roles.map((role, index) => {
          const roleError = errors?.[index];
          const label = `Role ${index + 1}`;
          return (
            <div key={role.id ?? `new-${index}`} className="flex flex-col gap-4 rounded-2xl border p-4">
              <div className="flex items-start justify-between gap-4">
                <TextField
                  label={`${label} title`}
                  maxLength={ROLE_TITLE_MAX}
                  value={role.title}
                  onChange={(event) => update(index, { title: event.target.value })}
                  error={roleError?.title}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="mt-6"
                  disabled={role.holders > 0}
                  aria-label={`Remove ${label.toLowerCase()}`}
                  onClick={() => remove(index)}
                >
                  Remove
                </Button>
              </div>
              {role.holders > 0 ? (
                <FieldDescription>A role with members already in it can't be removed.</FieldDescription>
              ) : null}
              <TextField
                label={`${label} places`}
                type="number"
                min={Math.max(1, role.holders)}
                max={OPEN_SLOTS_MAX}
                value={role.open_slots}
                onChange={(event) => update(index, { open_slots: Number(event.target.value) })}
                error={roleError?.open_slots}
              />
              <SkillField
                legend={`${label} required skills`}
                options={skillOptions}
                selected={role.skills.map((slug) => ({ slug }))}
                withLevel={false}
                onChange={(choices) => update(index, { skills: choices.map((choice) => choice.slug) })}
                error={roleError?.skills}
              />
            </div>
          );
        })}
      </div>
      <Button type="button" variant="outline" onClick={add} disabled={roles.length >= MAX_ROLES} className="self-start">
        Add role
      </Button>
    </FieldSet>
  );
}
