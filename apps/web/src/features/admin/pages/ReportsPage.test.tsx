import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const reverify = vi.fn();
vi.mock("@/features/admin/components/AdminGate", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/admin/components/AdminGate")>();
  return { ...actual, useAdminGate: () => ({ reverify }) };
});

const ADMIN = { ...ADA, role: "admin" };
// Stubbed in every test regardless of whether AdminGate is still the placeholder or the real
// gate: either way, a real gate would fetch this on the way to rendering its Outlet.
const MFA_VERIFIED = { enrolled: true, verified: true, recovery_codes_left: 10 };

const TARGET_ID = "22222222-2222-4222-8222-222222222222";
const REPORTER_ID = "33333333-3333-4333-8333-333333333333";
const SUSPENDED_TARGET_ID = "44444444-4444-4444-8444-444444444444";

const OPEN_REPORT = {
  id: "report-1",
  reason: "harassment",
  details: "Rude messages",
  status: "open",
  created_at: "2026-11-10T00:00:00Z",
  resolved_at: null,
  reporter: { user_id: REPORTER_ID, display_name: "Grace Hopper" },
  target: { user_id: TARGET_ID, display_name: "Katherine Johnson", suspended: false },
};

const RESOLVED_REPORT = {
  id: "report-2",
  reason: "spam",
  details: "Spam content",
  status: "resolved",
  created_at: "2026-11-09T00:00:00Z",
  resolved_at: "2026-11-09T01:00:00Z",
  reporter: null,
  target: { user_id: SUSPENDED_TARGET_ID, display_name: null, suspended: true },
};

describe("ReportsPage", () => {
  beforeEach(() => {
    reverify.mockClear();
  });

  it("lists open reports by default with the reason, target, reporter and a link to the detail", async () => {
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      "GET /admin/reports": (call) =>
        call.search.includes("status=open")
          ? [200, { items: [OPEN_REPORT], next_offset: null }]
          : [200, { items: [], next_offset: null }],
    });
    renderApp("/admin");

    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Open" })).toHaveAttribute("aria-selected", "true");
    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(within(rows[0]!).getByRole("link")).toHaveAttribute("href", "/admin/reports/report-1");
    expect(within(rows[0]!).getByText("Harassment")).toBeInTheDocument();
    expect(within(rows[0]!).getByText(/Katherine Johnson/)).toBeInTheDocument();
    expect(within(rows[0]!).getByText(/Grace Hopper/)).toBeInTheDocument();
  });

  it("shows 'A student' and 'Deleted account' when a display name or the reporter is missing, plus a Suspended badge", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      "GET /admin/reports": (call) =>
        call.search.includes("status=resolved")
          ? [200, { items: [RESOLVED_REPORT], next_offset: null }]
          : [200, { items: [], next_offset: null }],
    });
    renderApp("/admin");

    await user.click(await screen.findByRole("tab", { name: "Resolved" }));

    const row = await screen.findByRole("listitem");
    expect(within(row).getByText(/A student/)).toBeInTheDocument();
    expect(within(row).getByText(/Deleted account/)).toBeInTheDocument();
    expect(within(row).getByText("Suspended")).toBeInTheDocument();
  });

  it("switches status filters and shows an empty state per filter", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      "GET /admin/reports": [200, { items: [], next_offset: null }],
    });
    renderApp("/admin");

    expect(await screen.findByText("No open reports.")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Dismissed" }));

    expect(await screen.findByText("No dismissed reports.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Dismissed" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Open" })).toHaveAttribute("aria-selected", "false");
    expect(api.callsTo("GET /admin/reports").some((call) => call.search.includes("status=dismissed"))).toBe(true);
  });

  it("pages Load more from the rows shown, de-duplicated by id", async () => {
    const user = userEvent.setup();
    const SECOND_OPEN = { ...OPEN_REPORT, id: "report-3" };
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      "GET /admin/reports": (call) =>
        call.search.includes("offset=")
          ? [200, { items: [OPEN_REPORT, SECOND_OPEN], next_offset: null }]
          : [200, { items: [OPEN_REPORT], next_offset: 5 }],
    });
    renderApp("/admin");

    await screen.findAllByRole("listitem");
    await user.click(screen.getByRole("button", { name: "Load more" }));

    const rows = await screen.findAllByRole("listitem");
    expect(rows).toHaveLength(2);
    const offsetCalls = api.callsTo("GET /admin/reports").filter((call) => call.search.includes("offset="));
    expect(offsetCalls).toHaveLength(1);
    expect(offsetCalls[0]?.search).toBe("?status=open&offset=1");
  });

  it("calls reverify when the list answers 403 mfa_required", async () => {
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      "GET /admin/reports": apiError(403, "mfa_required"),
    });
    renderApp("/admin");

    await vi.waitFor(() => expect(reverify).toHaveBeenCalled());
  });
});
