import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { Discovery } from "@/features/discovery/api";
import { ADA, apiError, fakeApi, type Reply, type Route } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const GRACE_ID = "11111111-1111-4111-8111-111111111112";
const KATHERINE_ID = "11111111-1111-4111-8111-111111111113";

const GRACE: Discovery["matches"][number] = {
  user_id: GRACE_ID,
  display_name: "Grace Hopper",
  headline: "Compiler enthusiast",
  reasons: [
    { kind: "skills_covered", items: [{ slug: "ui-design", name: "UI design" }, { slug: "figma", name: "Figma" }] },
  ],
};

const KATHERINE: Discovery["matches"][number] = {
  user_id: KATHERINE_ID,
  display_name: "Katherine Johnson",
  headline: "Orbital mechanics",
  reasons: [{ kind: "shared_goals", goals: ["research"] }],
};

function discoveryRoutes(matches: Discovery["matches"]): Record<string, Route> {
  return { "GET /me": [200, ADA], "GET /discovery": [200, { matches }] };
}

describe("DiscoverPage", () => {
  it("shows a loading state, then the results", async () => {
    let resolveDiscovery: (reply: Reply) => void = () => undefined;
    const pending = new Promise<Reply>((resolve) => {
      resolveDiscovery = resolve;
    });
    fakeApi({ "GET /me": [200, ADA], "GET /discovery": () => pending });
    renderApp("/discover");

    expect(await screen.findByText("Finding matches…")).toBeInTheDocument();

    resolveDiscovery([200, { matches: [GRACE] }]);

    expect(await screen.findByRole("list", { name: "Suggested collaborators" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grace Hopper" })).toHaveAttribute("href", `/people/${GRACE_ID}`);
  });

  it("explains the opt-in requirement and links to the profile page", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /discovery": apiError(403, "discovery_opt_in_required") });
    renderApp("/discover");

    expect(await screen.findByText(/Turn on discovery in your profile/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /profile/i })).toHaveAttribute("href", "/profile");
  });

  it("shows an empty state with the scan entry point", async () => {
    fakeApi(discoveryRoutes([]));
    renderApp("/discover");

    expect(await screen.findByText("No suggestions right now")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Scan a code" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Show your code" })).toHaveAttribute("href", "/share");
    expect(screen.getByRole("link", { name: /projects/i })).toHaveAttribute("href", "/projects");
  });

  it("shows an error with a retry that recovers", async () => {
    const user = userEvent.setup();
    let failing = true;
    fakeApi({
      "GET /me": [200, ADA],
      "GET /discovery": () => (failing ? apiError(500, "server_error") : [200, { matches: [GRACE] }]),
    });
    renderApp("/discover");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    failing = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("link", { name: "Grace Hopper" })).toBeInTheDocument();
  });

  it("says to wait on a 429, with no retry that would only hit the limit again", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /discovery": apiError(429, "rate_limited") });
    renderApp("/discover");

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a few minutes and try again.");
    expect(screen.queryByText(/Check your connection/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("never renders a percentage anywhere on the page", async () => {
    fakeApi(discoveryRoutes([GRACE, KATHERINE]));
    renderApp("/discover");

    await screen.findByRole("list", { name: "Suggested collaborators" });
    expect(document.body.textContent).not.toMatch(/%/);
  });

  it("sends a connect request, shows it pending then sent, with a live region", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      ...discoveryRoutes([GRACE]),
      "POST /connections": [200, { id: "c1", state: "pending", direction: "outgoing", user_id: GRACE_ID }],
      "POST /discovery/feedback": [204],
    });
    renderApp("/discover");

    const connect = await screen.findByRole("button", { name: "Connect with Grace Hopper" });
    await user.click(connect);

    expect(await screen.findByText("Connection request sent to Grace Hopper")).toHaveAttribute("role", "status");
    expect(screen.getByRole("button", { name: "Request sent to Grace Hopper" })).toBeDisabled();
    expect(api.callsTo("POST /connections")).toHaveLength(1);
    await waitFor(() => expect(api.callsTo("POST /discovery/feedback")).toHaveLength(1));
    expect(api.callsTo("POST /discovery/feedback")[0]?.body).toEqual({ candidate_id: GRACE_ID, action: "connected" });
  });

  it("shows a friendly message on a 409 connect conflict", async () => {
    const user = userEvent.setup();
    fakeApi({ ...discoveryRoutes([GRACE]), "POST /connections": () => apiError(409, "connection_exists") });
    renderApp("/discover");

    await user.click(await screen.findByRole("button", { name: "Connect with Grace Hopper" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already connected or requested");
  });

  it("gives every action an accessible name with the student's name", async () => {
    fakeApi(discoveryRoutes([GRACE]));
    renderApp("/discover");

    expect(await screen.findByRole("button", { name: "Connect with Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not now: Grace Hopper" })).toBeInTheDocument();
  });

  it("hides a card on Not now, fires dismissed feedback, and moves focus to the next card", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ ...discoveryRoutes([GRACE, KATHERINE]), "POST /discovery/feedback": [204] });
    renderApp("/discover");

    await screen.findByRole("link", { name: "Grace Hopper" });
    await user.click(screen.getByRole("button", { name: "Not now: Grace Hopper" }));

    expect(screen.queryByRole("link", { name: "Grace Hopper" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Katherine Johnson" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Katherine Johnson" })).toHaveFocus();
    await waitFor(() => expect(api.callsTo("POST /discovery/feedback")).toHaveLength(1));
    expect(api.callsTo("POST /discovery/feedback")[0]?.body).toEqual({ candidate_id: GRACE_ID, action: "dismissed" });
  });

  it("moves focus to the previous card when Not now removes the last one in the list", async () => {
    const user = userEvent.setup();
    fakeApi({ ...discoveryRoutes([GRACE, KATHERINE]), "POST /discovery/feedback": [204] });
    renderApp("/discover");

    await user.click(await screen.findByRole("button", { name: "Not now: Katherine Johnson" }));

    expect(screen.queryByRole("link", { name: "Katherine Johnson" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grace Hopper" })).toHaveFocus();
  });

  it("tells an admin that discovery is for students, with no opt-in link", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /discovery": apiError(403, "discovery_students_only") });
    renderApp("/discover");

    expect(await screen.findByText("Discovery is for student accounts.")).toBeInTheDocument();
    expect(screen.queryByText(/Turn on discovery/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("moves focus to the page heading when Not now removes the last card", async () => {
    const user = userEvent.setup();
    fakeApi({ ...discoveryRoutes([GRACE]), "POST /discovery/feedback": [204] });
    renderApp("/discover");

    await user.click(await screen.findByRole("button", { name: "Not now: Grace Hopper" }));

    expect(await screen.findByText("No suggestions right now")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Discover" })).toHaveFocus();
  });

  it("opens the scan dialog and shows the share link in the results state too", async () => {
    const user = userEvent.setup();
    fakeApi(discoveryRoutes([GRACE]));
    renderApp("/discover");

    await screen.findByRole("list", { name: "Suggested collaborators" });
    expect(screen.getByRole("link", { name: "Show your code" })).toHaveAttribute("href", "/share");

    await user.click(screen.getByRole("button", { name: "Scan a code" }));

    expect(await screen.findByRole("heading", { name: "Scan a code" })).toBeInTheDocument();
  });
});
