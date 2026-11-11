import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const PROJECT_ID = "project-1";
const APPLICANT_ID = "22222222-2222-4222-8222-222222222222";
const OTHER_APPLICANT_ID = "33333333-3333-4333-8333-333333333333";
const DECIDED_APPLICANT_ID = "44444444-4444-4444-8444-444444444444";
const ROLE_ID = "role-1";

const PROJECT = {
  id: PROJECT_ID,
  title: "Campus Tutor Finder",
  summary: "",
  capacity: 4,
  status: "open",
  member_count: 2,
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-01T00:00:00Z",
  owner: { user_id: ADA.id, display_name: "Ada Lovelace" },
  roles: [{ id: ROLE_ID, title: "Designer", open_slots: 2, remaining_slots: 1, skills: [] }],
  members: [],
  missing_skills: [],
  conversation_id: "conv-team",
  viewer_application: null,
};

const PENDING_APP = {
  id: "app-1",
  project_id: PROJECT_ID,
  applicant: { user_id: APPLICANT_ID, display_name: "Grace Hopper" },
  role: { id: ROLE_ID, title: "Designer" },
  status: "pending",
  created_at: "2026-11-05T00:00:00Z",
  decided_at: null,
};

const ANY_ROLE_PENDING = {
  id: "app-2",
  project_id: PROJECT_ID,
  applicant: { user_id: OTHER_APPLICANT_ID, display_name: null },
  role: null,
  status: "pending",
  created_at: "2026-11-05T01:00:00Z",
  decided_at: null,
};

const DECIDED_APP = {
  id: "app-3",
  project_id: PROJECT_ID,
  applicant: { user_id: DECIDED_APPLICANT_ID, display_name: "Katherine Johnson" },
  role: null,
  status: "rejected",
  created_at: "2026-11-04T00:00:00Z",
  decided_at: "2026-11-04T12:00:00Z",
};

