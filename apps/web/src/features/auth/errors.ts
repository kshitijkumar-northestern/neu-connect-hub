import { ApiError } from "@/lib/http";

// What students see for each API error code (codes from apps/api/app/modules/auth/service.py).
// A Map, so a code such as "constructor" can't pick up an Object.prototype member.
const MESSAGES = new Map<string, string>([
  ["invalid_credentials", "That email and password don't match."],
  ["email_domain_not_allowed", "Use your university email address."],
  ["invalid_token", "This link is invalid or has expired."],
  ["rate_limited", "Too many attempts. Wait a few minutes and try again."],
  ["account_suspended", "This account is suspended."],
  ["origin_not_allowed", "Your session check failed. Refresh the page and try again."],
  ["csrf_failed", "Your session check failed. Refresh the page and try again."],
  ["network_error", "Can't reach NU Connect Hub. Check your connection and try again."],
  // Codes from apps/api/app/modules/profiles/service.py and trust.py.
  ["unknown_skill", "One of the skills isn't on the list. Refresh the page and try again."],
  ["unknown_interest", "One of the interests isn't on the list. Refresh the page and try again."],
  ["cannot_target_self", "You can't do that to your own account."],
  ["not_found", "That account couldn't be found."],
  // From apps/api/app/modules/auth/service.py's delete_account.
  ["password_incorrect", "That password isn't right."],
  // Codes from apps/api/app/modules/connections/service.py.
  ["connection_exists", "You already have a request or connection with this student."],
  ["connection_closed", "This request is no longer open."],
  // Codes from apps/api/app/modules/teams/service.py.
  ["unknown_role", "That role isn't part of this project anymore. Refresh the page and try again."],
  ["cannot_apply_to_own_project", "You lead this project, so you can't apply to it."],
  ["already_a_member", "You're already on this team."],
  ["project_closed", "This project isn't taking new members."],
  ["project_full", "This team is full."],
  ["role_full", "That place was just filled."],
  ["capacity_reached", "That place was just filled."],
  ["application_not_pending", "This application has already been decided."],
  ["capacity_too_low", "The team size can't be smaller than the team already is."],
  ["invalid_status", "A project can only be set to open or closed."],
  ["role_open_slots_too_low", "A role can't have fewer places than the students already in it."],
  ["cannot_remove_role_in_use", "A role with members or pending applications can't be removed."],
]);

const FALLBACK = "Something went wrong. Please try again.";

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return MESSAGES.get(error.code) ?? (error.status < 500 ? error.message : FALLBACK);
  return FALLBACK;
}

/** Field errors from a 422 validation response, keyed by field name ("body.email" -> "email"). */
export function fieldErrors(error: unknown): ReadonlyMap<string, string> {
  const fields = new Map<string, string>();
  if (!(error instanceof ApiError) || error.code !== "validation_error") return fields;
  for (const field of error.fields) {
    const name = field.location.split(".").at(-1);
    if (name && !fields.has(name)) fields.set(name, field.message);
  }
  return fields;
}

/** A failed submit as errors for the form's own fields when the API names any, else one form-level message. */
export function submitErrors<Field extends string>(
  error: unknown,
  formFields: readonly Field[],
): Partial<Record<Field | "form", string>> {
  const fields = fieldErrors(error);
  const errors: Partial<Record<Field | "form", string>> = {};
  for (const name of formFields) {
    const message = fields.get(name);
    if (message) errors[name] = message;
  }
  if (!Object.keys(errors).length) errors.form = errorMessage(error);
  return errors;
}
