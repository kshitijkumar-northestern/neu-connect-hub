import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

describe("profile navigation", () => {
  it("links to your profile and settings from the account menu", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, ADA], "GET /me/profile": apiError(404, "profile_not_found") });
    const { router } = renderApp("/");

    await user.click(await screen.findByRole("button", { name: `Account: ${ADA.email}` }));
    const menu = await screen.findByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: "Your profile" })).toHaveAttribute("href", "/profile");
    expect(within(menu).getByRole("menuitem", { name: "Messages" })).toHaveAttribute("href", "/messages");
    expect(within(menu).getByRole("menuitem", { name: "Projects" })).toHaveAttribute("href", "/projects");
    expect(within(menu).getByRole("menuitem", { name: "Connections" })).toHaveAttribute("href", "/connections");
    expect(within(menu).getByRole("menuitem", { name: "Share or scan" })).toHaveAttribute("href", "/share");
    await user.click(within(menu).getByRole("menuitem", { name: "Settings" }));

    expect(router.state.location.pathname).toBe("/settings");
  });

  it("links to connections from the account menu", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, ADA], "GET /connections": [200, []] });
    const { router } = renderApp("/");

    await user.click(await screen.findByRole("button", { name: `Account: ${ADA.email}` }));
    const menu = await screen.findByRole("menu");
    await user.click(within(menu).getByRole("menuitem", { name: "Connections" }));

    expect(router.state.location.pathname).toBe("/connections");
  });

  it("links to share or scan from the account menu, after Connections", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, { status: "off", url: null, expires_at: null, shareable: true }],
    });
    const { router } = renderApp("/");

    await user.click(await screen.findByRole("button", { name: `Account: ${ADA.email}` }));
    const menu = await screen.findByRole("menu");
    const labels = within(menu)
      .getAllByRole("menuitem")
      .map((item) => item.textContent);
    expect(labels.indexOf("Share or scan")).toBeGreaterThan(labels.indexOf("Connections"));
    await user.click(within(menu).getByRole("menuitem", { name: "Share or scan" }));

    expect(router.state.location.pathname).toBe("/share");
  });

  it("links to projects from the account menu", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, ADA], "GET /projects": [200, { items: [], next_offset: null }] });
    const { router } = renderApp("/");

    await user.click(await screen.findByRole("button", { name: `Account: ${ADA.email}` }));
    const menu = await screen.findByRole("menu");
    await user.click(within(menu).getByRole("menuitem", { name: "Projects" }));

    expect(router.state.location.pathname).toBe("/projects");
  });

  it("asks unverified students to verify before they can edit a profile", async () => {
    const api = fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp("/profile");

    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
    expect(api.callsTo("GET /me/profile")).toHaveLength(0);
  });
});
