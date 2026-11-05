import { useState, type FormEvent } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { authApi } from "@/features/auth/api";
import { AuthLayout } from "@/features/auth/components/AuthLayout";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { submitErrors } from "@/features/auth/errors";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"email" | "form", string>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [sentTo, setSentTo] = useState<string>();

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim()) {
      setErrors({ email: "Enter your university email address." });
      return;
    }
    setSubmitting(true);
    setErrors({});
    try {
      await authApi.requestPasswordReset(email.trim());
      setSentTo(email.trim());
    } catch (failure) {
      setErrors(submitErrors(failure, ["email"]));
    } finally {
      setSubmitting(false);
    }
  }

  const backToSignIn = (
    <Link className="underline underline-offset-4" to="/sign-in">
      Back to sign in
    </Link>
  );

  if (sentTo) {
    // Same words whether or not the account exists, so this page can't be used to probe emails.
    return (
      <AuthLayout title="Check your inbox" footer={backToSignIn}>
        <p role="status" className="text-sm text-muted-foreground">
          If an account exists for {sentTo}, we've sent a link to reset its password. It works for 60 minutes.
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Forgot your password?"
      description="We'll email you a link to choose a new one."
      footer={backToSignIn}
    >
      <form onSubmit={onSubmit} noValidate aria-busy={submitting}>
        <FieldGroup>
          <FormError>{errors.form}</FormError>
          <TextField
            label="University email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            error={errors.email}
          />
          <Button type="submit" disabled={submitting}>
            Send reset link
          </Button>
        </FieldGroup>
      </form>
    </AuthLayout>
  );
}
