import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, FieldLabel } from "@/components/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAuth } from "@/features/auth/AuthProvider";
import { FormError } from "@/features/auth/components/FormError";
import { errorMessage } from "@/features/auth/errors";
import { profilesApi, trustApi, type PublicProfile, type ReportReason } from "@/features/profiles/api";
import { TextareaField } from "@/features/profiles/components/TextareaField";
import { GOAL_LABELS, LEVEL_LABELS, REPORT_DETAILS_MAX, REPORT_REASONS, REPORT_REASON_LABELS, WEEKDAYS } from "@/features/profiles/constants";
import { formatTimeRange } from "@/features/profiles/time";
import { ApiError } from "@/lib/http";

type LoadState = "loading" | "ready" | "not-found" | "error";

function BlockAction({ userId, onBlocked }: { userId: string; onBlocked: () => void }) {
  const [open, setOpen] = useState(false);
  const [blocking, setBlocking] = useState(false);
  const [error, setError] = useState<string>();

  async function confirm() {
    setBlocking(true);
    setError(undefined);
    try {
      await trustApi.block(userId);
      setOpen(false);
      onBlocked();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBlocking(false);
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger asChild>
        <Button variant="outline">Block</Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Block this student?</AlertDialogTitle>
          <AlertDialogDescription>
            You and this student will no longer see each other's profiles. You can undo this from Settings at any
            time.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <FormError>{error}</FormError>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={blocking}>Cancel</AlertDialogCancel>
          {/* A plain button, not AlertDialogAction: that primitive always closes the dialog on
              click, which would hide a failed block's error message. */}
          <Button variant="destructive" disabled={blocking} onClick={() => void confirm()}>
            Block
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function ReportAction({ userId, onBlocked }: { userId: string; onBlocked: () => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState<ReportReason | "">("");
  const [details, setDetails] = useState("");
  const [alsoBlock, setAlsoBlock] = useState(false);
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [errors, setErrors] = useState<{ reason?: string; form?: string }>({});

  function reset() {
    setReason("");
    setDetails("");
    setAlsoBlock(false);
    setSent(false);
    setErrors({});
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!reason) {
      setErrors({ reason: "Choose a reason." });
      return;
    }
    setErrors({});
    setSending(true);
    try {
      await trustApi.report(userId, reason, details.trim());
      if (alsoBlock) {
        await trustApi.block(userId);
        onBlocked();
      }
      setSent(true);
    } catch (failure) {
      setErrors({ form: errorMessage(failure) });
    } finally {
      setSending(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline">Report</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Report this student</DialogTitle>
          <DialogDescription>Our moderators review every report.</DialogDescription>
        </DialogHeader>
        {sent ? (
          <>
            <p role="status">Thanks. Our moderators will review this report.</p>
            <DialogFooter>
              <Button variant="outline" onClick={() => setOpen(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={onSubmit} noValidate aria-busy={sending} className="flex flex-col gap-4">
            <FormError>{errors.form}</FormError>
            <Field data-invalid={errors.reason ? true : undefined}>
              <FieldLabel htmlFor="report-reason">Reason</FieldLabel>
              <Select value={reason} onValueChange={(value) => setReason(value as ReportReason)}>
                <SelectTrigger id="report-reason" aria-invalid={errors.reason ? true : undefined} className="w-full">
                  <SelectValue placeholder="Choose a reason" />
                </SelectTrigger>
                <SelectContent>
                  {REPORT_REASONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {REPORT_REASON_LABELS[value]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <TextareaField
              label="Details (optional)"
              maxLength={REPORT_DETAILS_MAX}
              value={details}
              onChange={(event) => setDetails(event.target.value)}
            />
            <Field orientation="horizontal">
              <Checkbox id="also-block" checked={alsoBlock} onCheckedChange={(checked) => setAlsoBlock(checked === true)} />
              <FieldLabel htmlFor="also-block" className="font-normal">
                Also block this student
              </FieldLabel>
            </Field>
            <DialogFooter>
              <Button type="submit" disabled={sending}>
                Send report
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** How other students see a profile: name, headline, bio, goals, skills, interests, time zone
 * and availability. A 404 (hidden, missing or blocked) always reads the same neutral message. */
export function PublicProfilePage() {
  const { userId } = useParams<{ userId: string }>();
  const { state } = useAuth();
  const ownUserId = state.status === "signed-in" ? state.user.id : undefined;

  const [load, setLoad] = useState<LoadState>("loading");
  const [profile, setProfile] = useState<PublicProfile>();
  const [blocked, setBlocked] = useState(false);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    profilesApi
      .get(userId)
      .then((result) => {
        if (cancelled) return;
        setProfile(result);
        setLoad("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoad(error instanceof ApiError && error.status === 404 ? "not-found" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  if (!userId) {
    return <p>This profile isn't available.</p>;
  }

  if (load === "loading") {
    return (
      <p role="status" className="text-muted-foreground">
        Loading…
      </p>
    );
  }

  if (load === "not-found") {
    return <p>This profile isn't available.</p>;
  }

  if (load === "error" || !profile) {
    return <p role="alert">Can't load this profile right now. Check your connection and try again.</p>;
  }

  const isOwn = profile.user_id === ownUserId;
  const availableDays = WEEKDAYS.map((label, weekday) => ({
    label,
    slots: profile.availability.filter((slot) => slot.weekday === weekday),
  })).filter((day) => day.slots.length);

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">{profile.display_name}</h1>
        {profile.headline ? <p className="text-muted-foreground">{profile.headline}</p> : null}
      </div>

      {isOwn ? (
        <div className="flex flex-col items-start gap-2">
          <p className="text-sm text-muted-foreground">This is how other students see your profile.</p>
          <Button asChild variant="outline">
            <Link to="/profile">Edit your profile</Link>
          </Button>
        </div>
      ) : null}

      {profile.bio ? <p className="whitespace-pre-line">{profile.bio}</p> : null}

      {profile.goals.length ? (
        <div>
          <h2 className="text-sm font-medium">Looking for</h2>
          <ul className="mt-2 flex flex-wrap gap-2">
            {profile.goals.map((goal) => (
              <li key={goal}>
                <Badge variant="secondary">{GOAL_LABELS[goal]}</Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {profile.skills.length ? (
        <div>
          <h2 className="text-sm font-medium">Skills</h2>
          <ul className="mt-2 flex flex-wrap gap-2">
            {profile.skills.map((skill) => (
              <li key={skill.slug}>
                <Badge variant="secondary">
                  {skill.name} · {LEVEL_LABELS[skill.level]}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {profile.seeking_skills.length ? (
        <div>
          <h2 className="text-sm font-medium">Looking to learn</h2>
          <ul className="mt-2 flex flex-wrap gap-2">
            {profile.seeking_skills.map((skill) => (
              <li key={skill.slug}>
                <Badge variant="outline">{skill.name}</Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {profile.interests.length ? (
        <div>
          <h2 className="text-sm font-medium">Interests</h2>
          <ul className="mt-2 flex flex-wrap gap-2">
            {profile.interests.map((interest) => (
              <li key={interest.slug}>
                <Badge variant="outline">{interest.name}</Badge>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div>
        <h2 className="text-sm font-medium">Time zone</h2>
        <p className="text-muted-foreground">{profile.timezone}</p>
      </div>

      {availableDays.length ? (
        <div>
          <h2 className="text-sm font-medium">Weekly availability</h2>
          <ul className="mt-2 flex flex-col gap-1">
            {availableDays.map((day) => (
              <li key={day.label} className="flex gap-2 text-sm">
                <span className="w-24 shrink-0 font-medium">{day.label}</span>
                <span className="text-muted-foreground">
                  {day.slots.map((slot) => formatTimeRange(slot.start_min, slot.end_min)).join(", ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {!isOwn ? (
        <div className="flex flex-wrap items-center gap-3">
          {blocked ? (
            <p className="text-sm text-muted-foreground">
              This student is now hidden from you.{" "}
              <Link className="underline underline-offset-4" to="/settings">
                Manage blocked students
              </Link>
            </p>
          ) : (
            <BlockAction userId={profile.user_id} onBlocked={() => setBlocked(true)} />
          )}
          <ReportAction userId={profile.user_id} onBlocked={() => setBlocked(true)} />
        </div>
      ) : null}
    </section>
  );
}
