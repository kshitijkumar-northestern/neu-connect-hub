import { useMemo, useState, type FormEvent } from "react";
import { encode } from "uqr";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { adminApi } from "@/features/admin/api";
import { RecoveryCodesList } from "@/features/admin/components/RecoveryCodesList";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { errorMessage } from "@/features/auth/errors";
import { QrCode } from "@/features/scan/components/QrCode";

type Step =
  | { kind: "intro" }
  | { kind: "enrolled"; secret: string; otpauth_url: string }
  | { kind: "confirmed"; recoveryCodes: string[] };

/** Not enrolled yet: explain, start enrolment, scan the QR (or type the secret), confirm a code,
 * then show the recovery codes once. `onDone` continues into the admin area once the admin has
 * acknowledged saving them (the backend already marked this session verified on confirm). */
export function MfaEnrolment({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>({ kind: "intro" });
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [copied, setCopied] = useState(false);

  const otpauthUrl = step.kind === "enrolled" ? step.otpauth_url : undefined;
  const matrix = useMemo(() => (otpauthUrl ? encode(otpauthUrl) : undefined), [otpauthUrl]);

  async function startEnrolment() {
    setBusy(true);
    setError(undefined);
    try {
      const enrolment = await adminApi.enroll();
      setStep({ kind: "enrolled", secret: enrolment.secret, otpauth_url: enrolment.otpauth_url });
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const { recovery_codes } = await adminApi.confirm(code.trim());
      setStep({ kind: "confirmed", recoveryCodes: recovery_codes });
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  async function copyCodes(codes: string[]) {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
    } catch (failure) {
      setError(errorMessage(failure));
    }
  }

  if (step.kind === "confirmed") {
    return (
      <section className="flex flex-col gap-6">
        <h1 className="text-2xl font-semibold tracking-tight">Save your recovery codes</h1>
        <p className="text-muted-foreground">
          Each code works once, to get back in if you lose your authenticator. We won't show them again.
        </p>
        <FormError>{error}</FormError>
        <RecoveryCodesList codes={step.recoveryCodes} />
        <div className="flex gap-2">
          <Button type="button" variant="outline" onClick={() => void copyCodes(step.recoveryCodes)}>
            {copied ? "Copied" : "Copy codes"}
          </Button>
          <Button type="button" onClick={onDone}>
            I've saved these codes
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Set up two-factor authentication</h1>
      <p className="text-muted-foreground">
        Admin accounts need an authenticator app, in addition to your password, to protect reports and member data.
      </p>
      <FormError>{error}</FormError>
      {step.kind === "intro" ? (
        <Button type="button" disabled={busy} onClick={() => void startEnrolment()} className="self-start">
          Set up your authenticator
        </Button>
      ) : (
        <form onSubmit={confirm} noValidate aria-busy={busy}>
          <FieldGroup>
            {matrix ? (
              <div className="h-48 w-48">
                <QrCode matrix={matrix} aria-label="QR code to scan with your authenticator app" className="h-full w-full" />
              </div>
            ) : null}
            <p className="text-sm text-muted-foreground">
              Can't scan it? Enter this key by hand: <code className="font-mono">{step.secret}</code>
            </p>
            <TextField
              label="Authentication code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
            <Button type="submit" disabled={busy || !code.trim()}>
              Confirm
            </Button>
          </FieldGroup>
        </form>
      )}
    </section>
  );
}
