import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi, type RecordedCall, type Route } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const SKILLS = [
  { slug: "figma", name: "Figma", category: "Design" },
  { slug: "python", name: "Python", category: "Programming" },
];

const OTHER_USER = "22222222-2222-4222-8222-222222222222";

function ownedProject(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "project-1",
    title: "Campus meal planner",
    summary: "A meal planner.",
    capacity: 3,
    status: "open",
    member_count: 2,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    owner: { user_id: ADA.id, display_name: "Ada Lovelace" },
    roles: [{ id: "role-1", title: "Designer", open_slots: 1, remaining_slots: 0, skills: [{ slug: "figma", name: "Figma" }] }],
    members: [
      { user_id: ADA.id, display_name: "Ada Lovelace", member_role: "owner", role: null, joined_at: "2026-11-01T00:00:00Z" },
      {
        user_id: "33333333-3333-4333-8333-333333333333",
        display_name: "Grace Hopper",
        member_role: "member",
        role: { id: "role-1", title: "Designer" },
        joined_at: "2026-11-02T00:00:00Z",
      },
    ],
    missing_skills: [],
    conversation_id: "conv-1",
    viewer_application: null,
    ...overrides,
  };
}

function routes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return { "GET /me": [200, ADA], "GET /skills": [200, SKILLS], "GET /projects/project-1": [200, ownedProject()], ...overrides };
}

describe("EditProjectPage", () => {
  it("pre-fills the form from the project", async () => {
    fakeApi(routes());
    renderApp("/projects/project-1/edit");

    expect(await screen.findByLabelText("Title")).toHaveValue("Campus meal planner");
    expect(screen.getByLabelText("Summary (optional)")).toHaveValue("A meal planner.");
    expect(screen.getByLabelText("Team size")).toHaveValue(3);
    expect(screen.getByLabelText("Role 1 title")).toHaveValue("Designer");
  });

  it("turns away a non-owner before showing the form", async () => {
    fakeApi(routes({ "GET /projects/project-1": [200, ownedProject({ owner: { user_id: OTHER_USER, display_name: "Bob" } })] }));
    renderApp("/projects/project-1/edit");

    expect(await screen.findByText("Only the project's owner can edit it.")).toBeInTheDocument();
  });

  it("shows a not-found message for a missing project", async () => {
    fakeApi(routes({ "GET /projects/project-1": apiError(404, "not_found") }));
    renderApp("/projects/project-1/edit");

    expect(await screen.findByText("This project isn't available.")).toBeInTheDocument();
  });

  it("disables removing a role that has a holder, and floors its places at the holder count", async () => {
    fakeApi(routes());
    renderApp("/projects/project-1/edit");

    await screen.findByLabelText("Role 1 title");
    expect(screen.getByRole("button", { name: "Remove role 1" })).toBeDisabled();
    expect(screen.getByText("A role with members already in it can't be removed.")).toBeInTheDocument();
    expect(screen.getByLabelText("Role 1 places")).toHaveAttribute("min", "1");
  });

  it("saves changes and returns to the detail page", async () => {
    const user = userEvent.setup();
    let current = ownedProject();
    const api = fakeApi(
      routes({
        "GET /projects/project-1": () => [200, current],
        "PATCH /projects/project-1": (call: RecordedCall) => {
          current = { ...current, title: (call.body as { title: string }).title };
          return [200, current];
        },
      }),
    );
    renderApp("/projects/project-1/edit");

    const titleField = await screen.findByLabelText("Title");
    await user.clear(titleField);
    await user.type(titleField, "Campus meal planner v2");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(api.callsTo("PATCH /projects/project-1")[0]?.body).toEqual({
      title: "Campus meal planner v2",
      summary: "A meal planner.",
      capacity: 3,
      roles: [{ id: "role-1", title: "Designer", open_slots: 1, skills: ["figma"] }],
    });
    expect(await screen.findByRole("heading", { name: "Campus meal planner v2" })).toBeInTheDocument();
  });

  it("can't drop the team size below the current team", async () => {
    const user = userEvent.setup();
    const api = fakeApi(routes());
    renderApp("/projects/project-1/edit");

    const capacityField = await screen.findByLabelText("Team size");
    await user.clear(capacityField);
    await user.type(capacityField, "1");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("The team size can't be smaller than the team already is.")).toBeInTheDocument();
    expect(api.callsTo("PATCH /projects/project-1")).toHaveLength(0);
  });
});
