import { apiFetch } from "@/lib/http";

// Mirrors apps/api/app/modules/scan/schemas.py.
export type ConnectionCodeStatus = "active" | "expired" | "off";

interface CodeBase {
  /** False when a scan by someone you aren't connected with would show nothing: no profile yet,
   * the profile is hidden from discovery, or the account can't be shown (unverified, suspended). */
  shareable: boolean;
}

/** Narrow on `status`: only an active code has a link. */
export type ConnectionCode =
  | (CodeBase & {
      status: "active";
      /** `${web origin}/connect#token=...`. The QR code encodes exactly this. */
      url: string;
      expires_at: string;
    })
  | (CodeBase & { status: "expired"; url: null; expires_at: string })
  | (CodeBase & { status: "off"; url: null; expires_at: null });

export type PreviewConnectionState = "none" | "pending_outgoing" | "pending_incoming" | "connected";

/** The minimum a scan reveals: who it is, and where the two of you stand. */
export interface ConnectPreview {
  user_id: string;
  display_name: string;
  headline: string;
  connection_state: PreviewConnectionState;
}

export const scanApi = {
  /** The signed-in student's own code. Never creates one: use `rotate` to start sharing. */
  myCode: () => apiFetch<ConnectionCode>("/me/connection-code"),
  /** Issues a new code, revoking the current one (also how sharing is turned on). */
  rotate: () => apiFetch<ConnectionCode>("/me/connection-code/rotate", { method: "POST" }),
  /** Turns sharing off: the current code stops working. */
  disable: () => apiFetch<void>("/me/connection-code", { method: "DELETE" }),
  /** Every failure (bad, expired, revoked, hidden, blocked, yourself) is the same 404. The token
   * goes in the body, never the URL, so no proxy or access log along the way records it. */
  resolve: (token: string) => apiFetch<ConnectPreview>("/connect/resolve", { method: "POST", json: { token } }),
};
