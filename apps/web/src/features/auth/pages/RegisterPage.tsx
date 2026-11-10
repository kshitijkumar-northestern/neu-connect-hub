import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router";

import { Button } from "@/components/ui/button";
import { FieldGroup } from "@/components/ui/field";
import { authApi, PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from "@/features/auth/api";
import { useAuth } from "@/features/auth/AuthProvider";
import { AuthLayout } from "@/features/auth/components/AuthLayout";
import { FormError } from "@/features/auth/components/FormError";
import { ResendVerification } from "@/features/auth/components/ResendVerification";
import { TextField } from "@/features/auth/components/TextField";
import { errorMessage, submitErrors } from "@/features/auth/errors";
import { passwordError } from "@/features/auth/validation";
import { ApiError } from "@/lib/http";

interface Errors {
  email?: string;
  password?: string;
  form?: string;
}

function validate(email: string, password: string): Errors {
  const errors: Errors = {};
  if (!email.trim()) errors.email = "Enter your university email address.";
  const weak = passwordError(password);
  if (weak) errors.password = weak;
  return errors;
}

export function RegisterPage() {
  const { state } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [registeredAs, setRegisteredAs] = useState<string>();

  if (state.status === "signed-in") return <Navigate to="/" replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const found = validate(email, password);
    setErrors(found);
    if (Object.keys(found).length) return;

    setSubmitting(true);
    try {
      await authApi.register(email.trim(), password);
      setRegisteredAs(email.trim());
    } catch (error) {
      const wrongDomain = error instanceof ApiError && error.code === "email_domain_not_allowed";
      setErrors(wrongDomain ? { email: errorMessage(error) } : submitErrors(error, ["email", "password"]));
    } finally {
      setSubmitting(false);
    }
  }

  if (registeredAs) {
    return (
      <AuthLayout
        title="Check your inbox"
        description={
          <>
            We sent a verification link to <strong className="text-foreground">{registeredAs}</strong>. Open it within
            24 hours to activate your account.
          </>
        }
        footer={
          <Link className="underline underline-offset-4" to="/sign-in">
            Already verified? Sign in
          </Link>
        }
      >
        <ResendVerification email={registeredAs} />
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Create your account"
      description="Students sign up with their university email."
      footer={
        <p className="text-muted-foreground">
          Already have an account?{" "}
          <Link className="text-foreground underline underline-offset-4" to="/sign-in">
            Sign in
          </Link>
        </p>
      }
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
          <TextField
            label="Password"
            type="password"
            autoComplete="new-password"
            required
            minLength={PASSWORD_MIN_LENGTH}
            maxLength={PASSWORD_MAX_LENGTH}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            description={`At least ${PASSWORD_MIN_LENGTH} characters. A short phrase is easy to remember.`}
            error={errors.password}
          />
          <Button type="submit" disabled={submitting}>
            Create account
          </Button>
        </FieldGroup>
      </form>
    </AuthLayout>
  );
}
