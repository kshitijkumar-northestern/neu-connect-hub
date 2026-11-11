import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

function project(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "11111111-1111-4111-8111-111111111101",
    title: "Campus meal planner",
    summary: "Build a meal planner for students on a budget.",
    capacity: 4,
    status: "open",
    member_count: 1,
    owner: { user_id: ADA.id, display_name: "Ada Lovelace" },
    roles: [{ id: "role-1", title: "Designer", open_slots: 2, remaining_slots: 2 }],
    created_at: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

describe("ProjectsPage", () => {
  it("shows an empty state when no projects are open", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /projects": [200, { items: [], next_offset: null }] });
    renderApp("/projects");

    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(screen.getByText("No projects are looking for teammates right now.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New project" })).toHaveAttribute("href", "/projects/new");
    expect(screen.getByRole("link", { name: "My applications" })).toHaveAttribute("href", "/applications");
  });

  it("lists a project's title, owner, summary, team size and roles", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /projects": [200, { items: [project()], next_offset: null }],
    });
    renderApp("/projects");

    expect(await screen.findByRole("link", { name: "Campus meal planner" })).toHaveAttribute(
      "href",
      "/projects/11111111-1111-4111-8111-111111111101",
    );
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("Build a meal planner for students on a budget.")).toBeInTheDocument();
    expect(screen.getByText("1 of 4 on the team")).toBeInTheDocument();
    expect(screen.getByText("Designer · 2 places left")).toBeInTheDocument();
  });

  it("falls back to 'A student' when the owner has no display name", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /projects": [200, { items: [project({ owner: { user_id: "other", display_name: null } })], next_offset: null }],
    });
    renderApp("/projects");

    expect(await screen.findByText("A student")).toBeInTheDocument();
  });

  it("loads more projects on request and hides the button once exhausted", async () => {
    const user = userEvent.setup();
    const first = project({ id: "project-1", title: "First project" });
    const second = project({ id: "project-2", title: "Second project" });
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /projects": (call) => {
        if (call.search.includes("offset=20")) return [200, { items: [second], next_offset: null }];
        return [200, { items: [first], next_offset: 20 }];
      },
    });
    renderApp("/projects");

    expect(await screen.findByRole("link", { name: "First project" })).toBeInTheDocument();
    const loadMore = screen.getByRole("button", { name: "Load more" });
    await user.click(loadMore);

    expect(await screen.findByRole("link", { name: "Second project" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "First project" })).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument());
    expect(api.calls.filter((call) => call.key === "GET /projects" && call.search.includes("offset=20"))).toHaveLength(1);
  });

  it("shows an error when the list can't load", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /projects": [500, { error: { code: "server_error", message: "oops" } }] });
    renderApp("/projects");

    expect(await screen.findByRole("alert")).toHaveTextContent("Can't load projects right now.");
  });

  it("asks unverified students to verify before seeing projects", async () => {
    fakeApi({ "GET /me": [200, { ...ADA, verified: false }] });
    renderApp("/projects");

    expect(await screen.findByRole("heading", { name: "Verify your email" })).toBeInTheDocument();
  });

  it("hides the 'Your projects' section when the student leads and belongs to nothing", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /projects": [200, { items: [], next_offset: null }],
      "GET /me/projects": [200, { items: [], next_offset: null }],
    });
    renderApp("/projects");

    await screen.findByRole("heading", { name: "Projects" });
    expect(screen.queryByRole("heading", { name: "Your projects" })).not.toBeInTheDocument();
  });

  it("lists the student's own projects, full and closed included, with their status badge", async () => {
    const closed = project({ id: "mine-1", title: "Closed project", status: "closed" });
    const full = project({ id: "mine-2", title: "Full project", status: "full" });
    fakeApi({
      "GET /me": [200, ADA],
      "GET /projects": [200, { items: [], next_offset: null }],
      "GET /me/projects": [200, { items: [closed, full], next_offset: null }],
    });
    renderApp("/projects");

    expect(await screen.findByRole("heading", { name: "Your projects" })).toBeInTheDocument();
    const closedLink = screen.getByRole("link", { name: "Closed project" });
    const fullLink = screen.getByRole("link", { name: "Full project" });
    expect(closedLink).toHaveAttribute("href", "/projects/mine-1");
    expect(fullLink).toHaveAttribute("href", "/projects/mine-2");
    expect(closedLink.closest("[data-slot='card']")).toHaveTextContent("Closed");
    expect(fullLink.closest("[data-slot='card']")).toHaveTextContent("Full");
  });

  it("pages 'Your projects' separately from the public list", async () => {
    const user = userEvent.setup();
    const mineFirst = project({ id: "mine-1", title: "My first project" });
    const mineSecond = project({ id: "mine-2", title: "My second project" });
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /projects": [200, { items: [], next_offset: null }],
      "GET /me/projects": (call) => {
        if (call.search.includes("offset=20")) return [200, { items: [mineSecond], next_offset: null }];
        return [200, { items: [mineFirst], next_offset: 20 }];
      },
    });
    renderApp("/projects");

    expect(await screen.findByRole("link", { name: "My first project" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more of your projects" }));

    expect(await screen.findByRole("link", { name: "My second project" })).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Load more of your projects" })).not.toBeInTheDocument(),
    );
    expect(
      api.calls.filter((call) => call.key === "GET /me/projects" && call.search.includes("offset=20")),
    ).toHaveLength(1);
  });
});
