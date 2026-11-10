import { Link, useLocation } from "react-router";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/AuthProvider";
import { AuthUnavailable } from "@/features/auth/components/AuthUnavailable";
import { ResendVerification } from "@/features/auth/components/ResendVerification";
import type { HomeNotice } from "@/features/profiles/pages/SettingsPage";

export function HomePage() {
  const { state } = useAuth();
  const location = useLocation();
  const accountDeleted = (location.state as HomeNotice | null)?.notice === "account-deleted";

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        <h1 className="text-4xl font-bold tracking-tight">NU Connect Hub</h1>
        <p className="text-lg text-muted-foreground">Find collaborators, form project teams and work together.</p>
      </div>

      {accountDeleted ? <p role="status">Your account has been deleted.</p> : null}

      {state.status === "signed-out" ? (
        <div className="flex flex-wrap gap-3">
          <Button asChild>
            <Link to="/register">Create account</Link>
          </Button>
          <Button asChild variant="outline">
            <Link to="/sign-in">Sign in</Link>
          </Button>
        </div>
      ) : null}

      {state.status === "unavailable" ? <AuthUnavailable /> : null}

      {state.status === "signed-in" ? (
        <div className="flex flex-col gap-3">
          <p>
            Signed in as <strong>{state.user.email}</strong>.
          </p>
          {state.user.verified ? (
            <Link className="text-sm underline underline-offset-4" to="/profile">
              Edit your profile
            </Link>
          ) : null}
          {state.user.verified ? null : (
            <div className="flex max-w-sm flex-col gap-3">
              <p className="text-muted-foreground">
                Verify your email address to start finding collaborators. Check your inbox for the link.
              </p>
              <ResendVerification email={state.user.email} />
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}
