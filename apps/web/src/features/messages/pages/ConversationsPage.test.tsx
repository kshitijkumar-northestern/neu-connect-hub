import { act, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ADA, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";
import { resetFakeSocket, triggerMessage, triggerPresence } from "@/test/fake-socket";

vi.mock("@/lib/socket", async () => (await import("@/test/fake-socket")).fakeSocketModule);

const GRACE_ID = "22222222-2222-4222-8222-222222222222";
const KATHERINE_ID = "33333333-3333-4333-8333-333333333333";

const WITH_GRACE = {
  id: "conv-grace",
  kind: "direct",
  other: { user_id: GRACE_ID, display_name: "Grace Hopper", online: false },
  last_message: null,
  updated_at: "2026-11-01T00:00:00Z",
};

const WITH_KATHERINE = {
  id: "conv-katherine",
  kind: "direct",
  other: { user_id: KATHERINE_ID, display_name: null, online: true },
  last_message: {
    id: "m1",
    conversation_id: "conv-katherine",
    author_id: KATHERINE_ID,
    body: null,
    client_msg_id: null,
    created_at: "2026-11-02T00:00:00Z",
    removed: true,
  },
  updated_at: "2026-11-02T00:00:00Z",
};

afterEach(() => {
  resetFakeSocket();
});

describe("ConversationsPage", () => {
  it("explains how to start messaging when there are no conversations yet", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /conversations": [200, []] });
    renderApp("/messages");

    expect(await screen.findByRole("heading", { name: "Messages" })).toBeInTheDocument();
    expect(screen.getByText(/once you're connected/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to connections" })).toHaveAttribute("href", "/connections");
  });

  it("lists conversations with previews, online status and names, newest first", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /conversations": [200, [WITH_KATHERINE, WITH_GRACE]] });
    renderApp("/messages");

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByRole("link")).toHaveAttribute("href", "/messages/conv-katherine");
    expect(within(rows[0]!).getByText("A student")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("online")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Message removed")).toBeInTheDocument();

    expect(within(rows[1]!).getByRole("link")).toHaveAttribute("href", "/messages/conv-grace");
    expect(within(rows[1]!).getByText("Grace Hopper")).toBeInTheDocument();
    expect(within(rows[1]!).queryByText("online")).not.toBeInTheDocument();
    expect(within(rows[1]!).getByText("No messages yet")).toBeInTheDocument();
  });

  it("moves a conversation to the top and updates its preview on a live message", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /conversations": [200, [WITH_KATHERINE, WITH_GRACE]] });
    renderApp("/messages");
    await screen.findAllByRole("listitem");

    act(() => {
      triggerMessage({
        id: "m2",
        conversation_id: "conv-grace",
        author_id: GRACE_ID,
        body: "Hey there!",
        client_msg_id: null,
        created_at: "2026-11-03T00:00:00Z",
        removed: false,
      });
    });

    const rows = await screen.findAllByRole("listitem");
    expect(within(rows[0]!).getByText("Grace Hopper")).toBeInTheDocument();
    expect(within(rows[0]!).getByText("Hey there!")).toBeInTheDocument();
  });

  it("updates online status on a live presence change", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /conversations": [200, [WITH_GRACE]] });
    renderApp("/messages");
    await screen.findAllByRole("listitem");
    expect(screen.queryByText("online")).not.toBeInTheDocument();

    act(() => {
      triggerPresence({ user_id: GRACE_ID, online: true });
    });

    expect(await screen.findByText("online")).toBeInTheDocument();
  });
});
