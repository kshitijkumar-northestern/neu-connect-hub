import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation } from "react-router";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { useAuth } from "@/features/auth/AuthProvider";
import { AuthLayout } from "@/features/auth/components/AuthLayout";
import { FormError } from "@/features/auth/components/FormError";
import { TextField } from "@/features/auth/components/TextField";
import { submitErrors } from "@/features/auth/errors";
import { safeReturnPath, takeReturnHash } from "@/features/auth/guards";

/** Set by ResetPasswordPage when it sends the student here. */
export interface SignInNotice {
  notice?: "password-reset";
}

export function SignInPage() {
  const { state, signIn } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"email" | "password" | "form", string>>>({});
  const [submitting, setSubmitting] = useState(false);
  // Consumed at most once per visit to this page, the same way useHashToken reads the fragment
  // once: a lazy initializer only ever keeps its first call's result, even under StrictMode's
  // double-render, so the destructive takeReturnHash() can't run twice and lose the hash.
  const [returnHash] = useState(() => takeReturnHash());
  const returnTo = safeReturnPath(location.state) + returnHash;
  const passwordWasReset = (location.state as SignInNotice | null)?.notice === "password-reset";

  // Also where a successful sign-in lands: signIn() updates the state and this redirect follows.
  if (state.status === "signed-in") return <Navigate to={returnTo} replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setErrors({ form: "Enter your email and password." });
      return;
    }
    setSubmitting(true);
    setErrors({});
    try {
      await signIn(email.trim(), password);
    } catch (failure) {
      setErrors(submitErrors(failure, ["email", "password"]));
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout
      title="Sign in"
      footer={
        <p className="text-muted-foreground">
          New here?{" "}
          <Link className="text-foreground underline underline-offset-4" to="/register">
            Create an account
          </Link>
        </p>
      }
    >
      <form onSubmit={onSubmit} noValidate aria-busy={submitting}>
        <FieldGroup>
          {passwordWasReset && !errors.form ? (
            <Alert role="status">
              <AlertDescription>Your password has been changed. Sign in with your new password.</AlertDescription>
            </Alert>
          ) : null}
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
          <TextField
            label="Password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            error={errors.password}
          />
          <Button type="submit" disabled={submitting}>
            Sign in
          </Button>
          <Link className="text-sm underline underline-offset-4" to="/forgot-password">
            Forgot your password?
          </Link>
        </FieldGroup>
      </form>
    </AuthLayout>
  );
}
