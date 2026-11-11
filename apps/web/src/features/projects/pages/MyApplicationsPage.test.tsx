import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const PROJECT_ONE_ID = "project-1";
const PROJECT_TWO_ID = "project-2";

const PENDING_APPLICATION = {
  id: "app-1",
  project_id: PROJECT_ONE_ID,
  applicant: { user_id: ADA.id, display_name: "Ada Lovelace" },
  role: { id: "role-1", title: "Designer" },
  status: "pending",
  created_at: "2026-11-05T00:00:00Z",
  decided_at: null,
  project: { id: PROJECT_ONE_ID, title: "Campus Tutor Finder" },
};

const ACCEPTED_APPLICATION = {
  id: "app-2",
  project_id: PROJECT_TWO_ID,
  applicant: { user_id: ADA.id, display_name: "Ada Lovelace" },
  role: null,
  status: "accepted",
  created_at: "2026-11-01T00:00:00Z",
  decided_at: "2026-11-02T00:00:00Z",
  project: { id: PROJECT_TWO_ID, title: "Study Group Matcher" },
};

describe("MyApplicationsPage", () => {
  it("shows a friendly empty state linking to the projects list", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /me/applications": [200, { items: [], next_offset: null }] });
    renderApp("/applications");

    expect(await screen.findByRole("heading", { name: "My applications" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /projects/i })).toHaveAttribute("href", "/projects");
  });

  it("lists applications with the project, role, status and applied time", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /me/applications": [200, { items: [PENDING_APPLICATION, ACCEPTED_APPLICATION], next_offset: null }],
    });
    renderApp("/applications");

    expect(await screen.findByRole("link", { name: "Campus Tutor Finder" })).toHaveAttribute(
      "href",
      `/projects/${PROJECT_ONE_ID}`,
    );
    expect(screen.getByText("Designer")).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();

    expect(screen.getByRole("link", { name: "Study Group Matcher" })).toHaveAttribute(
      "href",
      `/projects/${PROJECT_TWO_ID}`,
    );
    expect(screen.getByText("Any role")).toBeInTheDocument();
    expect(screen.getByText("Accepted")).toBeInTheDocument();
  });

  it("withdraws a pending application and shows it as Withdrawn", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /me/applications": [200, { items: [PENDING_APPLICATION], next_offset: null }],
      [`POST /projects/${PROJECT_ONE_ID}/applications/${PENDING_APPLICATION.id}/withdraw`]: [204],
    });
    renderApp("/applications");

    await user.click(await screen.findByRole("button", { name: /withdraw/i }));

    expect(await screen.findByText("Withdrawn")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /withdraw/i })).not.toBeInTheDocument();
    expect(api.callsTo(`POST /projects/${PROJECT_ONE_ID}/applications/${PENDING_APPLICATION.id}/withdraw`)).toHaveLength(1);
  });

  it("shows an error when withdrawing fails", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADA],
      "GET /me/applications": [200, { items: [PENDING_APPLICATION], next_offset: null }],
      [`POST /projects/${PROJECT_ONE_ID}/applications/${PENDING_APPLICATION.id}/withdraw`]: apiError(409, "application_not_pending"),
    });
    renderApp("/applications");

    await user.click(await screen.findByRole("button", { name: /withdraw/i }));

    expect(await screen.findByText("This application has already been decided.")).toBeInTheDocument();
  });

  it("loads more applications with Load more", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /me/applications": (call) =>
        call.search.includes("offset=")
          ? [200, { items: [ACCEPTED_APPLICATION], next_offset: null }]
          : [200, { items: [PENDING_APPLICATION], next_offset: 20 }],
    });
    renderApp("/applications");

    await screen.findByRole("link", { name: "Campus Tutor Finder" });
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("link", { name: "Study Group Matcher" })).toBeInTheDocument();
    const offsetCalls = api.callsTo("GET /me/applications").filter((call) => call.search.includes("offset="));
    expect(offsetCalls).toHaveLength(1);
    expect(offsetCalls[0]?.search).toBe("?offset=20");
  });
});
