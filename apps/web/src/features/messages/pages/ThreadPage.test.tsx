import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ADA, apiError, fakeApi, type Reply } from "@/test/fake-api";
import { pendingSendPayloads, resetFakeSocket, resolveSend, sentPayloadsFor, triggerConnect, triggerDisconnect, triggerMessage, triggerPresence } from "@/test/fake-socket";
import { renderApp } from "@/test/render-app";

vi.mock("@/lib/socket", async () => (await import("@/test/fake-socket")).fakeSocketModule);

const OTHER_ID = "22222222-2222-4222-8222-222222222222";
const CONVERSATION_ID = "conv-1";
const PROJECT_ID = "44444444-4444-4444-8444-444444444444";
const TEAM_CONVERSATION_ID = "conv-team";
const TEAMMATE_ID = "33333333-3333-4333-8333-333333333333";

const CONVERSATION = {
  id: CONVERSATION_ID,
  kind: "direct",
  name: null,
  project_id: null,
  other: { user_id: OTHER_ID, display_name: "Grace Hopper", online: true },
  last_message: null,
  updated_at: "2026-11-05T00:00:00Z",
};

const TEAM_CONVERSATION = {
  id: TEAM_CONVERSATION_ID,
  kind: "project",
  name: "Campus Tutor Finder",
  project_id: PROJECT_ID,
  other: null,
  last_message: null,
  updated_at: "2026-11-05T00:00:00Z",
};

const MSG_MINE = {
  id: "m1",
  conversation_id: CONVERSATION_ID,
  author_id: ADA.id,
  author_name: "Ada Lovelace",
  body: "Hi!",
  client_msg_id: null,
  created_at: "2026-11-05T00:00:00Z",
  removed: false,
};

const MSG_THEIRS = {
  id: "m2",
  conversation_id: CONVERSATION_ID,
  author_id: OTHER_ID,
  author_name: "Grace Hopper",
  body: "Hello back",
  client_msg_id: null,
  created_at: "2026-11-05T00:01:00Z",
  removed: false,
};

const MSG_REMOVED = {
  id: "m3",
  conversation_id: CONVERSATION_ID,
  author_id: null,
  author_name: null,
  body: null,
  client_msg_id: null,
  created_at: "2026-11-05T00:02:00Z",
  removed: true,
};

function messagesRoute(page: { messages: unknown[]; before: string | null; after: string | null }): Reply {
  return [200, page];
}

afterEach(() => {
  resetFakeSocket();
});

