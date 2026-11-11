import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { vi } from "vitest";

import { CONNECT_TIMEOUT_MS } from "@/features/rooms/pages/RoomPage";
import { CONNECT_ERROR_MESSAGE } from "@/features/rooms/errors";
import type { RoomErrorCode } from "@/lib/socket";
import { ADA, apiError, fakeApi, type Route } from "@/test/fake-api";
import {
  pendingRoomJoinPayloads,
  pendingRoomLeavePayloads,
  resetFakeSocket,
  resolveRoomJoin,
  triggerConnect,
  triggerConnectError,
  triggerDisconnect,
  triggerPeerJoined,
  triggerPeerLeft,
  triggerRoomRemoved,
} from "@/test/fake-socket";
import { FakeMediaStream, installFakeRTCPeerConnection, stubGetUserMedia } from "@/test/fake-webrtc";
import { renderApp } from "@/test/render-app";

vi.mock("@/lib/socket", async () => (await import("@/test/fake-socket")).fakeSocketModule);

const ROOM_ID = "11111111-1111-4111-8111-111111111112";
const CONVERSATION_ID = "conv-1";
const OTHER_ID = "22222222-2222-4222-8222-222222222222";
const THIRD_ID = "33333333-3333-4333-8333-333333333333";

const ROOM = {
  id: ROOM_ID,
  conversation_id: CONVERSATION_ID,
  kind: "direct" as const,
  name: "Grace Hopper",
  project_id: null,
  room_state: "active" as const,
  capacity: 4,
  participants: [{ user_id: OTHER_ID, display_name: "Grace Hopper" }],
  created_at: "2026-11-05T00:00:00Z",
  ended_at: null,
};

const ICE_SERVERS = { ice_servers: [{ urls: ["stun:stun.example.com"] }], expires_at: null };

function baseRoutes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "GET /me": [200, ADA],
    [`GET /rooms/${ROOM_ID}`]: [200, ROOM],
    [`GET /rooms/${ROOM_ID}/ice-servers`]: [200, ICE_SERVERS],
    ...overrides,
  };
}

let peerConnections: ReturnType<typeof installFakeRTCPeerConnection>;

beforeEach(() => {
  peerConnections = installFakeRTCPeerConnection();
});

afterEach(() => {
  peerConnections.uninstall();
  resetFakeSocket();
});

/** The page fetches ICE servers (an awaited fetch) before it opens the call socket and starts
 * waiting for "connect", so a fresh `room.join` doesn't land in the fake's queue the instant
 * "Join call" is clicked. Retries "connect" until the join actually lands, rather than guessing a
 * fixed number of microtask ticks. */
async function awaitJoinAttempt(): Promise<void> {
  await waitFor(() => {
    triggerConnect();
    expect(pendingRoomJoinPayloads().length).toBeGreaterThan(0);
  });
}

/** Fires a fake-socket event and lets the resulting state updates (which land via a microtask,
 * not synchronously under the triggering call) actually commit before the next assertion, inside
 * an `act()` React recognises as asynchronous. */
