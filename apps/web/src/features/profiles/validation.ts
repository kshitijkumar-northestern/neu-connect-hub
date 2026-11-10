import type { AvailabilitySlot, ProfileInput } from "@/features/profiles/api";
import { BIO_MAX, DISPLAY_NAME_MAX, HEADLINE_MAX, MAX_INTERESTS, MAX_SKILLS, MAX_SLOTS } from "@/features/profiles/constants";

export type ProfileField =
  | "display_name"
  | "headline"
  | "bio"
  | "timezone"
  | "skills"
  | "seeking_skills"
  | "interests"
  | "availability";

export const PROFILE_FIELDS: readonly ProfileField[] = [
  "display_name",
  "headline",
  "bio",
  "timezone",
  "skills",
  "seeking_skills",
  "interests",
  "availability",
];

export type ProfileFormErrors = Partial<Record<ProfileField | "form", string>>;

/** A range must end after it starts, and ranges on the same day can't overlap, matching the
 * API's own checks (apps/api/app/modules/profiles/schemas.py). */
export function availabilityError(slots: readonly AvailabilitySlot[]): string | undefined {
  if (slots.length > MAX_SLOTS) return `Use at most ${MAX_SLOTS} time ranges in total.`;
  for (const slot of slots) {
    if (slot.start_min >= slot.end_min) return "A time range must end after it starts.";
  }
  const ordered = [...slots].sort((a, b) => a.weekday - b.weekday || a.start_min - b.start_min);
  for (let index = 1; index < ordered.length; index += 1) {
    const earlier = ordered[index - 1]!;
    const later = ordered[index]!;
    if (earlier.weekday === later.weekday && earlier.end_min > later.start_min) {
      return "Time ranges on the same day can't overlap.";
    }
  }
  return undefined;
}

/** Client-side checks that mirror the API's own validation, so most mistakes are caught before
 * a round trip. The server still re-validates and remains the source of truth. */
export function validateProfile(input: ProfileInput): ProfileFormErrors {
  const errors: ProfileFormErrors = {};

  if (!input.display_name.trim()) errors.display_name = "Enter your name.";
  else if (input.display_name.length > DISPLAY_NAME_MAX) {
    errors.display_name = `Use at most ${DISPLAY_NAME_MAX} characters.`;
  }

  if (input.headline.length > HEADLINE_MAX) errors.headline = `Use at most ${HEADLINE_MAX} characters.`;
  if (input.bio.length > BIO_MAX) errors.bio = `Use at most ${BIO_MAX} characters.`;
  if (!input.timezone.trim()) errors.timezone = "Choose your time zone.";

  if (input.skills.length > MAX_SKILLS) errors.skills = `Choose at most ${MAX_SKILLS} skills.`;
  if (input.seeking_skills.length > MAX_SKILLS) {
    errors.seeking_skills = `Choose at most ${MAX_SKILLS} skills.`;
  }
  if (input.interests.length > MAX_INTERESTS) errors.interests = `Choose at most ${MAX_INTERESTS} interests.`;

  const availability = availabilityError(input.availability);
  if (availability) errors.availability = availability;

  return errors;
}
