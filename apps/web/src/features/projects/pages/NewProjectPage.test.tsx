import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi, type Route } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const SKILLS = [
  { slug: "figma", name: "Figma", category: "Design" },
  { slug: "python", name: "Python", category: "Programming" },
];

function routes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return { "GET /me": [200, ADA], "GET /skills": [200, SKILLS], ...overrides };
}

describe("NewProjectPage", () => {
  it("shows the form once skills load", async () => {
    fakeApi(routes());
    renderApp("/projects/new");

    expect(await screen.findByRole("heading", { name: "New project" })).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toBeInTheDocument();
    expect(screen.getByLabelText("Summary (optional)")).toBeInTheDocument();
    expect(screen.getByLabelText("Team size")).toBeInTheDocument();
  });

  it("defaults team size to 2, so the project doesn't fill up before anyone can apply", async () => {
    fakeApi(routes());
    renderApp("/projects/new");

    expect(await screen.findByLabelText("Team size")).toHaveValue(2);
    expect(
      screen.getByText("Includes you. Count the whole team, not just the teammates you're looking for."),
    ).toBeInTheDocument();
    expect(screen.queryByText("A team of 1 is full straight away, so nobody can apply.")).not.toBeInTheDocument();
  });

  it("warns that a team of 1 is full straight away", async () => {
    const user = userEvent.setup();
    fakeApi(routes());
    renderApp("/projects/new");

    const teamSize = await screen.findByLabelText("Team size");
    await user.clear(teamSize);
    await user.type(teamSize, "1");

    expect(screen.getByText("A team of 1 is full straight away, so nobody can apply.")).toBeInTheDocument();

    await user.clear(teamSize);
    await user.type(teamSize, "2");
    expect(screen.queryByText("A team of 1 is full straight away, so nobody can apply.")).not.toBeInTheDocument();
  });

  it("blocks submission with an empty title and sends nothing", async () => {
    const user = userEvent.setup();
    const api = fakeApi(routes());
    renderApp("/projects/new");

    await screen.findByLabelText("Title");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Enter a title.")).toBeInTheDocument();
    expect(api.callsTo("POST /projects")).toHaveLength(0);
  });

  it("adds a role with a required skill and creates the project", async () => {
    const user = userEvent.setup();
    const created = {
      id: "new-project-1",
      title: "Campus meal planner",
      summary: "",
      capacity: 3,
      status: "open",
      member_count: 1,
      created_at: "2026-11-10T00:00:00Z",
      updated_at: "2026-11-10T00:00:00Z",
      owner: { user_id: ADA.id, display_name: "Ada Lovelace" },
      roles: [],
      members: [],
      missing_skills: [],
      conversation_id: "conv-new",
      viewer_application: null,
    };
    const api = fakeApi(
      routes({
        "POST /projects": [201, created],
        "GET /projects/new-project-1": [200, created],
      }),
    );
    renderApp("/projects/new");

    await user.type(await screen.findByLabelText("Title"), "Campus meal planner");
    await user.clear(screen.getByLabelText("Team size"));
    await user.type(screen.getByLabelText("Team size"), "3");
    await user.click(screen.getByRole("button", { name: "Add role" }));
    await user.type(screen.getByLabelText("Role 1 title"), "Designer");
    const roleSkills = screen.getByRole("group", { name: "Role 1 required skills" });
    await user.click(within(roleSkills).getByLabelText("Skill"));
    await user.click(await screen.findByRole("option", { name: "Figma" }));
    await user.click(within(roleSkills).getByRole("button", { name: "Add" }));

    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(api.callsTo("POST /projects")[0]?.body).toEqual({
      title: "Campus meal planner",
      summary: "",
      capacity: 3,
      roles: [{ title: "Designer", open_slots: 1, skills: ["figma"] }],
    });
    expect(await screen.findByRole("heading", { name: "Campus meal planner" })).toBeInTheDocument();
  });

  it("puts a server field error on the matching field", async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      routes({
        "POST /projects": [
          422,
          {
            error: {
              code: "validation_error",
              message: "The request is invalid.",
              request_id: "req-test",
              fields: [{ location: "body.title", message: "Use at most 140 characters." }],
            },
          },
        ],
      }),
    );
    renderApp("/projects/new");

    await user.type(await screen.findByLabelText("Title"), "A title");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByText("Use at most 140 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText("Title")).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("POST /projects")).toHaveLength(1);
  });

  it("shows a form-level error for a failure with no matching field", async () => {
    const user = userEvent.setup();
    fakeApi(routes({ "POST /projects": apiError(422, "unknown_skill") }));
    renderApp("/projects/new");

    await user.type(await screen.findByLabelText("Title"), "A title");
    await user.click(screen.getByRole("button", { name: "Create project" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("One of the skills isn't on the list.");
  });
});
