import { apiFetch } from "@/lib/http";

// Mirrors the Literal unions in apps/api/app/modules/connections/schemas.py.
export type ConnectionState = "pending" | "accepted";
export type ConnectionDirection = "incoming" | "outgoing";

/** `user_id` and `display_name` are always the OTHER student; `direction` is relative to the
 * caller signed in. */
export interface Connection {
  id: string;
  state: ConnectionState;
  direction: ConnectionDirection;
  user_id: string;
  display_name: string | null;
  created_at: string;
  responded_at: string | null;
  conversation_id: string | null;
}

export const connectionsApi = {
  /** Only pending and accepted connections, newest first. */
  list: () => apiFetch<Connection[]>("/connections"),
  request: (userId: string) => apiFetch<Connection>("/connections", { method: "POST", json: { user_id: userId } }),
  accept: (id: string) => apiFetch<Connection>(`/connections/${id}/accept`, { method: "POST" }),
  decline: (id: string) => apiFetch<void>(`/connections/${id}/decline`, { method: "POST" }),
  withdraw: (id: string) => apiFetch<void>(`/connections/${id}/withdraw`, { method: "POST" }),
};
