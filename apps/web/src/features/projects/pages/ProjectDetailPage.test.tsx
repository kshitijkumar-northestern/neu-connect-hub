import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const OWNER = { user_id: ADA.id, display_name: "Ada Lovelace" };
const MEMBER_ID = "33333333-3333-4333-8333-333333333333";
const OUTSIDER_ID = "44444444-4444-4444-8444-444444444444";

function baseProject(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    id: "project-1",
    title: "Campus meal planner",
    summary: "Line one.\nLine two.",
    capacity: 3,
    status: "open",
    member_count: 2,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    owner: OWNER,
    roles: [
      { id: "role-1", title: "Designer", open_slots: 2, remaining_slots: 1, skills: [{ slug: "figma", name: "Figma" }] },
    ],
    members: [
      { user_id: ADA.id, display_name: "Ada Lovelace", member_role: "owner", role: null, joined_at: "2026-11-01T00:00:00Z" },
      {
        user_id: MEMBER_ID,
        display_name: "Grace Hopper",
        member_role: "member",
        role: { id: "role-1", title: "Designer" },
        joined_at: "2026-11-02T00:00:00Z",
      },
    ],
    missing_skills: [{ slug: "python", name: "Python" }],
    conversation_id: null,
    viewer_application: null,
    ...overrides,
  };
}

