import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const BLOCKED = {
  user_id: "33333333-3333-4333-8333-333333333333",
  display_name: "Grace Hopper",
  blocked_at: "2026-11-01T00:00:00Z",
};

describe("SettingsPage: blocked students", () => {
  it("shows an empty state when nothing is blocked", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /blocks": [200, []] });
    renderApp("/settings");

    expect(await screen.findByText("You haven't blocked anyone.")).toBeInTheDocument();
  });

  it("lists blocked students and unblocks on request", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /blocks": [200, [BLOCKED]],
      [`DELETE /blocks/${BLOCKED.user_id}`]: [204],
    });
    renderApp("/settings");

    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Unblock" }));

    await waitFor(() => expect(screen.queryByText("Grace Hopper")).not.toBeInTheDocument());
    expect(api.callsTo(`DELETE /blocks/${BLOCKED.user_id}`)).toHaveLength(1);
  });

  it("does not show the section to an unverified student", async () => {
    fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp("/settings");

    expect(await screen.findByRole("heading", { name: "Delete account" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Blocked students" })).not.toBeInTheDocument();
  });
});

describe("SettingsPage: delete account", () => {
  it("shows an error for the wrong password", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADA],
      "GET /blocks": [200, []],
      "DELETE /me": apiError(403, "password_incorrect"),
    });
    renderApp("/settings");

    await screen.findByText("You haven't blocked anyone.");
    await user.type(screen.getByLabelText("Password"), "wrong password");
    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await user.click(await screen.findByRole("button", { name: "Delete account" }));

    expect(await screen.findByText("That password isn't right.")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeVisible();
  });

  it("deletes the account, lands on home signed out with a notice, and never visits sign-in", async () => {
    const user = userEvent.setup();
    let deleted = false;
    const api = fakeApi({
      "GET /me": () => (deleted ? apiError(401, "unauthorized") : [200, ADA]),
      "GET /blocks": [200, []],
      "DELETE /me": () => {
        deleted = true;
        return [204];
      },
    });
    const { router } = renderApp("/settings");
    const visited: string[] = [];
    router.subscribe((state) => visited.push(state.location.pathname));

    await screen.findByText("You haven't blocked anyone.");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: "Delete account" }));
    await user.click(await screen.findByRole("button", { name: "Delete account" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(router.state.location.state).toEqual({ notice: "account-deleted" });
    expect(await screen.findByRole("status")).toHaveTextContent("Your account has been deleted.");
    expect(visited).not.toContain("/sign-in");
    expect(api.callsTo("DELETE /me")[0]?.body).toEqual({ password: "correct horse battery" });

    await waitFor(() =>
      expect(screen.getByRole("banner").textContent).toMatch(/Sign in/),
    );
  });
});
