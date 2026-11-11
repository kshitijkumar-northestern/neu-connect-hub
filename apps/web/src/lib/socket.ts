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

/** Another student in a call. Peers are addressed by `user_id` (one socket per student per room). */
export interface Peer {
  user_id: string;
  display_name: string | null;
}

/** Codes a room event's ack can carry (apps/api's realtime rooms module), plus a client-only
 * "timeout" for when no ack arrives at all. */
export type RoomErrorCode =
  | "not_found"
  | "room_ended"
  | "room_full"
  | "not_in_room"
  | "peer_not_found"
  | "validation_error"
  | "rate_limited"
  | "unauthorized"
  | "internal_error"
  | "timeout";

export type RoomError = { ok: false; error: { code: RoomErrorCode; message: string } };

/** WebRTC negotiation relayed by the server, untouched, between two students in the same room. */
export type RtcSignal =
  | { type: "offer" | "answer"; sdp: string }
  | {
      type: "candidate";
      candidate: { candidate: string; sdpMid: string | null; sdpMLineIndex: number | null; usernameFragment?: string | null };
    };

export interface RoomPayload {
  room_id: string;
}

/** On success, `peers` are the students already in the room: the joiner sends each an offer. */
export type RoomJoinAck = { ok: true; room_id: string; peers: Peer[] } | RoomError;
export type RoomAck = { ok: true } | RoomError;

export interface RtcSignalPayload {
  room_id: string;
  /** The receiving peer's user_id. */
  to: string;
  signal: RtcSignal;
}

export interface PeerJoinedEvent {
  room_id: string;
  peer: Peer;
}

export interface PeerLeftEvent {
  room_id: string;
  user_id: string;
}

/** Sent to a student's socket when the server takes it out of a call: they lost access (a block,
 * or a removal from the team), or they joined the same call from another tab, which takes over. */
export interface RoomRemovedEvent {
  room_id: string;
  reason: "access_lost" | "joined_elsewhere";
}

export interface RtcSignalEvent {
  room_id: string;
  /** The sending peer's user_id. */
  from: string;
  signal: RtcSignal;
}

interface ServerToClientEvents {
  "chat.message": (message: Message) => void;
  "presence.changed": (event: PresenceEvent) => void;
  "room.peer_joined": (event: PeerJoinedEvent) => void;
  "room.peer_left": (event: PeerLeftEvent) => void;
  "room.removed": (event: RoomRemovedEvent) => void;
  "rtc.signal": (event: RtcSignalEvent) => void;
}

interface ClientToServerEvents {
  "chat.send": (payload: ChatSendPayload, ack: (response: ChatSendAck) => void) => void;
  "room.join": (payload: RoomPayload, ack: (response: RoomJoinAck) => void) => void;
  "room.leave": (payload: RoomPayload, ack: (response: RoomAck) => void) => void;
  "rtc.signal": (payload: RtcSignalPayload, ack: (response: RoomAck) => void) => void;
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

/** Room events wait this long for an ack before resolving with a "timeout" error. */
export const ROOM_ACK_TIMEOUT_MS = 10_000;

const ROOM_TIMEOUT: RoomError = { ok: false, error: { code: "timeout", message: "The server didn't respond in time." } };

/** Resolves with the server's ack, or with `fallback` once `timeoutMs` passes without one. */
function ackOrTimeout<T>(register: (done: (response: T) => void) => void, fallback: T, timeoutMs: number): Promise<T> {
  return new Promise((resolve) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      resolve(fallback);
    }, timeoutMs);
    register((response) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(response);
    });
  });
}

export function emitRoomJoin(socket: AppSocket, payload: RoomPayload): Promise<RoomJoinAck> {
  return ackOrTimeout<RoomJoinAck>((done) => socket.emit("room.join", payload, done), ROOM_TIMEOUT, ROOM_ACK_TIMEOUT_MS);
}

export function emitRoomLeave(socket: AppSocket, payload: RoomPayload): Promise<RoomAck> {
  return ackOrTimeout<RoomAck>((done) => socket.emit("room.leave", payload, done), ROOM_TIMEOUT, ROOM_ACK_TIMEOUT_MS);
}

export function emitRtcSignal(socket: AppSocket, payload: RtcSignalPayload): Promise<RoomAck> {
  return ackOrTimeout<RoomAck>((done) => socket.emit("rtc.signal", payload, done), ROOM_TIMEOUT, ROOM_ACK_TIMEOUT_MS);
}