describe("ProjectDetailPage", () => {
  it("renders the project's title, status, summary, roles, missing skills and team", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /projects/project-1": [200, baseProject({ conversation_id: "conv-1" })] });
    renderApp("/projects/project-1");

    expect(await screen.findByRole("heading", { name: "Campus meal planner" })).toBeInTheDocument();
    expect(screen.getByText("Open")).toBeInTheDocument();
    expect(screen.getByText("Led by Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.textContent === "Line one.\nLine two.")).toBeInTheDocument();
    expect(screen.getByText("2 of 3 on the team")).toBeInTheDocument();
    expect(screen.getAllByText("Designer")).toHaveLength(2); // the role itself, and Grace's role label
    expect(screen.getByText("1 place left")).toBeInTheDocument();
    expect(screen.getByText("Figma")).toBeInTheDocument();
    expect(screen.getByText("Skills the team still needs")).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Grace Hopper" })).toHaveAttribute("href", `/people/${MEMBER_ID}`);
    expect(screen.getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute("href", `/people/${ADA.id}`);
    expect(screen.getByText("Lead")).toBeInTheDocument();
  });

  it("hides the missing-skills section when it's empty", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /projects/project-1": [200, baseProject({ missing_skills: [] })] });
    renderApp("/projects/project-1");

    await screen.findByRole("heading", { name: "Campus meal planner" });
    expect(screen.queryByText("Skills the team still needs")).not.toBeInTheDocument();
  });

  it("not found: shows a neutral message for a missing or blocked project", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /projects/project-1": apiError(404, "not_found") });
    renderApp("/projects/project-1");

    expect(await screen.findByText("This project isn't available.")).toBeInTheDocument();
  });

  describe("as the owner", () => {
    it("offers edit, applications and team chat links, and can close an open project", async () => {
      const user = userEvent.setup();
      let current = baseProject({ conversation_id: "conv-1" });
      const api = fakeApi({
        "GET /me": [200, ADA],
        "GET /projects/project-1": () => [200, current],
        "PATCH /projects/project-1": (call) => {
          current = { ...current, status: (call.body as { status: string }).status };
          return [200, current];
        },
      });
      renderApp("/projects/project-1");

      expect(await screen.findByRole("link", { name: "Edit project" })).toHaveAttribute(
        "href",
        "/projects/project-1/edit",
      );
      expect(screen.getByRole("link", { name: "Applications" })).toHaveAttribute(
        "href",
        "/projects/project-1/applications",
      );
      expect(screen.getByRole("link", { name: "Team chat" })).toHaveAttribute("href", "/messages/conv-1");

      await user.click(screen.getByRole("button", { name: "Close project" }));

      expect(await screen.findByRole("button", { name: "Reopen project" })).toBeInTheDocument();
      expect(api.callsTo("PATCH /projects/project-1")[0]?.body).toEqual({ status: "closed" });
    });

    it("disables reopening a full project and explains why", async () => {
      fakeApi({
        "GET /me": [200, ADA],
        "GET /projects/project-1": [
          200,
          baseProject({ status: "full", member_count: 3, capacity: 3, conversation_id: "conv-1" }),
        ],
      });
      renderApp("/projects/project-1");

      const reopen = await screen.findByRole("button", { name: "Reopen project" });
      expect(reopen).toBeDisabled();
      expect(screen.getByText("Raise the team size to reopen it.")).toBeInTheDocument();
    });

    it("lets a full project reopen once there's room", async () => {
      fakeApi({
        "GET /me": [200, ADA],
        "GET /projects/project-1": [
          200,
          baseProject({ status: "full", member_count: 2, capacity: 3, conversation_id: "conv-1" }),
        ],
      });
      renderApp("/projects/project-1");

      expect(await screen.findByRole("button", { name: "Reopen project" })).toBeEnabled();
    });
  });

  describe("as a member", () => {
    it("shows 'You're on this team' and a team chat link", async () => {
      fakeApi({
        "GET /me": [200, { ...ADA, id: MEMBER_ID }],
        "GET /projects/project-1": [200, baseProject({ conversation_id: "conv-1" })],
      });
      renderApp("/projects/project-1");

      expect(await screen.findByText("You're on this team")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Team chat" })).toHaveAttribute("href", "/messages/conv-1");
      expect(screen.queryByRole("link", { name: "Edit project" })).not.toBeInTheDocument();
    });
  });

  describe("as an applicant", () => {
    it("shows the pending application and withdraws it", async () => {
      const user = userEvent.setup();
      let withdrawn = false;
      const api = fakeApi({
        "GET /me": [200, { ...ADA, id: OUTSIDER_ID }],
        "GET /projects/project-1": () =>
          withdrawn
            ? [200, baseProject({ viewer_application: { id: "app-1", status: "withdrawn", role_id: "role-1" } })]
            : [200, baseProject({ viewer_application: { id: "app-1", status: "pending", role_id: "role-1" } })],
        "POST /projects/project-1/applications/app-1/withdraw": () => {
          withdrawn = true;
          return [204];
        },
      });
      renderApp("/projects/project-1");

      expect(await screen.findByText("Application sent for Designer")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Withdraw application" }));

      await waitFor(() => expect(screen.queryByText("Application sent for Designer")).not.toBeInTheDocument());
      expect(api.callsTo("POST /projects/project-1/applications/app-1/withdraw")).toHaveLength(1);
    });
  });

  describe("as an outsider", () => {
    it("offers a role picker and applies", async () => {
      const user = userEvent.setup();
      let applied = false;
      const api = fakeApi({
        "GET /me": [200, { ...ADA, id: OUTSIDER_ID }],
        "GET /projects/project-1": () =>
          applied
            ? [200, baseProject({ viewer_application: { id: "app-2", status: "pending", role_id: null } })]
            : [200, baseProject()],
        "POST /projects/project-1/apply": () => {
          applied = true;
          return [200, { id: "app-2", project_id: "project-1", status: "pending", role_id: null }];
        },
      });
      renderApp("/projects/project-1");

      expect(await screen.findByRole("button", { name: "Apply" })).toBeInTheDocument();
      expect(screen.getByLabelText("Role")).toHaveTextContent("Any role");
      await user.click(screen.getByRole("button", { name: "Apply" }));

      expect(await screen.findByText("Application sent")).toBeInTheDocument();
      expect(api.callsTo("POST /projects/project-1/apply")[0]?.body).toEqual({ role_id: null });
    });

    it("shows a rejected application and offers to apply again", async () => {
      fakeApi({
        "GET /me": [200, { ...ADA, id: OUTSIDER_ID }],
        "GET /projects/project-1": [
          200,
          baseProject({ viewer_application: { id: "app-3", status: "rejected", role_id: null } }),
        ],
      });
      renderApp("/projects/project-1");

      expect(await screen.findByText("Your application was rejected.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Apply again" })).toBeInTheDocument();
    });

    it("shows a friendly message and no apply control for a full project", async () => {
      fakeApi({
        "GET /me": [200, { ...ADA, id: OUTSIDER_ID }],
        "GET /projects/project-1": [200, baseProject({ status: "full" })],
      });
      renderApp("/projects/project-1");

      expect(await screen.findByText("This team is full.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Apply/ })).not.toBeInTheDocument();
    });

    it("shows a friendly message and no apply control for a closed project", async () => {
      fakeApi({
        "GET /me": [200, { ...ADA, id: OUTSIDER_ID }],
        "GET /projects/project-1": [200, baseProject({ status: "closed" })],
      });
      renderApp("/projects/project-1");

      expect(await screen.findByText("This project isn't taking new members.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Apply/ })).not.toBeInTheDocument();
    });

    it("shows the error and refreshes the project on a 409", async () => {
      const user = userEvent.setup();
      let full = false;
      fakeApi({
        "GET /me": [200, { ...ADA, id: OUTSIDER_ID }],
        "GET /projects/project-1": () => [200, baseProject({ status: full ? "full" : "open" })],
        "POST /projects/project-1/apply": () => {
          full = true;
          return apiError(409, "project_full");
        },
      });
      renderApp("/projects/project-1");

      await user.click(await screen.findByRole("button", { name: "Apply" }));

      expect(await screen.findByText("This team is full.")).toBeInTheDocument();
    });
  });

  describe("starting a team call", () => {
    it("offers no Team call when there's no conversation yet", async () => {
      fakeApi({ "GET /me": [200, ADA], "GET /projects/project-1": [200, baseProject({ conversation_id: null })] });
      renderApp("/projects/project-1");

      await screen.findByRole("link", { name: "Edit project" });
      expect(screen.queryByRole("button", { name: "Team call" })).not.toBeInTheDocument();
    });

    it("starts a team call and navigates to the room, for the owner", async () => {
      const user = userEvent.setup();
      const api = fakeApi({
        "GET /me": [200, ADA],
        "GET /projects/project-1": [200, baseProject({ conversation_id: "conv-1" })],
        "POST /rooms": [201, { id: "room-1" }],
      });
      const { router } = renderApp("/projects/project-1");

      await user.click(await screen.findByRole("button", { name: "Team call" }));

      await waitFor(() => expect(router.state.location.pathname).toBe("/rooms/room-1"));
      expect(api.callsTo("POST /rooms")[0]?.body).toEqual({ conversation_id: "conv-1" });
    });

    it("starts a team call and navigates to the room, for a member", async () => {
      const user = userEvent.setup();
      const api = fakeApi({
        "GET /me": [200, { ...ADA, id: MEMBER_ID }],
        "GET /projects/project-1": [200, baseProject({ conversation_id: "conv-1" })],
        "POST /rooms": [201, { id: "room-2" }],
      });
      const { router } = renderApp("/projects/project-1");

      await user.click(await screen.findByRole("button", { name: "Team call" }));

      await waitFor(() => expect(router.state.location.pathname).toBe("/rooms/room-2"));
      expect(api.callsTo("POST /rooms")[0]?.body).toEqual({ conversation_id: "conv-1" });
    });

    it("disables Team call while starting, and shows the error without navigating on failure", async () => {
      const user = userEvent.setup();
      let resolveStart!: () => void;
      const { router } = (() => {
        fakeApi({
          "GET /me": [200, ADA],
          "GET /projects/project-1": [200, baseProject({ conversation_id: "conv-1" })],
          "POST /rooms": () =>
            new Promise((resolve) => {
              resolveStart = () => resolve(apiError(429, "rate_limited"));
            }),
        });
        return renderApp("/projects/project-1");
      })();

      const button = await screen.findByRole("button", { name: "Team call" });
      await user.click(button);

      expect(button).toBeDisabled();
      act(() => resolveStart());

      expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a moment and try again.");
      expect(screen.getByRole("button", { name: "Team call" })).toBeEnabled();
      expect(router.state.location.pathname).toBe("/projects/project-1");
    });

    it("shows the room's own copy for a 404 (e.g. after a team removal), not the account one", async () => {
      const user = userEvent.setup();
      fakeApi({
        "GET /me": [200, ADA],
        "GET /projects/project-1": [200, baseProject({ conversation_id: "conv-1" })],
        "POST /rooms": apiError(404, "not_found"),
      });
      renderApp("/projects/project-1");

      await user.click(await screen.findByRole("button", { name: "Team call" }));

      expect(await screen.findByRole("alert")).toHaveTextContent("This call isn't available.");
    });
  });
});
