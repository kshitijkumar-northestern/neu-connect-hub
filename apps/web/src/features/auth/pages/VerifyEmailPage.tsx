import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import { authApi } from "@/features/auth/api";
import { useAuth } from "@/features/auth/AuthProvider";
import { AuthLayout } from "@/features/auth/components/AuthLayout";
import { ResendVerification } from "@/features/auth/components/ResendVerification";
import { errorMessage } from "@/features/auth/errors";
import { useHashToken } from "@/features/auth/useHashToken";

type Status = "verifying" | "verified" | "failed" | "no-token";

export function VerifyEmailPage() {
  const token = useHashToken();
  const { state, refresh } = useAuth();
  const [status, setStatus] = useState<Status>(token ? "verifying" : "no-token");
  const [error, setError] = useState<string>();
  // The token is single-use: never send it twice (React StrictMode runs effects twice in dev).
  const sent = useRef(false);

  useEffect(() => {
    if (!token || sent.current) return;
    sent.current = true;
    authApi
      .verifyEmail(token)
      .then(async () => {
        setStatus("verified");
        await refresh();
      })
      .catch((failure: unknown) => {
        setError(errorMessage(failure));
        setStatus("failed");
      });
  }, [token, refresh]);

  if (status === "verifying") {
    return (
      <AuthLayout title="Verifying your email">
        <p role="status" className="text-muted-foreground">
          One moment…
        </p>
      </AuthLayout>
    );
  }

  if (status === "verified") {
    const signedIn = state.status === "signed-in";
    return (
      <AuthLayout title="Email verified" description="Your university email address is confirmed.">
        <Button asChild>
          <Link to={signedIn ? "/" : "/sign-in"}>{signedIn ? "Continue" : "Sign in"}</Link>
        </Button>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Verify your email"
      description={
        status === "failed" ? (
          <span role="alert" className="text-destructive">
            {error}
          </span>
        ) : (
          "Open the link in the email we sent you. Need a new one?"
        )
      }
    >
      <ResendVerification />
    </AuthLayout>
  );
}
