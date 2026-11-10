// The only module that imports socket.io-client directly, so tests can replace this whole module
// with a fake emitter (see src/test/fake-socket.ts) instead of mocking the real network/transport.
import { io, type Socket } from "socket.io-client";

import { config } from "@/lib/config";
import type { Message } from "@/features/messages/api";

export interface ChatSendPayload {
  conversation_id: string;
  /** A `crypto.randomUUID()` the client generates; re-sending the same id is a no-op server-side,
   * so a retry after a dropped ack (or a reconnect) never creates a duplicate message. */
  client_msg_id: string;
  body: string;
}

/** Codes the server's `chat.send` ack can carry (apps/api's chat module), plus a client-only
 * "timeout" for when no ack arrives at all (never sent by the server). */
export type ChatSendErrorCode = "not_found" | "validation_error" | "rate_limited" | "unauthorized" | "timeout";

export type ChatSendAck =
  | { ok: true; message: Message }
  | { ok: false; error: { code: ChatSendErrorCode; message: string } };

export interface PresenceEvent {
  user_id: string;
  online: boolean;
}

interface ServerToClientEvents {
  "chat.message": (message: Message) => void;
  "presence.changed": (event: PresenceEvent) => void;
}

interface ClientToServerEvents {
  "chat.send": (payload: ChatSendPayload, ack: (response: ChatSendAck) => void) => void;
}

export type AppSocket = Socket<ServerToClientEvents, ClientToServerEvents>;

/** A slow ack counts as a failed send, so the composer can offer Retry instead of hanging. */
export const CHAT_SEND_TIMEOUT_MS = 10_000;

/** A disconnected socket (call `.connect()` to open it): same origin by default, authenticated by
 * the session cookie rather than a token, and only ever opened while signed in. */
export function createSocket(): AppSocket {
  return io(config.socketUrl || undefined, {
    path: "/socket.io",
    withCredentials: true,
    autoConnect: false,
    transports: ["websocket", "polling"],
  });
}

/** `chat.send` with a client-side timeout. Resolves (never rejects) so callers can treat every
 * outcome, including a timeout, as a plain ack to render. */
export function emitChatSend(socket: AppSocket, payload: ChatSendPayload): Promise<ChatSendAck> {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      resolve({ ok: false, error: { code: "timeout", message: "The server didn't respond in time." } });
    }, CHAT_SEND_TIMEOUT_MS);
    // A genuine server ack always wins the race; the timer above only fires the fallback.
    socket.emit("chat.send", payload, (response) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(response);
    });
  });
}
