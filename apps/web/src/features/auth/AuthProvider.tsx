import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { authApi, type User } from "@/features/auth/api";
import { ApiError } from "@/lib/http";

export type AuthState =
  | { status: "loading" }
  | { status: "signed-out" }
  | { status: "signed-in"; user: User }
  /** /me failed for a reason other than "no session" (offline, API down): we don't know yet. */
  | { status: "unavailable" };

interface AuthContextValue {
  state: AuthState;
  /** Signs in and updates the state; throws ApiError on failure. */
  signIn: (email: string, password: string) => Promise<User>;
  /** Always ends up signed out, even if the request fails (e.g. offline). */
  signOut: () => Promise<void>;
  /** Re-reads /me, e.g. after verifying the email address. */
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function loadMe(): Promise<AuthState> {
  try {
    return { status: "signed-in", user: await authApi.me() };
  } catch (error) {
    return error instanceof ApiError && error.status === 401 ? { status: "signed-out" } : { status: "unavailable" };
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  // Bumped by every change; a /me answer only applies if nothing changed while it was in flight,
  // so a slow first /me can't sign out a student who signed in meanwhile.
  const version = useRef(0);

  const settle = useCallback((next: AuthState) => {
    version.current += 1;
    setState(next);
  }, []);

  const refresh = useCallback(async () => {
    const asked = ++version.current;
    const next = await loadMe();
    if (asked === version.current) setState(next);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const signIn = useCallback(
    async (email: string, password: string) => {
      const user = await authApi.login(email, password);
      settle({ status: "signed-in", user });
      return user;
    },
    [settle],
  );

  const signOut = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // The session cookie may already be gone; locally we are signed out either way.
    } finally {
      settle({ status: "signed-out" });
    }
  }, [settle]);

  const value = useMemo(() => ({ state, signIn, signOut, refresh }), [state, signIn, signOut, refresh]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