describe("ThreadPage", () => {
  it("loads the latest page, labelling your own messages and showing removed ones", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({
        messages: [MSG_MINE, MSG_THEIRS, MSG_REMOVED],
        before: null,
        after: "cursor-1",
      }),
    });
    renderApp(`/messages/${CONVERSATION_ID}`);

    expect(await screen.findByRole("link", { name: "Grace Hopper" })).toHaveAttribute(
      "href",
      `/people/${OTHER_ID}`,
    );
    expect(screen.getByRole("log")).toBeInTheDocument();
    expect(screen.getByText("Hi!")).toBeInTheDocument();
    expect(screen.getByText(/^You ·/)).toBeInTheDocument();
    expect(screen.getByText("Hello back")).toBeInTheDocument();
    expect(screen.getByText("Message removed")).toBeInTheDocument();
  });

  it("keeps the line breaks a student typed", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({
        messages: [{ ...MSG_THEIRS, body: "Yes!\nEvenings work for me." }],
        before: null,
        after: "cursor-1",
      }),
    });
    renderApp(`/messages/${CONVERSATION_ID}`);

    const body = await screen.findByText((_, element) => element?.textContent === "Yes!\nEvenings work for me.");
    expect(body).toHaveClass("whitespace-pre-wrap");
  });

  it("shows whether the other student is online, live", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, { ...CONVERSATION, other: { ...CONVERSATION.other, online: false } }],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
    });
    renderApp(`/messages/${CONVERSATION_ID}`);

    await screen.findByRole("link", { name: "Grace Hopper" });
    expect(screen.queryByText("online")).not.toBeInTheDocument();
    act(() => triggerPresence({ user_id: OTHER_ID, online: true }));
    expect(await screen.findByText("online")).toBeInTheDocument();
  });

  it("shows the neutral message for a conversation the student can't access", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": apiError(404, "not_found"),
      [`GET /conversations/${CONVERSATION_ID}/messages`]: apiError(404, "not_found"),
    });
    renderApp(`/messages/${CONVERSATION_ID}`);

    expect(await screen.findByText("This conversation isn't available.")).toBeInTheDocument();
  });

  it("pages in older messages with Load earlier and keeps them merged", async () => {
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: (call) =>
        call.search.includes("before=")
          ? [200, { messages: [MSG_MINE], before: null, after: "cursor-after-1" }]
          : [200, { messages: [MSG_THEIRS], before: "cursor-before-1", after: "cursor-after-1" }],
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);

    await screen.findByText("Hello back");
    await user.click(screen.getByRole("button", { name: "Load earlier messages" }));

    expect(await screen.findByText("Hi!")).toBeInTheDocument();
    const earlierCalls = api
      .callsTo(`GET /conversations/${CONVERSATION_ID}/messages`)
      .filter((call) => call.search.includes("before="));
    expect(earlierCalls).toHaveLength(1);
    expect(earlierCalls[0]?.search).toBe("?before=cursor-before-1");
    expect(screen.queryByRole("button", { name: "Load earlier messages" })).not.toBeInTheDocument();
  });

  it("sends a message optimistically, then replaces it with the acked message", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);
    await screen.findByRole("log");

    act(() => triggerConnect());
    await user.type(screen.getByLabelText("Message"), "New message");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("New message")).toBeInTheDocument();
    expect(screen.getByText("Sending…")).toBeInTheDocument();

    const [payload] = pendingSendPayloads();
    expect(payload?.body).toBe("New message");
    const acked = {
      id: "server-id-1",
      conversation_id: CONVERSATION_ID,
      author_id: ADA.id,
      author_name: "Ada Lovelace",
      body: "New message",
      client_msg_id: payload?.client_msg_id ?? null,
      created_at: "2026-11-05T01:00:00Z",
      removed: false,
    };
    act(() => resolveSend(payload!.client_msg_id, { ok: true, message: acked }));

    await waitFor(() => expect(screen.queryByText("Sending…")).not.toBeInTheDocument());
    expect(screen.getByText("New message")).toBeInTheDocument();

    // A later echo of the same message (by id) over the socket must not duplicate it.
    act(() => triggerMessage(acked));
    expect(screen.getAllByText("New message")).toHaveLength(1);
  });

  it("shows Not sent on a failed ack and lets Retry resend the same client_msg_id", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);
    await screen.findByRole("log");

    act(() => triggerConnect());
    await user.type(screen.getByLabelText("Message"), "Will fail");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const [payload] = pendingSendPayloads();
    act(() => resolveSend(payload!.client_msg_id, { ok: false, error: { code: "rate_limited", message: "Slow down." } }));

    expect(await screen.findByText("Not sent")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));

    const attempts = sentPayloadsFor(payload!.client_msg_id);
    expect(attempts).toHaveLength(2);
    expect(attempts[0]?.client_msg_id).toBe(attempts[1]?.client_msg_id);
  });

  it("keeps a send pending while offline, then resends it after a reconnect catches up", async () => {
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: (call) =>
        call.search.includes("after=")
          ? [200, { messages: [MSG_THEIRS], before: null, after: "cursor-2" }]
          : [200, { messages: [], before: null, after: "cursor-1" }],
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);
    await screen.findByRole("log");

    act(() => triggerConnect()); // the socket's first-ever connection
    act(() => triggerDisconnect());
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Message"), "Sent while offline");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Sending…")).toBeInTheDocument();
    expect(pendingSendPayloads()).toHaveLength(0); // nothing emitted yet: still offline

    act(() => triggerConnect()); // a reconnect this time

    const catchUpCalls = () =>
      api.callsTo(`GET /conversations/${CONVERSATION_ID}/messages`).filter((call) => call.search.includes("after="));
    await waitFor(() => expect(catchUpCalls()).toHaveLength(1));
    expect(catchUpCalls()[0]?.search).toBe("?after=cursor-1");
    expect(await screen.findByText("Hello back")).toBeInTheDocument();

    await waitFor(() => expect(pendingSendPayloads()).toHaveLength(1));
    expect(pendingSendPayloads()[0]?.body).toBe("Sent while offline");
  });

  it("sends a message typed before the socket's very first connect, once it connects", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);
    await screen.findByRole("log");

    // The socket has never connected yet: `connected` starts false.
    await user.type(screen.getByLabelText("Message"), "Typed before first connect");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText("Sending…")).toBeInTheDocument();
    expect(pendingSendPayloads()).toHaveLength(0); // nothing emitted yet: never connected

    act(() => triggerConnect()); // the socket's first-ever connection

    await waitFor(() => expect(pendingSendPayloads()).toHaveLength(1));
    expect(pendingSendPayloads()[0]?.body).toBe("Typed before first connect");
  });

  it("resends a queued offline message exactly once on a reconnect", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);
    await screen.findByRole("log");

    act(() => triggerConnect()); // the socket's first-ever connection
    act(() => triggerDisconnect());
    expect(await screen.findByText("Reconnecting…")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Message"), "Queued once");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(pendingSendPayloads()).toHaveLength(0); // still offline

    act(() => triggerConnect()); // a reconnect this time

    await waitFor(() => expect(pendingSendPayloads()).toHaveLength(1));
    const [payload] = pendingSendPayloads();
    expect(payload?.body).toBe("Queued once");
    expect(sentPayloadsFor(payload!.client_msg_id)).toHaveLength(1);
  });

  it("refreshes auth when a send comes back unauthorized", async () => {
    const api = fakeApi({
      "GET /me": () => [200, ADA],
      "GET /conversations/conv-1": [200, CONVERSATION],
      [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
    });
    const user = userEvent.setup();
    renderApp(`/messages/${CONVERSATION_ID}`);
    await screen.findByRole("log");

    act(() => triggerConnect());
    await user.type(screen.getByLabelText("Message"), "Hello");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const [payload] = pendingSendPayloads();
    const callsBefore = api.callsTo("GET /me").length;
    act(() => resolveSend(payload!.client_msg_id, { ok: false, error: { code: "unauthorized", message: "Nope." } }));

    await waitFor(() => expect(api.callsTo("GET /me").length).toBeGreaterThan(callsBefore));
  });

  it("shows a project conversation's header linking to its project, with no presence", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /conversations/${TEAM_CONVERSATION_ID}`]: [200, TEAM_CONVERSATION],
      [`GET /conversations/${TEAM_CONVERSATION_ID}/messages`]: messagesRoute({
        messages: [],
        before: null,
        after: null,
      }),
    });
    renderApp(`/messages/${TEAM_CONVERSATION_ID}`);

    expect(await screen.findByRole("link", { name: "Campus Tutor Finder" })).toHaveAttribute(
      "href",
      `/projects/${PROJECT_ID}`,
    );
    expect(screen.queryByText("online")).not.toBeInTheDocument();
  });

  it("labels each project message by its own author, falling back to A student", async () => {
    const TEAMMATE_MESSAGE = {
      id: "tm1",
      conversation_id: TEAM_CONVERSATION_ID,
      author_id: TEAMMATE_ID,
      author_name: "Katherine Johnson",
      body: "Standup at 10?",
      client_msg_id: null,
      created_at: "2026-11-05T00:00:00Z",
      removed: false,
    };
    const UNNAMED_MESSAGE = {
      id: "tm2",
      conversation_id: TEAM_CONVERSATION_ID,
      author_id: "55555555-5555-4555-8555-555555555555",
      author_name: null,
      body: "Sounds good",
      client_msg_id: null,
      created_at: "2026-11-05T00:01:00Z",
      removed: false,
    };
    const MY_MESSAGE = {
      id: "tm3",
      conversation_id: TEAM_CONVERSATION_ID,
      author_id: ADA.id,
      author_name: "Ada Lovelace",
      body: "I'll start it",
      client_msg_id: null,
      created_at: "2026-11-05T00:02:00Z",
      removed: false,
    };
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /conversations/${TEAM_CONVERSATION_ID}`]: [200, TEAM_CONVERSATION],
      [`GET /conversations/${TEAM_CONVERSATION_ID}/messages`]: messagesRoute({
        messages: [TEAMMATE_MESSAGE, UNNAMED_MESSAGE, MY_MESSAGE],
        before: null,
        after: "cursor-1",
      }),
    });
    renderApp(`/messages/${TEAM_CONVERSATION_ID}`);

    expect(await screen.findByText(/^Katherine Johnson ·/)).toBeInTheDocument();
    expect(screen.getByText(/^A student ·/)).toBeInTheDocument();
    expect(screen.getByText(/^You ·/)).toBeInTheDocument();
  });

  describe("starting a call", () => {
    it("starts a call and navigates to the room, for a direct thread", async () => {
      const user = userEvent.setup();
      const api = fakeApi({
        "GET /me": [200, ADA],
        "GET /conversations/conv-1": [200, CONVERSATION],
        [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
        "POST /rooms": [201, { id: "room-1" }],
      });
      const { router } = renderApp(`/messages/${CONVERSATION_ID}`);
      await screen.findByRole("log");

      await user.click(screen.getByRole("button", { name: "Call" }));

      await waitFor(() => expect(router.state.location.pathname).toBe("/rooms/room-1"));
      expect(api.callsTo("POST /rooms")[0]?.body).toEqual({ conversation_id: CONVERSATION_ID });
    });

    it("starts a call and navigates to the room, for a project thread", async () => {
      const user = userEvent.setup();
      const api = fakeApi({
        "GET /me": [200, ADA],
        [`GET /conversations/${TEAM_CONVERSATION_ID}`]: [200, TEAM_CONVERSATION],
        [`GET /conversations/${TEAM_CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
        "POST /rooms": [201, { id: "room-2" }],
      });
      const { router } = renderApp(`/messages/${TEAM_CONVERSATION_ID}`);
      await screen.findByRole("log");

      await user.click(screen.getByRole("button", { name: "Call" }));

      await waitFor(() => expect(router.state.location.pathname).toBe("/rooms/room-2"));
      expect(api.callsTo("POST /rooms")[0]?.body).toEqual({ conversation_id: TEAM_CONVERSATION_ID });
    });

    it("disables the Call button while starting, and shows the error without navigating on failure", async () => {
      const user = userEvent.setup();
      let resolveStart!: () => void;
      fakeApi({
        "GET /me": [200, ADA],
        "GET /conversations/conv-1": [200, CONVERSATION],
        [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
        "POST /rooms": () =>
          new Promise((resolve) => {
            resolveStart = () => resolve(apiError(429, "rate_limited"));
          }),
      });
      const { router } = renderApp(`/messages/${CONVERSATION_ID}`);
      await screen.findByRole("log");

      const button = screen.getByRole("button", { name: "Call" });
      await user.click(button);

      expect(button).toBeDisabled();
      act(() => resolveStart());

      expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a moment and try again.");
      expect(screen.getByRole("button", { name: "Call" })).toBeEnabled();
      expect(router.state.location.pathname).toBe(`/messages/${CONVERSATION_ID}`);
    });

    it("shows the room's own copy for a 404 (e.g. after a block), not the account one", async () => {
      const user = userEvent.setup();
      fakeApi({
        "GET /me": [200, ADA],
        "GET /conversations/conv-1": [200, CONVERSATION],
        [`GET /conversations/${CONVERSATION_ID}/messages`]: messagesRoute({ messages: [], before: null, after: null }),
        "POST /rooms": apiError(404, "not_found"),
      });
      renderApp(`/messages/${CONVERSATION_ID}`);
      await screen.findByRole("log");

      await user.click(screen.getByRole("button", { name: "Call" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("This call isn't available.");
    });
  });
});
