import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router";

import { useAuth } from "@/features/auth/AuthProvider";
import { AuthUnavailable } from "@/features/auth/components/AuthUnavailable";
import { VerifyEmailPrompt } from "@/features/auth/components/VerifyEmailPrompt";

export interface ReturnTo {
  from?: string;
}

// Module-scoped and memory-only — never put in `ReturnTo`/`history.state`, which browsers can
// persist across a session restore (and `from` applies to every guarded route, not just one).
// Every redirect to sign-in overwrites this, so a hash from an abandoned visit can never leak into
// a later, unrelated sign-in.
let pendingReturnHash: string | undefined;

/** A fragment (e.g. a scan token in `/connect#token=...`) is never the server's to see and never
 * the router's to remember: captured here instead of in `from`, for `takeReturnHash` to consume
 * once sign-in actually uses it. */
function rememberReturnHash(hash: string): void {
  pendingReturnHash = hash || undefined;
}

/** Consumes (clears) the hash a guard remembered before redirecting to sign-in, if any, so it's
 * read at most once and never lingers for an unrelated later sign-in. Returns "" when there's
 * nothing pending, or it doesn't actually look like a hash fragment. */
export function takeReturnHash(): string {
  const hash = pendingReturnHash;
  pendingReturnHash = undefined;
  return hash && hash.startsWith("#") && hash.length > 1 ? hash : "";
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
    // The hash (e.g. a scan token in /connect#token=...) is remembered separately, never in
    // `from`: `from` ends up in router/history state, which a browser can persist, but a fragment
    // must survive the round trip to sign in and back in memory only.
    rememberReturnHash(location.hash);
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
