import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { authApi, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/features/auth/api";
import { useAuth } from "@/features/auth/AuthProvider";
import { AuthLayout } from "@/features/auth/components/AuthLayout";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { submitErrors } from "@/features/auth/errors";
import type { SignInNotice } from "@/features/auth/pages/SignInPage";
import { useHashToken } from "@/features/auth/useHashToken";
import { passwordError } from "@/features/auth/validation";

interface Errors {
  password?: string;
  confirm?: string;
  form?: string;
}

export function ResetPasswordPage() {
  const token = useHashToken();
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);

  if (!token) {
    return (
      <AuthLayout title="Choose a new password" description="This link is missing its code.">
        <Button asChild variant="outline">
          <Link to="/forgot-password">Request a new link</Link>
        </Button>
      </AuthLayout>
    );
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const found: Errors = {};
    const weak = passwordError(password);
    if (weak) found.password = weak;
    else if (confirm !== password) found.confirm = "The passwords don't match.";
    setErrors(found);
    if (Object.keys(found).length || !token) return;

    setSubmitting(true);
    try {
      await authApi.confirmPasswordReset(token, password);
      await refresh(); // the reset ended every session, including this browser's
      const notice: SignInNotice = { notice: "password-reset" };
      void navigate("/sign-in", { replace: true, state: notice });
    } catch (failure) {
      setErrors(submitErrors(failure, ["password"]));
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Choose a new password"
      description="You'll need to sign in again on every device."
      footer={
        errors.form ? (
          <Link className="underline underline-offset-4" to="/forgot-password">
            Request a new link
          </Link>
        ) : null
      }
    >
      <form onSubmit={onSubmit} noValidate aria-busy={submitting}>
        <FieldGroup>
          <FormError>{errors.form}</FormError>
          <TextField
            label="New password"
            type="password"
            autoComplete="new-password"
            required
            minLength={PASSWORD_MIN_LENGTH}
            maxLength={PASSWORD_MAX_LENGTH}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            description={`At least ${PASSWORD_MIN_LENGTH} characters.`}
            error={errors.password}
          />
          <TextField
            label="Confirm new password"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            error={errors.confirm}
          />
          <Button type="submit" disabled={submitting}>
            Set new password
          </Button>
        </FieldGroup>
      </form>
    </AuthLayout>
  );
}
