import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CHAT_SEND_TIMEOUT_MS, emitChatSend, type AppSocket, type ChatSendPayload } from "@/lib/socket";

const PAYLOAD: ChatSendPayload = {
  conversation_id: "11111111-1111-4111-8111-111111111111",
  client_msg_id: "22222222-2222-4222-8222-222222222222",
  body: "hello",
};

function fakeRawSocket() {
  return { emit: vi.fn() } as unknown as AppSocket;
}

describe("emitChatSend", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("emits chat.send and resolves with the server's ack", async () => {
    const socket = fakeRawSocket();
    const message = {
      id: "m1",
      conversation_id: PAYLOAD.conversation_id,
      author_id: "u1",
      body: "hello",
      client_msg_id: PAYLOAD.client_msg_id,
      created_at: "2026-11-10T00:00:00Z",
      removed: false,
    };
    (socket.emit as ReturnType<typeof vi.fn>).mockImplementation((_event, _payload, ack) => {
      ack({ ok: true, message });
    });

    const result = await emitChatSend(socket, PAYLOAD);

    expect(socket.emit).toHaveBeenCalledWith("chat.send", PAYLOAD, expect.any(Function));
    expect(result).toEqual({ ok: true, message });
  });

  it("resolves with a failed ack from the server", async () => {
    const socket = fakeRawSocket();
    (socket.emit as ReturnType<typeof vi.fn>).mockImplementation((_event, _payload, ack) => {
      ack({ ok: false, error: { code: "rate_limited", message: "Slow down." } });
    });

    const result = await emitChatSend(socket, PAYLOAD);

    expect(result).toEqual({ ok: false, error: { code: "rate_limited", message: "Slow down." } });
  });

  it("times out as a failed ack when the server never answers", async () => {
    const socket = fakeRawSocket(); // emit() never calls the ack callback

    const pending = emitChatSend(socket, PAYLOAD);
    await vi.advanceTimersByTimeAsync(CHAT_SEND_TIMEOUT_MS);
    const result = await pending;

    expect(result).toEqual({ ok: false, error: { code: "timeout", message: expect.any(String) } });
  });

  it("ignores a late ack after it has already timed out", async () => {
    const socket = fakeRawSocket();
    let lateAck: ((response: unknown) => void) | undefined;
    (socket.emit as ReturnType<typeof vi.fn>).mockImplementation((_event, _payload, ack) => {
      lateAck = ack;
    });

    const pending = emitChatSend(socket, PAYLOAD);
    await vi.advanceTimersByTimeAsync(CHAT_SEND_TIMEOUT_MS);
    const result = await pending;
    lateAck?.({ ok: true, message: { id: "late" } });

    expect(result).toEqual({ ok: false, error: { code: "timeout", message: expect.any(String) } });
  });
});
