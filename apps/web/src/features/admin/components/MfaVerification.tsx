import { useState, type FormEvent } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { adminApi, type MfaStatus } from "@/features/admin/api";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { errorMessage } from "@/features/auth/errors";

/** Recovery codes left at or below this many gets a warning to set up a fresh authenticator soon. */
const LOW_RECOVERY_CODES = 2;

/** Already enrolled, but this session hasn't passed MFA recently enough: a 6-digit code from the
 * authenticator app, or a recovery code as a fallback. */
export function MfaVerification({
  recoveryCodesLeft,
  onVerified,
}: {
  recoveryCodesLeft: number;
  onVerified: (status: MfaStatus) => void;
}) {
  const [useRecoveryCode, setUseRecoveryCode] = useState(false);
  const [code, setCode] = useState("");
  const [recoveryCode, setRecoveryCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const status = useRecoveryCode
        ? await adminApi.verify({ recovery_code: recoveryCode.trim() })
        : await adminApi.verify({ code: code.trim() });
      onVerified(status);
    } catch (failure) {
      setError(errorMessage(failure));
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Verify it's you</h1>
      <p className="text-muted-foreground">Enter the code from your authenticator app to continue.</p>
      {recoveryCodesLeft <= LOW_RECOVERY_CODES ? (
        <Alert role="status">
          <AlertDescription>
            {recoveryCodesLeft === 0
              ? "You have no recovery codes left. If you lose your device, ask another admin for help."
              : `Only ${recoveryCodesLeft} recovery ${recoveryCodesLeft === 1 ? "code" : "codes"} left.`}
          </AlertDescription>
        </Alert>
      ) : null}
      <form onSubmit={onSubmit} noValidate aria-busy={busy}>
        <FieldGroup>
          <FormError>{error}</FormError>
          {useRecoveryCode ? (
            <TextField
              label="Recovery code"
              autoComplete="off"
              required
              value={recoveryCode}
              onChange={(event) => setRecoveryCode(event.target.value)}
            />
          ) : (
            <TextField
              label="Authentication code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          )}
          <Button type="submit" disabled={busy || (useRecoveryCode ? !recoveryCode.trim() : !code.trim())}>
            Verify
          </Button>
          <Button
            type="button"
            variant="link"
            className="self-start px-0"
            onClick={() => {
              setUseRecoveryCode((current) => !current);
              setError(undefined);
            }}
          >
            {useRecoveryCode ? "Use an authentication code instead" : "Use a recovery code instead"}
          </Button>
        </FieldGroup>
      </form>
    </section>
  );
}