async function flushAct(trigger: () => void): Promise<void> {
  await act(async () => {
    trigger();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("RoomPage", () => {
  it("shows the neutral message for a call the student can't access", async () => {
    fakeApi({ "GET /me": [200, ADA], [`GET /rooms/${ROOM_ID}`]: apiError(404, "not_found") });
    renderApp(`/rooms/${ROOM_ID}`);

    expect(await screen.findByText("This call isn't available.")).toBeInTheDocument();
  });

  it("shows the ended message with a link back to the conversation", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /rooms/${ROOM_ID}`]: [200, { ...ROOM, room_state: "ended", ended_at: "2026-11-05T01:00:00Z" }],
    });
    renderApp(`/rooms/${ROOM_ID}`);

    expect(await screen.findByText("This call has ended.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the conversation" })).toHaveAttribute(
      "href",
      `/messages/${CONVERSATION_ID}`,
    );
  });

  it("shows the lobby with the room name and how many are in the call", async () => {
    fakeApi(baseRoutes());
    renderApp(`/rooms/${ROOM_ID}`);

    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByText("1 of 4 in the call")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Join call" })).toBeInTheDocument();
  });

  it("requests media only once Check camera and microphone is pressed", async () => {
    const getUserMedia = stubGetUserMedia();
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);
    await screen.findByRole("button", { name: "Join call" });
    expect(getUserMedia).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Check camera and microphone" }));

    await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({ audio: true, video: true }));
    expect(await screen.findByText("You")).toBeInTheDocument();
  });

  it("falls back to receive-only when permission is denied, and still allows joining", async () => {
    stubGetUserMedia(new Error("Permission denied"));
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    expect(
      await screen.findByText("We can't use your camera or microphone. You can still join and listen."),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    expect(pendingRoomJoinPayloads()).toEqual([{ room_id: ROOM_ID }]);
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));

    expect(await screen.findByRole("button", { name: "Leave call" })).toBeInTheDocument();
    expect(screen.getByText("You")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Microphone" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Camera" })).toBeDisabled();
  });

  it("allows joining without ever checking devices (receive-only)", async () => {
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));

    expect(await screen.findByRole("button", { name: "Leave call" })).toBeInTheDocument();
  });

  it("joins the call naming tiles from the ack and from a later peer_joined, and drops one on peer_left", async () => {
    stubGetUserMedia();
    // A distinct room name: the in-call header shows it too, and this test's peer happens to
    // share the fixture's usual name ("Grace Hopper") — keep that collision out of this test.
    fakeApi(baseRoutes({ [`GET /rooms/${ROOM_ID}`]: [200, { ...ROOM, name: "Direct call" }] }));
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    await screen.findByText("You");
    await user.click(screen.getByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() =>
      resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [{ user_id: OTHER_ID, display_name: "Grace Hopper" }] }),
    );

    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();

    await flushAct(() => triggerPeerJoined({ room_id: ROOM_ID, peer: { user_id: THIRD_ID, display_name: null } }));
    expect(await screen.findByText("A student")).toBeInTheDocument();

    await flushAct(() => triggerPeerLeft({ room_id: ROOM_ID, user_id: OTHER_ID }));
    await waitFor(() => expect(screen.queryByText("Grace Hopper")).not.toBeInTheDocument());
    expect(screen.getByText("A student")).toBeInTheDocument();
  });

  it("flips track.enabled and aria-pressed when the mic and camera toggles are pressed", async () => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    const micButton = await screen.findByRole("button", { name: "Microphone" });
    const cameraButton = screen.getByRole("button", { name: "Camera" });
    expect(micButton).toHaveAttribute("aria-pressed", "true");
    expect(cameraButton).toHaveAttribute("aria-pressed", "true");

    await user.click(micButton);
    expect(micButton).toHaveAttribute("aria-pressed", "false");
    expect(stream.getAudioTracks().every((track) => !track.enabled)).toBe(true);
    expect(stream.getVideoTracks().every((track) => track.enabled)).toBe(true);

    await user.click(cameraButton);
    expect(cameraButton).toHaveAttribute("aria-pressed", "false");
    expect(stream.getVideoTracks().every((track) => !track.enabled)).toBe(true);
  });

  it("Leave call stops every track, emits room.leave and navigates back to the conversation", async () => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    const { router } = renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    await user.click(screen.getByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));
    await screen.findByRole("button", { name: "Leave call" });

    await user.click(screen.getByRole("button", { name: "Leave call" }));

    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
    expect(pendingRoomLeavePayloads()).toEqual([{ room_id: ROOM_ID }]);
    await waitFor(() => expect(router.state.location.pathname).toBe(`/messages/${CONVERSATION_ID}`));
  });

  it("emits room.leave and stops every track on unmount while in a call", async () => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    const view = renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));
    await screen.findByRole("button", { name: "Leave call" });

    view.unmount();

    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
    expect(pendingRoomLeavePayloads()).toEqual([{ room_id: ROOM_ID }]);
  });

  it("stops every track on unmount before ever joining", async () => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    const view = renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    await screen.findByText("You");

    view.unmount();

    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
  });

  it("shows Connection lost with both Rejoin and Leave call after a socket drop, and Rejoin reconnects", async () => {
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));
    await screen.findByRole("button", { name: "Leave call" });

    await flushAct(() => triggerDisconnect());
    expect(await screen.findByText("Connection lost.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Rejoin" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave call" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Rejoin" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));

    expect(await screen.findByRole("button", { name: "Leave call" })).toBeInTheDocument();
  });

  it("merges a room.peer_joined that arrives before the join ack with the ack's own peers", async () => {
    // A distinct room name: the in-call header would otherwise collide with the fixture's usual
    // peer name ("Grace Hopper").
    fakeApi(baseRoutes({ [`GET /rooms/${ROOM_ID}`]: [200, { ...ROOM, name: "Direct call" }] }));
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();

    // Arrives while the ack is still in flight: the mesh and the handler must already exist.
    await flushAct(() => triggerPeerJoined({ room_id: ROOM_ID, peer: { user_id: THIRD_ID, display_name: "Katherine Johnson" } }));
    // The ack lists a DIFFERENT peer only; a naive `setPeers(ack.peers)` would wipe out the early
    // arrival instead of merging it in.
    await flushAct(() =>
      resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [{ user_id: OTHER_ID, display_name: "Grace Hopper" }] }),
    );

    expect(await screen.findByText("Katherine Johnson")).toBeInTheDocument();
    expect(screen.getByText("Grace Hopper")).toBeInTheDocument();
  });

  it("cleans up a join attempt abandoned by unmounting during the ICE-servers fetch", async () => {
    let resolveIce!: () => void;
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /rooms/${ROOM_ID}`]: [200, ROOM],
      [`GET /rooms/${ROOM_ID}/ice-servers`]: () =>
        new Promise((resolve) => {
          resolveIce = () => resolve([200, ICE_SERVERS]);
        }),
    });
    const user = userEvent.setup();
    const view = renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await screen.findByText("Connecting…"); // the ICE-servers fetch is still in flight

    view.unmount();
    await act(async () => {
      resolveIce();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    // If the attempt wrongly proceeded (the ghost-call bug), it would now be creating a socket and
    // waiting on "connect"; feed it one to see whether it goes on to join with nothing left to
    // ever disconnect it.
    await act(async () => {
      triggerConnect();
      await Promise.resolve();
      await Promise.resolve();
    });

    // An abandoned attempt must notice and stop before ever opening a socket or joining: nothing
    // is left transmitting with no one left to disconnect it.
    expect(pendingRoomJoinPayloads()).toEqual([]);
  });

  it("shows an error and returns to the lobby on a connect_error", async () => {
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await waitFor(() => {
      triggerConnectError();
      expect(screen.queryByText(CONNECT_ERROR_MESSAGE)).toBeInTheDocument();
    });

    expect(screen.getByRole("button", { name: "Join call" })).toBeInTheDocument();
  });

  it("shows an error and returns to the lobby when the connect wait times out", async () => {
    fakeApi(baseRoutes());
    renderApp(`/rooms/${ROOM_ID}`);
    const joinButton = await screen.findByRole("button", { name: "Join call" });

    vi.useFakeTimers();
    try {
      fireEvent.click(joinButton);
      // Lets the ICE-servers fetch's microtasks settle before the connect wait's own timer is
      // even created, without advancing (fake) real time.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(CONNECT_TIMEOUT_MS);
      });

      expect(screen.getByText("Couldn't reach the server in time.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Join call" })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops the previous stream's tracks before replacing it when Check camera and microphone is pressed again", async () => {
    const firstStream = new FakeMediaStream();
    const secondStream = new FakeMediaStream();
    let calls = 0;
    stubGetUserMedia(() => (calls++ === 0 ? firstStream : secondStream));
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    const checkButton = await screen.findByRole("button", { name: "Check camera and microphone" });
    await user.click(checkButton);
    await screen.findByText("You");
    expect(firstStream.getTracks().every((track) => track.readyState === "live")).toBe(true);

    await user.click(checkButton);

    await waitFor(() => expect(firstStream.getTracks().every((track) => track.readyState === "ended")).toBe(true));
    expect(secondStream.getTracks().every((track) => track.readyState === "live")).toBe(true);
  });

  it("shows the room name and capacity in the in-call header", async () => {
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));

    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByText("1 of 4 in the call")).toBeInTheDocument();
  });

  it("updates the in-call count live as peers join and leave, instead of the count from when the page loaded", async () => {
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));
    expect(await screen.findByText("1 of 4 in the call")).toBeInTheDocument(); // just "You"

    await flushAct(() => triggerPeerJoined({ room_id: ROOM_ID, peer: { user_id: THIRD_ID, display_name: "Katherine Johnson" } }));
    expect(await screen.findByText("2 of 4 in the call")).toBeInTheDocument();

    await flushAct(() => triggerPeerLeft({ room_id: ROOM_ID, user_id: THIRD_ID }));
    expect(await screen.findByText("1 of 4 in the call")).toBeInTheDocument();
  });

  const ROOM_REMOVED_CASES: Array<["access_lost" | "joined_elsewhere", string]> = [
    ["access_lost", "You no longer have access to this call."],
    ["joined_elsewhere", "You joined this call in another tab or window."],
  ];

  it.each(ROOM_REMOVED_CASES)("tears the call down and shows the copy for room.removed reason %s", async (reason, message) => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Check camera and microphone" }));
    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: true, room_id: ROOM_ID, peers: [] }));
    await screen.findByRole("button", { name: "Leave call" });

    await flushAct(() => triggerRoomRemoved({ room_id: ROOM_ID, reason }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to the conversation" })).toHaveAttribute(
      "href",
      `/messages/${CONVERSATION_ID}`,
    );
    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
    expect(screen.queryByRole("button", { name: "Rejoin" })).not.toBeInTheDocument();
  });

  it("shows the ended message if the room ends between loading and joining", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /rooms/${ROOM_ID}`]: [200, ROOM],
      [`GET /rooms/${ROOM_ID}/ice-servers`]: apiError(409, "room_ended"),
    });
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));

    expect(await screen.findByText("This call has ended.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Join call" })).toBeInTheDocument();
  });

  const ACK_ERRORS: Array<[RoomErrorCode, string]> = [
    ["room_full", "This call is full."],
    ["not_found", "This call isn't available."],
    ["room_ended", "This call has ended."],
    ["rate_limited", "Too many attempts. Wait a moment and try again."],
    ["timeout", "Couldn't reach the server in time."],
    ["internal_error", "Something went wrong. Please try again."],
  ];

  it.each(ACK_ERRORS)("shows the copy for a %s room.join ack error", async (code, message) => {
    fakeApi(baseRoutes());
    const user = userEvent.setup();
    renderApp(`/rooms/${ROOM_ID}`);

    await user.click(await screen.findByRole("button", { name: "Join call" }));
    await awaitJoinAttempt();
    await flushAct(() => resolveRoomJoin({ ok: false, error: { code, message: "ignored" } }));

    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Join call" })).toBeInTheDocument();
  });
});
