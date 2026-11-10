import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { Field, FieldContent, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useAuth } from "@/features/auth/AuthProvider";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { submitErrors } from "@/features/auth/errors";
import { profilesApi, type AvailabilitySlot, type Goal, type InterestOption, type OwnProfile, type ProfileInput, type SkillOption } from "@/features/profiles/api";
import {
  AvailabilityEditor,
  emptyAvailabilityRows,
  type AvailabilityRows,
} from "@/features/profiles/components/AvailabilityEditor";
import { GoalsField } from "@/features/profiles/components/GoalsField";
import { InterestToggles } from "@/features/profiles/components/InterestToggles";
import { SkillField, type SkillChoice } from "@/features/profiles/components/SkillField";
import { TextareaField } from "@/features/profiles/components/TextareaField";
import { BIO_MAX, DISPLAY_NAME_MAX, HEADLINE_MAX } from "@/features/profiles/constants";
import { minutesToTime, timeToEndMinutes, timeToStartMinutes } from "@/features/profiles/time";
import { PROFILE_FIELDS, validateProfile, type ProfileFormErrors } from "@/features/profiles/validation";
import { ApiError } from "@/lib/http";

const TIMEZONES = Intl.supportedValuesOf("timeZone");

type LoadState = "loading" | "ready" | "error";

function rowsFromSlots(slots: readonly AvailabilitySlot[]): AvailabilityRows {
  const rows = emptyAvailabilityRows();
  let id = 0;
  const ordered = [...slots].sort((a, b) => a.weekday - b.weekday || a.start_min - b.start_min);
  for (const slot of ordered) {
    rows[slot.weekday]?.push({ id: id++, start: minutesToTime(slot.start_min), end: minutesToTime(slot.end_min) });
  }
  return rows;
}

function slotsFromRows(rows: AvailabilityRows): AvailabilitySlot[] {
  const slots: AvailabilitySlot[] = [];
  rows.forEach((day, weekday) => {
    for (const row of day) slots.push({ weekday, start_min: timeToStartMinutes(row.start), end_min: timeToEndMinutes(row.end) });
  });
  return slots;
}

