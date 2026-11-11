// A stand-in for src/lib/socket.ts: records emits, answers acks on demand, and can trigger
// "connect", "disconnect", "chat.message", "presence.changed" and the room/call events from a
// test. Mock the real module with it like this, directly inside the test file (vi.mock must be
// hoistable there):
//
//   vi.mock("@/lib/socket", async () => (await import("@/test/fake-socket")).fakeSocketModule);
import type {
  AppSocket,
  ChatSendAck,
  ChatSendPayload,
  PeerJoinedEvent,
  PeerLeftEvent,
  RoomAck,
  RoomJoinAck,
  RoomPayload,
  RoomRemovedEvent,
  RtcSignalEvent,
  RtcSignalPayload,
} from "@/lib/socket";
import type { Message } from "@/features/messages/api";

type Handler = (...args: never[]) => void;

interface PendingAck<TPayload, TAck> {
  payload: TPayload;
  resolve: (ack: TAck) => void;
}

type PendingSend = PendingAck<ChatSendPayload, ChatSendAck>;

let handlers = new Map<string, Set<Handler>>();
// Unresolved sends, waiting for a test to answer them with `resolveSend`.
let pending: PendingSend[] = [];
// Every payload ever sent, kept even after it's resolved, so a test can confirm a retry re-sent
// the same `client_msg_id` instead of minting a new one.
let sentLog: ChatSendPayload[] = [];

// Unresolved room.join / room.leave / rtc.signal, waiting for a test to answer them.
let pendingJoins: PendingAck<RoomPayload, RoomJoinAck>[] = [];
let pendingLeaves: PendingAck<RoomPayload, RoomAck>[] = [];
let pendingSignals: PendingAck<RtcSignalPayload, RoomAck>[] = [];

function on(event: string, handler: Handler): void {
  if (!handlers.has(event)) handlers.set(event, new Set());
  handlers.get(event)?.add(handler);
}

function off(event: string, handler: Handler): void {
  handlers.get(event)?.delete(handler);
}

function emitLocal(event: string, ...args: unknown[]): void {
  for (const handler of handlers.get(event) ?? []) (handler as (...a: unknown[]) => void)(...args);
}

/** Resets all recorded state; call from `afterEach` (or rely on the module being re-imported). */
export function resetFakeSocket(): void {
  handlers = new Map();
  pending = [];
  sentLog = [];
  pendingJoins = [];
  pendingLeaves = [];
  pendingSignals = [];
}

export const fakeSocket = {
  on,
  off,
  connect: () => undefined,
  disconnect: () => undefined,
} as unknown as AppSocket;

/** The replacement for `createSocket`/`emitChatSend`/the room emitters, passed as the `vi.mock`
 * factory result. */
export const fakeSocketModule = {
  createSocket: () => fakeSocket,
  emitChatSend: (_socket: AppSocket, payload: ChatSendPayload) =>
    new Promise<ChatSendAck>((resolve) => {
      sentLog.push(payload);
      pending.push({ payload, resolve });
    }),
  emitRoomJoin: (_socket: AppSocket, payload: RoomPayload) =>
    new Promise<RoomJoinAck>((resolve) => {
      pendingJoins.push({ payload, resolve });
    }),
  emitRoomLeave: (_socket: AppSocket, payload: RoomPayload) =>
    new Promise<RoomAck>((resolve) => {
      pendingLeaves.push({ payload, resolve });
    }),
  emitRtcSignal: (_socket: AppSocket, payload: RtcSignalPayload) =>
    new Promise<RoomAck>((resolve) => {
      pendingSignals.push({ payload, resolve });
    }),
};

export function triggerConnect(): void {
  emitLocal("connect");
}

export function triggerDisconnect(reason = "transport close"): void {
  emitLocal("disconnect", reason);
}

export function triggerConnectError(): void {
  emitLocal("connect_error", new Error("connect_error"));
}

export function triggerMessage(message: Message): void {
  emitLocal("chat.message", message);
}

export function triggerPresence(event: { user_id: string; online: boolean }): void {
  emitLocal("presence.changed", event);
}

/** Every `chat.send` currently waiting for an ack (a retry adds a second entry). */
export function pendingSendPayloads(): ChatSendPayload[] {
  return pending.map((entry) => entry.payload);
}

/** Every `chat.send` payload ever emitted for this `client_msg_id`, resolved or not. */
export function sentPayloadsFor(clientMsgId: string): ChatSendPayload[] {
  return sentLog.filter((payload) => payload.client_msg_id === clientMsgId);
}

/** Resolves the OLDEST still-pending `chat.send` for this `client_msg_id` with `ack`. */
export function resolveSend(clientMsgId: string, ack: ChatSendAck): void {
  const index = pending.findIndex((entry) => entry.payload.client_msg_id === clientMsgId);
  if (index === -1) throw new Error(`No pending chat.send for client_msg_id ${clientMsgId}`);
  const [entry] = pending.splice(index, 1);
  entry?.resolve(ack);
}

export function triggerPeerJoined(event: PeerJoinedEvent): void {
  emitLocal("room.peer_joined", event);
}

export function triggerPeerLeft(event: PeerLeftEvent): void {
  emitLocal("room.peer_left", event);
}

export function triggerRtcSignal(event: RtcSignalEvent): void {
  emitLocal("rtc.signal", event);
}

export function triggerRoomRemoved(event: RoomRemovedEvent): void {
  emitLocal("room.removed", event);
}

/** Every `room.join` currently waiting for an ack. */
export function pendingRoomJoinPayloads(): RoomPayload[] {
  return pendingJoins.map((entry) => entry.payload);
}

/** Every `room.leave` currently waiting for an ack. */
export function pendingRoomLeavePayloads(): RoomPayload[] {
  return pendingLeaves.map((entry) => entry.payload);
}

/** Every `rtc.signal` currently waiting for an ack. */
export function pendingRtcSignalPayloads(): RtcSignalPayload[] {
  return pendingSignals.map((entry) => entry.payload);
}

/** Resolves the OLDEST still-pending `room.join` with `ack`. */
export function resolveRoomJoin(ack: RoomJoinAck): void {
  const entry = pendingJoins.shift();
  if (!entry) throw new Error("No pending room.join to resolve");
  entry.resolve(ack);
}

/** Resolves the OLDEST still-pending `room.leave` with `ack`. */
export function resolveRoomLeave(ack: RoomAck): void {
  const entry = pendingLeaves.shift();
  if (!entry) throw new Error("No pending room.leave to resolve");
  entry.resolve(ack);
}

/** Resolves the OLDEST still-pending `rtc.signal` with `ack`. */
export function resolveRtcSignal(ack: RoomAck): void {
  const entry = pendingSignals.shift();
  if (!entry) throw new Error("No pending rtc.signal to resolve");
  entry.resolve(ack);
}
