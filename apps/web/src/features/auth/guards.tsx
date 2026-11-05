import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/features/auth/AuthProvider";
import { AuthUnavailable } from "@/features/auth/components/AuthUnavailable";
import { VerifyEmailPrompt } from "@/features/auth/components/VerifyEmailPrompt";

export interface ReturnTo {
  from?: string;
}

function Loading() {
  return (
    <p role="status" className="text-muted-foreground">
      Loading…
    </p>
  );
}

/** Pages for signed-in students. Others go to sign in and come back here afterwards. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  const location = useLocation();
  if (state.status === "loading") return <Loading />;
  if (state.status === "unavailable") return <AuthUnavailable />;
  if (state.status === "signed-out") {
    const returnTo: ReturnTo = { from: `${location.pathname}${location.search}` };
    return <Navigate to="/sign-in" replace state={returnTo} />;
  }
  return children;
}

function VerifiedOnly({ children }: { children: ReactNode }) {
  const { state } = useAuth();
  if (state.status === "signed-in" && !state.user.verified) return <VerifyEmailPrompt email={state.user.email} />;
  return children;
}

/** Pages for verified students. Includes RequireAuth, so it can be used on its own. */
export function RequireVerified({ children }: { children: ReactNode }) {
  return (
    <RequireAuth>
      <VerifiedOnly>{children}</VerifiedOnly>
    </RequireAuth>
  );
}

/** Only accept in-app paths as a return target (never "//evil.example" or a full URL). */
export function safeReturnPath(state: unknown): string {
  const from = (state as ReturnTo | null)?.from;
  return typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/";
}
