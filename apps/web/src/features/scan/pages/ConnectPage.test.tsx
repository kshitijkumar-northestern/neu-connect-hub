import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const TOKEN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLM1234"; // 43 chars of [A-Za-z0-9_-]
const RESOLVE = "POST /connect/resolve"; // the token goes in the body, never the URL

function previewWith(connectionState: string) {
  return {
    user_id: "22222222-2222-4222-8222-222222222222",
    display_name: "Grace Hopper",
    headline: "Looking for teammates",
    connection_state: connectionState,
  };
}

describe("ConnectPage", () => {
  it("never requests a preview without a token, and shows the invalid-code message", async () => {
    const api = fakeApi({ "GET /me": [200, ADA] });
    renderApp("/connect");

    expect(await screen.findByText("This code isn't valid. Ask them for a new one.")).toBeInTheDocument();
    expect(api.callsTo(RESOLVE)).toHaveLength(0);
  });

  it("sends the token in the request body and clears it from the address bar once read", async () => {
    const api = fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, previewWith("none")] });
    const { router } = renderApp(`/connect#token=${TOKEN}`);

    await screen.findByRole("heading", { name: "Grace Hopper" });
    expect(router.state.location.hash).toBe("");
    expect(api.callsTo(RESOLVE)[0]?.body).toEqual({ token: TOKEN });
  });

  it("shows the invalid-code message for a 404", async () => {
    fakeApi({ "GET /me": [200, ADA], [RESOLVE]: apiError(404, "not_found") });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByText("This code isn't valid. Ask them for a new one.")).toBeInTheDocument();
  });

  it("shows the rate-limit copy for a 429", async () => {
    fakeApi({ "GET /me": [200, ADA], [RESOLVE]: apiError(429, "rate_limited") });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Too many attempts/);
  });

  it("shows the name and headline with a Send invitation action for connection_state none", async () => {
    fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, previewWith("none")] });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByText("Looking for teammates")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send invitation" })).toBeInTheDocument();
  });

  it("sends the invitation exactly once and then shows Invitation sent", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      [RESOLVE]: [200, previewWith("none")],
      "POST /connections": [200, { id: "c1", state: "pending", direction: "outgoing" }],
    });
    renderApp(`/connect#token=${TOKEN}`);

    await user.click(await screen.findByRole("button", { name: "Send invitation" }));

    expect(await screen.findByText("Invitation sent")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send invitation" })).not.toBeInTheDocument();
    expect(api.callsTo("POST /connections")).toHaveLength(1);
    expect(api.callsTo("POST /connections")[0]?.body).toEqual({ user_id: previewWith("none").user_id });
  });

  it("shows Invitation sent (no button) for connection_state pending_outgoing", async () => {
    fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, previewWith("pending_outgoing")] });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByText("Invitation sent")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send invitation" })).not.toBeInTheDocument();
  });

  it("links to Connections for connection_state pending_incoming", async () => {
    fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, previewWith("pending_incoming")] });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByText(/They've invited you/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /connections/i })).toHaveAttribute("href", "/connections");
  });

  it("links to the profile for connection_state connected", async () => {
    const preview = previewWith("connected");
    fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, preview] });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByText(/You're connected/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /profile/i })).toHaveAttribute("href", `/people/${preview.user_id}`);
  });

  it("asks unverified students to verify before seeing a preview", async () => {
    fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp(`/connect#token=${TOKEN}`);

    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
  });

  it("sends signed-out visitors to sign in without the token in router state, then back to /connect with it", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": apiError(401, "unauthorized"),
      "POST /auth/login": [200, ADA],
      [RESOLVE]: [200, previewWith("none")],
    });
    const { router } = renderApp(`/connect#token=${TOKEN}`);

    await waitFor(() => expect(router.state.location.pathname).toBe("/sign-in"));
    // The path+query only: the hash never rides along in router/history state.
    expect(router.state.location.state).toEqual({ from: "/connect" });

    await user.type(screen.getByLabelText("University email"), "ada@northeastern.edu");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/connect"));
    expect(router.state.location.hash).toBe("");
    expect(api.callsTo(RESOLVE)[0]?.body).toEqual({ token: TOKEN });
  });
});
