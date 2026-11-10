// A stand-in for src/lib/socket.ts: records emits, answers acks on demand, and can trigger
// "connect", "disconnect", "chat.message" and "presence.changed" from a test. Mock the real
// module with it like this, directly inside the test file (vi.mock must be hoistable there):
//
//   vi.mock("@/lib/socket", async () => (await import("@/test/fake-socket")).fakeSocketModule);
import type { AppSocket, ChatSendAck, ChatSendPayload } from "@/lib/socket";
import type { Message } from "@/features/messages/api";

type Handler = (...args: never[]) => void;

interface PendingSend {
  payload: ChatSendPayload;
  resolve: (ack: ChatSendAck) => void;
}

let handlers = new Map<string, Set<Handler>>();
// Unresolved sends, waiting for a test to answer them with `resolveSend`.
let pending: PendingSend[] = [];
// Every payload ever sent, kept even after it's resolved, so a test can confirm a retry re-sent
// the same `client_msg_id` instead of minting a new one.
let sentLog: ChatSendPayload[] = [];

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
}

export const fakeSocket = {
  on,
  off,
  connect: () => undefined,
  disconnect: () => undefined,
} as unknown as AppSocket;

/** The replacement for `createSocket`/`emitChatSend`, passed as the `vi.mock` factory result. */
export const fakeSocketModule = {
  createSocket: () => fakeSocket,
  emitChatSend: (_socket: AppSocket, payload: ChatSendPayload) =>
    new Promise<ChatSendAck>((resolve) => {
      sentLog.push(payload);
      pending.push({ payload, resolve });
    }),
};

export function triggerConnect(): void {
  emitLocal("connect");
}

export function triggerDisconnect(reason = "transport close"): void {
  emitLocal("disconnect", reason);
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