/** Create or edit your own profile. A 404 from /me/profile means a new, empty profile. */
export function ProfilePage() {
  const { state } = useAuth();
  const ownUserId = state.status === "signed-in" ? state.user.id : undefined;

  const [load, setLoad] = useState<LoadState>("loading");
  const [attempt, setAttempt] = useState(0);
  const [isNewProfile, setIsNewProfile] = useState(false);
  const [skillOptions, setSkillOptions] = useState<SkillOption[]>([]);
  const [interestOptions, setInterestOptions] = useState<InterestOption[]>([]);

  const [displayName, setDisplayName] = useState("");
  const [headline, setHeadline] = useState("");
  const [bio, setBio] = useState("");
  const [goals, setGoals] = useState<Goal[]>([]);
  const [timezone, setTimezone] = useState("");
  const [skillsHave, setSkillsHave] = useState<SkillChoice[]>([]);
  const [skillsSeeking, setSkillsSeeking] = useState<SkillChoice[]>([]);
  const [interests, setInterests] = useState<string[]>([]);
  const [rows, setRows] = useState<AvailabilityRows>(emptyAvailabilityRows());
  const [discoverable, setDiscoverable] = useState(false);
  const [feedbackConsent, setFeedbackConsent] = useState(false);

  const [errors, setErrors] = useState<ProfileFormErrors>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  function applyProfile(profile: OwnProfile) {
    setDisplayName(profile.display_name);
    setHeadline(profile.headline);
    setBio(profile.bio);
    setGoals(profile.goals);
    setTimezone(profile.timezone);
    setSkillsHave(profile.skills.map((skill) => ({ slug: skill.slug, level: skill.level })));
    setSkillsSeeking(profile.seeking_skills.map((skill) => ({ slug: skill.slug })));
    setInterests(profile.interests.map((interest) => interest.slug));
    setRows(rowsFromSlots(profile.availability));
    setDiscoverable(profile.discoverable);
    setFeedbackConsent(profile.feedback_consent);
  }

  useEffect(() => {
    let cancelled = false;

    async function run() {
      try {
        const [profile, skills, interestList] = await Promise.all([
          profilesApi.mine().catch((error: unknown) => {
            if (error instanceof ApiError && error.status === 404) return null;
            throw error;
          }),
          profilesApi.skills(),
          profilesApi.interests(),
        ]);
        if (cancelled) return;
        setSkillOptions(skills);
        setInterestOptions(interestList);
        if (profile) {
          applyProfile(profile);
          setIsNewProfile(false);
        } else {
          setIsNewProfile(true);
          setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone);
        }
        setLoad("ready");
      } catch {
        if (!cancelled) setLoad("error");
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
    // Re-runs only when the student asks to retry after a load failure (`attempt` is a counter,
    // not read inside the effect).
  }, [attempt]);

  function buildInput(): ProfileInput {
    return {
      display_name: displayName.trim(),
      headline: headline.trim(),
      bio: bio.trim(),
      goals,
      timezone,
      skills: skillsHave.map((choice) => ({ slug: choice.slug, level: choice.level ?? "beginner" })),
      seeking_skills: skillsSeeking.map((choice) => choice.slug),
      interests,
      availability: slotsFromRows(rows),
      discoverable,
      feedback_consent: feedbackConsent,
    };
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setSaved(false);
    const input = buildInput();
    const clientErrors = validateProfile(input);
    if (Object.keys(clientErrors).length) {
      setErrors(clientErrors);
      return;
    }
    setErrors({});
    setSaving(true);
    try {
      const saved_ = await profilesApi.save(input);
      applyProfile(saved_);
      setIsNewProfile(false);
      setSaved(true);
    } catch (failure) {
      setErrors(submitErrors(failure, PROFILE_FIELDS));
    } finally {
      setSaving(false);
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
    return (
      <div role="alert" className="flex max-w-sm flex-col items-start gap-3">
        <p>Can't load your profile right now. Check your connection and try again.</p>
        <Button
          variant="outline"
          onClick={() => {
            setLoad("loading");
            setAttempt((value) => value + 1);
          }}
        >
          Try again
        </Button>
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">Your profile</h1>
        {isNewProfile ? <p className="text-muted-foreground">Tell other students about yourself.</p> : null}
      </div>
      <form onSubmit={onSubmit} noValidate aria-busy={saving} className="max-w-2xl">
        <FieldGroup>
          <FormError>{errors.form}</FormError>
          <TextField
            label="Display name"
            required
            maxLength={DISPLAY_NAME_MAX}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            error={errors.display_name}
          />
          <TextField
            label="Headline"
            maxLength={HEADLINE_MAX}
            value={headline}
            onChange={(event) => setHeadline(event.target.value)}
            error={errors.headline}
          />
          <TextareaField
            label="Bio"
            maxLength={BIO_MAX}
            value={bio}
            onChange={(event) => setBio(event.target.value)}
            error={errors.bio}
          />
          <GoalsField selected={goals} onChange={setGoals} />
          <Field data-invalid={errors.timezone ? true : undefined}>
            <FieldLabel htmlFor="timezone">Time zone</FieldLabel>
            <Select value={timezone} onValueChange={setTimezone}>
              <SelectTrigger id="timezone" aria-invalid={errors.timezone ? true : undefined} className="w-full">
                <SelectValue placeholder="Choose a time zone" />
              </SelectTrigger>
              <SelectContent>
                {TIMEZONES.map((zone) => (
                  <SelectItem key={zone} value={zone}>
                    {zone}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <FieldError>{errors.timezone}</FieldError>
          </Field>
          <SkillField
            legend="Skills you have"
            options={skillOptions}
            selected={skillsHave}
            withLevel
            onChange={setSkillsHave}
            error={errors.skills}
          />
          <SkillField
            legend="Skills you're looking for"
            options={skillOptions}
            selected={skillsSeeking}
            withLevel={false}
            onChange={setSkillsSeeking}
            error={errors.seeking_skills}
          />
          <InterestToggles options={interestOptions} selected={interests} onChange={setInterests} error={errors.interests} />
          <AvailabilityEditor rows={rows} onChange={setRows} error={errors.availability} />
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor="discoverable-switch">Show my profile to other students</FieldLabel>
              <FieldDescription>
                Off by default. Turn this on once you're ready for verified students to find and contact you.
              </FieldDescription>
            </FieldContent>
            <Switch id="discoverable-switch" checked={discoverable} onCheckedChange={setDiscoverable} />
          </Field>
          <Field orientation="horizontal">
            <FieldContent>
              <FieldLabel htmlFor="feedback-consent-switch">
                Let NU Connect Hub learn from my match feedback
              </FieldLabel>
              <FieldDescription>Helps us improve future matches. You can turn this off at any time.</FieldDescription>
            </FieldContent>
            <Switch id="feedback-consent-switch" checked={feedbackConsent} onCheckedChange={setFeedbackConsent} />
          </Field>
          <div className="flex flex-wrap items-center gap-4">
            <Button type="submit" disabled={saving}>
              Save profile
            </Button>
            {ownUserId ? (
              <Link className="text-sm underline underline-offset-4" to={`/people/${ownUserId}`}>
                Preview how other students see your profile
              </Link>
            ) : null}
          </div>
          {saved ? <p role="status">Profile saved</p> : null}
        </FieldGroup>
      </form>
    </section>
  );
}
