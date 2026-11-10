import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { authApi } from "@/features/auth/api";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { submitErrors } from "@/features/auth/errors";

/** Ask for a fresh verification link. The answer is the same whether or not the account exists. */
export function ResendVerification({ email: knownEmail }: { email?: string }) {
  const [email, setEmail] = useState(knownEmail ?? "");
  const [status, setStatus] = useState<"idle" | "sending" | "sent">("idle");
  const [errors, setErrors] = useState<Partial<Record<"email" | "form", string>>>({});

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setStatus("sending");
    setErrors({});
    try {
      await authApi.resendVerification(email.trim());
      setStatus("sent");
    } catch (failure) {
      setErrors(submitErrors(failure, knownEmail ? [] : ["email"]));
      setStatus("idle");
    }
  }

  if (status === "sent") {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        If that address still needs verifying, we've sent a new link. It works for 24 hours.
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate aria-busy={status === "sending"}>
      <FieldGroup>
        {knownEmail ? null : (
          <TextField
            label="University email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            error={errors.email}
          />
        )}
        <Button type="submit" variant="outline" disabled={status === "sending" || !email.trim()}>
          Send a new link
        </Button>
        <FormError>{errors.form}</FormError>
      </FieldGroup>
    </form>
  );
}
