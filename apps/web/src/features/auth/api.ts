import { apiFetch } from "@/lib/http";

/** Mirrors UserResponse in apps/api/app/modules/auth/schemas.py. */
export interface User {
  id: string;
  email: string;
  role: "student" | "admin";
  verified: boolean;
  created_at: string;
}

/** Mirrors PASSWORD_MIN_LENGTH / PASSWORD_MAX_LENGTH in apps/api/app/modules/auth/schemas.py. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;

export const authApi = {
  me: () => apiFetch<User>("/me"),
  register: (email: string, password: string) =>
    apiFetch<void>("/auth/register", { method: "POST", json: { email, password } }),
  resendVerification: (email: string) =>
    apiFetch<void>("/auth/resend-verification", { method: "POST", json: { email } }),
  verifyEmail: (token: string) => apiFetch<void>("/auth/verify", { method: "POST", json: { token } }),
  login: (email: string, password: string) =>
    apiFetch<User>("/auth/login", { method: "POST", json: { email, password } }),
  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
  requestPasswordReset: (email: string) =>
    apiFetch<void>("/auth/password-reset/request", { method: "POST", json: { email } }),
  confirmPasswordReset: (token: string, password: string) =>
    apiFetch<void>("/auth/password-reset/confirm", { method: "POST", json: { token, password } }),
  /** Erases the account, its profile, blocks and sessions; reports it filed stay, unattributed. */
  deleteAccount: (password: string) => apiFetch<void>("/me", { method: "DELETE", json: { password } }),
};
