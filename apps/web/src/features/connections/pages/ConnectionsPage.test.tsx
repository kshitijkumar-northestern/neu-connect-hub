import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const INCOMING = {
  id: "i1",
  state: "pending",
  direction: "incoming",
  user_id: "11111111-1111-4111-8111-111111111112",
  display_name: "Grace Hopper",
  created_at: "2026-11-01T00:00:00Z",
  responded_at: null,
  conversation_id: null,
};

const OUTGOING = {
  id: "o1",
  state: "pending",
  direction: "outgoing",
  user_id: "11111111-1111-4111-8111-111111111113",
  display_name: null,
  created_at: "2026-11-02T00:00:00Z",
  responded_at: null,
  conversation_id: null,
};

const ACCEPTED = {
  id: "a1",
  state: "accepted",
  direction: "incoming",
  user_id: "11111111-1111-4111-8111-111111111114",
  display_name: "Katherine Johnson",
  created_at: "2026-11-03T00:00:00Z",
  responded_at: "2026-11-04T00:00:00Z",
  conversation_id: "conv-1",
};

describe("ConnectionsPage", () => {
  it("shows a friendly empty state for each section", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /connections": [200, []] });
    renderApp("/connections");

    expect(await screen.findByRole("heading", { name: "Connections" })).toBeInTheDocument();
    expect(screen.getByText("No one has asked to connect yet.")).toBeInTheDocument();
    expect(screen.getByText("You haven't sent any requests.")).toBeInTheDocument();
    expect(screen.getByText("You don't have any connections yet.")).toBeInTheDocument();
  });

  it("lists requests for you, sent requests and connections", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /connections": [200, [INCOMING, OUTGOING, ACCEPTED]] });
    renderApp("/connections");

    expect(await screen.findByRole("link", { name: "Grace Hopper" })).toHaveAttribute(
      "href",
      `/people/${INCOMING.user_id}`,
    );
    expect(screen.getByRole("link", { name: "A student" })).toHaveAttribute("href", `/people/${OUTGOING.user_id}`);
    expect(screen.getByRole("link", { name: "Katherine Johnson" })).toHaveAttribute(
      "href",
      `/people/${ACCEPTED.user_id}`,
    );
    expect(screen.getByRole("button", { name: "Accept Grace Hopper's request" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Decline Grace Hopper's request" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Withdraw request to A student" })).toBeInTheDocument();
  });

  it("accepts a request and moves it to Your connections", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /connections": [200, [INCOMING]],
      [`POST /connections/${INCOMING.id}/accept`]: [
        200,
        { ...INCOMING, state: "accepted", responded_at: "2026-11-05T00:00:00Z", conversation_id: "conv-9" },
      ],
    });
    renderApp("/connections");

    await user.click(await screen.findByRole("button", { name: "Accept Grace Hopper's request" }));

    expect(await screen.findByText("No one has asked to connect yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grace Hopper" })).toHaveAttribute("href", `/people/${INCOMING.user_id}`);
    expect(api.callsTo(`POST /connections/${INCOMING.id}/accept`)).toHaveLength(1);
  });

  it("declines a request and removes it from the list", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /connections": [200, [INCOMING]],
      [`POST /connections/${INCOMING.id}/decline`]: [204],
    });
    renderApp("/connections");

    await user.click(await screen.findByRole("button", { name: "Decline Grace Hopper's request" }));

    await waitFor(() => expect(screen.queryByText("Grace Hopper")).not.toBeInTheDocument());
    expect(screen.getByText("No one has asked to connect yet.")).toBeInTheDocument();
    expect(api.callsTo(`POST /connections/${INCOMING.id}/decline`)).toHaveLength(1);
  });

  it("withdraws a sent request and removes it from the list", async () => {
    const user = userEvent.setup();
    const WITH_NAME = { ...OUTGOING, display_name: "Grace Hopper" };
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /connections": [200, [WITH_NAME]],
      [`POST /connections/${OUTGOING.id}/withdraw`]: [204],
    });
    renderApp("/connections");

    await user.click(await screen.findByRole("button", { name: "Withdraw request to Grace Hopper" }));

    await waitFor(() => expect(screen.queryByText("Grace Hopper")).not.toBeInTheDocument());
    expect(screen.getByText("You haven't sent any requests.")).toBeInTheDocument();
    expect(api.callsTo(`POST /connections/${OUTGOING.id}/withdraw`)).toHaveLength(1);
  });

  it("shows an error and reloads the list when a request is already closed", async () => {
    const user = userEvent.setup();
    const WITH_NAME = { ...OUTGOING, display_name: "Grace Hopper" };
    let withdrawn = false;
    fakeApi({
      "GET /me": [200, ADA],
      "GET /connections": () => (withdrawn ? [200, []] : [200, [WITH_NAME]]),
      [`POST /connections/${OUTGOING.id}/withdraw`]: () => {
        withdrawn = true;
        return apiError(409, "connection_closed");
      },
    });
    renderApp("/connections");

    await user.click(await screen.findByRole("button", { name: "Withdraw request to Grace Hopper" }));

    expect(await screen.findByText("This request is no longer open.")).toBeInTheDocument();
    expect(await screen.findByText("You haven't sent any requests.")).toBeInTheDocument();
  });

  it("asks unverified students to verify before seeing their connections", async () => {
    fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp("/connections");

    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
  });
});