describe("ProjectApplicationsPage", () => {
  it("shows the project header with team size and places left per role", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: () => [200, { items: [], next_offset: null }],
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    expect(await screen.findByRole("link", { name: "Campus Tutor Finder" })).toHaveAttribute(
      "href",
      `/projects/${PROJECT_ID}`,
    );
    expect(screen.getByText("2 of 4 members")).toBeInTheDocument();
    expect(screen.getByText("Designer: 1 place left")).toBeInTheDocument();
    expect(screen.getByText("No applications waiting.")).toBeInTheDocument();
  });

  it("lists pending applications with the applicant, role and applied time", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) =>
        call.search.includes("status=pending")
          ? [200, { items: [PENDING_APP, ANY_ROLE_PENDING], next_offset: null }]
          : [200, { items: [], next_offset: null }],
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    expect(await screen.findByRole("link", { name: "Grace Hopper" })).toHaveAttribute("href", `/people/${APPLICANT_ID}`);
    expect(screen.getByText("Designer")).toBeInTheDocument();
    expect(screen.getByText("Any role")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "A student" })).toHaveAttribute("href", `/people/${OTHER_APPLICANT_ID}`);
  });

  it("accepts a pending application and moves it to Decided", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) =>
        call.search.includes("status=pending")
          ? [200, { items: [PENDING_APP], next_offset: null }]
          : [200, { items: [], next_offset: null }],
      [`POST /projects/${PROJECT_ID}/applications/${PENDING_APP.id}/accept`]: [
        200,
        { ...PENDING_APP, status: "accepted", decided_at: "2026-11-05T02:00:00Z" },
      ],
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    await user.click(await screen.findByRole("button", { name: "Accept Grace Hopper" }));

    expect(await screen.findByText("No applications waiting.")).toBeInTheDocument();
    expect(screen.getByText("Accepted")).toBeInTheDocument();
    expect(api.callsTo(`POST /projects/${PROJECT_ID}/applications/${PENDING_APP.id}/accept`)).toHaveLength(1);
  });

  it("refreshes the header's member count and places left after a successful accept", async () => {
    const user = userEvent.setup();
    let accepted = false;
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: () => [
        200,
        accepted ? { ...PROJECT, member_count: 3, roles: [{ ...PROJECT.roles[0], remaining_slots: 0 }] } : PROJECT,
      ],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) =>
        call.search.includes("status=pending")
          ? [200, { items: [PENDING_APP], next_offset: null }]
          : [200, { items: [], next_offset: null }],
      [`POST /projects/${PROJECT_ID}/applications/${PENDING_APP.id}/accept`]: () => {
        accepted = true;
        return [200, { ...PENDING_APP, status: "accepted", decided_at: "2026-11-05T02:00:00Z" }];
      },
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);
    await screen.findByRole("button", { name: "Accept Grace Hopper" });
    const callsBeforeAccept = api.callsTo(`GET /projects/${PROJECT_ID}`).length;

    await user.click(screen.getByRole("button", { name: "Accept Grace Hopper" }));

    expect(await screen.findByText("3 of 4 members")).toBeInTheDocument();
    expect(screen.getByText("Designer: 0 places left")).toBeInTheDocument();
    expect(api.callsTo(`GET /projects/${PROJECT_ID}`).length).toBeGreaterThan(callsBeforeAccept);
  });

  it("shows 'That place was just filled.' on a 409 and reloads the project and queue", async () => {
    const user = userEvent.setup();
    let accepted = false;
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: () => [200, accepted ? { ...PROJECT, member_count: 3 } : PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) => {
        if (call.search.includes("status=pending")) return [200, { items: accepted ? [] : [PENDING_APP], next_offset: null }];
        return [200, { items: [], next_offset: null }];
      },
      [`POST /projects/${PROJECT_ID}/applications/${PENDING_APP.id}/accept`]: () => {
        accepted = true;
        return apiError(409, "capacity_reached");
      },
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    await user.click(await screen.findByRole("button", { name: "Accept Grace Hopper" }));

    expect(await screen.findByText("That place was just filled.")).toBeInTheDocument();
    expect(await screen.findByText("No applications waiting.")).toBeInTheDocument();
    expect(screen.getByText("3 of 4 members")).toBeInTheDocument();
  });

  it("loads more pending applications with Load more", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) => {
        if (call.search.includes("status=pending")) {
          return call.search.includes("offset=")
            ? [200, { items: [ANY_ROLE_PENDING], next_offset: null }]
            : [200, { items: [PENDING_APP], next_offset: 20 }];
        }
        return [200, { items: [], next_offset: null }];
      },
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    await screen.findByRole("link", { name: "Grace Hopper" });
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("link", { name: "A student" })).toBeInTheDocument();
    const offsetCalls = api.callsTo(`GET /projects/${PROJECT_ID}/applications`).filter((call) => call.search.includes("offset="));
    expect(offsetCalls).toHaveLength(1);
    // Offset comes from the single row still shown, not the server's original next_offset (20):
    // see the "pages from the rows still shown" test below for why that distinction matters.
    expect(offsetCalls[0]?.search).toBe("?status=pending&offset=1");
  });

  it("pages Pending from the rows still shown after deciding one, so no application is skipped", async () => {
    const user = userEvent.setup();
    const THIRD_PENDING = {
      id: "app-99",
      project_id: PROJECT_ID,
      applicant: { user_id: "55555555-5555-4555-8555-555555555555", display_name: "Rosalind Franklin" },
      role: null,
      status: "pending",
      created_at: "2026-11-05T02:00:00Z",
      decided_at: null,
    };
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) => {
        if (call.search.includes("status=pending")) {
          // The server's own next_offset (2) would skip THIRD_PENDING once a row has been
          // decided and removed from the two shown here; offset must come from the client's
          // remaining row count (1) instead.
          return call.search.includes("offset=")
            ? [200, { items: [THIRD_PENDING], next_offset: null }]
            : [200, { items: [PENDING_APP, ANY_ROLE_PENDING], next_offset: 2 }];
        }
        return [200, { items: [], next_offset: null }];
      },
      [`POST /projects/${PROJECT_ID}/applications/${PENDING_APP.id}/accept`]: [
        200,
        { ...PENDING_APP, status: "accepted", decided_at: "2026-11-05T03:00:00Z" },
      ],
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    await user.click(await screen.findByRole("button", { name: "Accept Grace Hopper" }));
    await screen.findByText("Accepted");
    // Grace Hopper moved to Decided; only the Pending row (with its Accept button) is gone.
    expect(screen.queryByRole("button", { name: "Accept Grace Hopper" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByRole("link", { name: "Rosalind Franklin" })).toBeInTheDocument();
    const pendingOffsetCalls = api
      .callsTo(`GET /projects/${PROJECT_ID}/applications`)
      .filter((call) => call.search.includes("status=pending") && call.search.includes("offset="));
    expect(pendingOffsetCalls).toHaveLength(1);
    expect(pendingOffsetCalls[0]?.search).toBe("?status=pending&offset=1");
  });

  it("fetches Decided with its own multi-status query and its own Load more", async () => {
    const user = userEvent.setup();
    const SECOND_DECIDED = {
      id: "app-100",
      project_id: PROJECT_ID,
      applicant: { user_id: "66666666-6666-4666-8666-666666666666", display_name: "Ada Yonath" },
      role: null,
      status: "withdrawn",
      created_at: "2026-11-03T00:00:00Z",
      decided_at: "2026-11-03T01:00:00Z",
    };
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) => {
        if (call.search.includes("status=pending")) return [200, { items: [], next_offset: null }];
        if (
          call.search.includes("status=accepted") &&
          call.search.includes("status=rejected") &&
          call.search.includes("status=withdrawn")
        ) {
          return call.search.includes("offset=")
            ? [200, { items: [SECOND_DECIDED], next_offset: null }]
            : [200, { items: [DECIDED_APP], next_offset: 1 }];
        }
        return [200, { items: [], next_offset: null }];
      },
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    expect(await screen.findByRole("link", { name: "Katherine Johnson" })).toBeInTheDocument();
    expect(screen.queryByText("No decisions yet.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Load more decided applications" }));

    expect(await screen.findByRole("link", { name: "Ada Yonath" })).toBeInTheDocument();
    const decidedOffsetCalls = api
      .callsTo(`GET /projects/${PROJECT_ID}/applications`)
      .filter((call) => call.search.includes("offset=") && call.search.includes("status=accepted"));
    expect(decidedOffsetCalls).toHaveLength(1);
    expect(decidedOffsetCalls[0]?.search).toBe("?status=accepted&status=rejected&status=withdrawn&offset=1");
  });

  it("shows decided applications below pending, with their status", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: (call) =>
        call.search.includes("status=pending") ? [200, { items: [], next_offset: null }] : [200, { items: [DECIDED_APP], next_offset: null }],
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    expect(await screen.findByRole("link", { name: "Katherine Johnson" })).toHaveAttribute(
      "href",
      `/people/${DECIDED_APPLICANT_ID}`,
    );
    expect(screen.getByText("Not accepted")).toBeInTheDocument();
  });

  it("shows a message when someone other than the owner tries to view the queue", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: [200, PROJECT],
      [`GET /projects/${PROJECT_ID}/applications`]: apiError(403, "forbidden"),
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    expect(await screen.findByText("Only the project's owner can see its applications.")).toBeInTheDocument();
  });

  it("shows a message for a project that isn't available", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /projects/${PROJECT_ID}`]: apiError(404, "not_found"),
    });
    renderApp(`/projects/${PROJECT_ID}/applications`);

    expect(await screen.findByText("This project isn't available.")).toBeInTheDocument();
  });
});
