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
    await user.click(within(menu).getByRole("menuitem", { name: "Settings" }));

    expect(router.state.location.pathname).toBe("/settings");
  });

  it("asks unverified students to verify before they can edit a profile", async () => {
    const api = fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp("/profile");

    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
    expect(api.callsTo("GET /me/profile")).toHaveLength(0);
  });
});
