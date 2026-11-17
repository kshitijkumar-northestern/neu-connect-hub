import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Outlet } from "react-router";

import { NotFoundPage } from "@/app/NotFoundPage";
import { Button } from "@/components/ui/button";
import { adminApi, type MfaStatus } from "@/features/admin/api";
import { MfaEnrolment } from "@/features/admin/components/MfaEnrolment";
import { MfaVerification } from "@/features/admin/components/MfaVerification";
import { useAuth } from "@/features/auth/AuthProvider";

export interface AdminGateValue {
  /** Call when an admin request answers 403 `mfa_required`: the gate asks for a code again. */
  reverify: () => void;
}

const AdminGateContext = createContext<AdminGateValue>({ reverify: () => undefined });

export function useAdminGate(): AdminGateValue {
  return useContext(AdminGateContext);
}

type LoadState = "loading" | "ready" | "error";

function Loading() {
  return (
    <p role="status" className="text-muted-foreground">
      Loading…
    </p>
  );
}

/** Loads and reacts to `GET /admin/mfa`, once we already know the signed-in student is an admin. */
function MfaGate() {
  const [load, setLoad] = useState<LoadState>("loading");
  const [mfa, setMfa] = useState<MfaStatus>();

  // Split from `reload` below: the mount effect only ever starts the fetch (state already starts
  // "loading"), so its body never calls setState synchronously itself — only the async callbacks
  // that settle later do.
  const fetchStatus = useCallback(() => {
    adminApi
      .mfaStatus()
      .then((status) => {
        setMfa(status);
        setLoad("ready");
      })
      .catch(() => setLoad("error"));
  }, []);

  const reload = useCallback(() => {
    setLoad("loading");
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const value = useMemo<AdminGateValue>(() => ({ reverify: reload }), [reload]);

  let content: ReactNode;
  if (load === "loading") {
    content = <Loading />;
  } else if (load === "error" || !mfa) {
    content = (
      <div className="flex flex-col items-start gap-4">
        <p role="alert">Can't load your admin access right now. Check your connection and try again.</p>
        <Button type="button" variant="outline" onClick={reload}>
          Try again
        </Button>
      </div>
    );
  } else if (!mfa.enrolled) {
    content = <MfaEnrolment onDone={reload} />;
  } else if (!mfa.verified) {
    content = <MfaVerification recoveryCodesLeft={mfa.recovery_codes_left} onVerified={setMfa} />;
  } else {
    content = <Outlet />;
  }

  return <AdminGateContext.Provider value={value}>{content}</AdminGateContext.Provider>;
}

/** Guards `/admin`: students (any role but admin) see the ordinary not-found page, and never make
 * an admin request. Admins must enrol in (or pass) TOTP multifactor before the reports pages
 * render behind `<Outlet />`. */
export function AdminGate() {
  const { state } = useAuth();
  if (state.status !== "signed-in") return null;
  if (state.user.role !== "admin") return <NotFoundPage />;
  return <MfaGate />;
}
