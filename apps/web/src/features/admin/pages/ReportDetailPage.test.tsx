import { screen, waitFor, within } from "@testing-library/react";
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
const MFA_VERIFIED = { enrolled: true, verified: true, recovery_codes_left: 10 };

const REPORT_ID = "report-1";
const TARGET_ID = "22222222-2222-4222-8222-222222222222";
const REPORTER_ID = "33333333-3333-4333-8333-333333333333";

const DETAIL = {
  id: REPORT_ID,
  reason: "harassment",
  details: "Line one\nLine two",
  status: "open",
  created_at: "2026-11-10T00:00:00Z",
  resolved_at: null,
  reporter: { user_id: REPORTER_ID, display_name: "Grace Hopper" },
  target: { user_id: TARGET_ID, display_name: "Katherine Johnson", suspended: false },
  target_context: {
    headline: "Full-stack developer",
    member_since: "2025-09-01T00:00:00Z",
    verified: true,
    open_reports: 1,
    total_reports: 3,
  },
};

describe("ReportDetailPage", () => {
  beforeEach(() => {
    reverify.mockClear();
  });

  it("shows the reason, details with line breaks preserved, when, reporter, target context and status", async () => {
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: [200, DETAIL],
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    expect(await screen.findByText("Harassment")).toBeInTheDocument();
    // The newline inside `details` must survive into the rendered text (preserved line breaks).
    expect(screen.getByText((_, element) => element?.textContent === "Line one\nLine two")).toBeInTheDocument();
    expect(screen.getByText("Grace Hopper")).toBeInTheDocument();
    expect(screen.getByText("Katherine Johnson")).toBeInTheDocument();
    expect(screen.getByText("Full-stack developer")).toBeInTheDocument();
    expect(screen.getByText(/1 open of 3 total/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Open");
    expect(screen.getByRole("link", { name: "Back to reports" })).toHaveAttribute("href", "/admin");
  });

  it("dismisses an open report and shows the new status", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: [200, DETAIL],
      [`POST /admin/reports/${REPORT_ID}/dismiss`]: [
        200,
        { ...DETAIL, status: "dismissed", resolved_at: "2026-11-11T00:00:00Z" },
      ],
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    expect(await screen.findByText("Dismissed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Dismiss" })).not.toBeInTheDocument();
    expect(api.callsTo(`POST /admin/reports/${REPORT_ID}/dismiss`)).toHaveLength(1);
  });

  it("suspends the target behind a confirm dialog naming them", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: [200, DETAIL],
      [`POST /admin/reports/${REPORT_ID}/suspend`]: [
        200,
        { ...DETAIL, status: "resolved", target: { ...DETAIL.target, suspended: true } },
      ],
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    await user.click(await screen.findByRole("button", { name: "Suspend account" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Suspend Katherine Johnson?")).toBeInTheDocument();
    expect(within(dialog).getByText("They're signed out everywhere and can't sign in again.")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: "Suspend account" }));

    expect(await screen.findByText("Resolved")).toBeInTheDocument();
    expect(await screen.findByText("Suspended")).toBeInTheDocument();
    expect(api.callsTo(`POST /admin/reports/${REPORT_ID}/suspend`)).toHaveLength(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("disables Dismiss and Suspend account while a decision is in flight", async () => {
    const user = userEvent.setup();
    let resolveDismiss: (() => void) | undefined;
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: [200, DETAIL],
      [`POST /admin/reports/${REPORT_ID}/dismiss`]: () =>
        new Promise((resolve) => {
          resolveDismiss = () => resolve([200, { ...DETAIL, status: "dismissed" }]);
        }),
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    expect(screen.getByRole("button", { name: "Dismiss" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Suspend account" })).toBeDisabled();

    resolveDismiss?.();
    await screen.findByText("Dismissed");
  });

  it("shows the error and reloads on a 409", async () => {
    const user = userEvent.setup();
    let dismissed = false;
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: () => [200, dismissed ? { ...DETAIL, status: "dismissed" } : DETAIL],
      [`POST /admin/reports/${REPORT_ID}/dismiss`]: () => {
        dismissed = true;
        return apiError(409, "report_closed");
      },
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    // The copy for `report_closed` comes from the shared auth error map (errorMessage).
    expect(await screen.findByText("This report was already handled.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("Dismissed")).toBeInTheDocument());
  });

  it("calls reverify on a 403 mfa_required while suspending", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: [200, DETAIL],
      [`POST /admin/reports/${REPORT_ID}/suspend`]: apiError(403, "mfa_required"),
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    await user.click(await screen.findByRole("button", { name: "Suspend account" }));
    await user.click(await screen.findByRole("button", { name: "Suspend account" }));

    await waitFor(() => expect(reverify).toHaveBeenCalled());
  });

  it("shows a not-available message for a missing report", async () => {
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: apiError(404, "not_found"),
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    expect(await screen.findByText("This report isn't available.")).toBeInTheDocument();
  });

  it("shows a not-available message and reloads to the not-available state on a 404 from an action", async () => {
    const user = userEvent.setup();
    let dismissed = false;
    let resolveReload: (() => void) | undefined;
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, MFA_VERIFIED],
      [`GET /admin/reports/${REPORT_ID}`]: () => {
        // The initial load (and any StrictMode re-run before the dismiss) succeeds; only the
        // reload after the 404 is held open, so the transient error is observable before the
        // page swaps to the not-available state.
        if (!dismissed) return [200, DETAIL];
        return new Promise((resolve) => {
          resolveReload = () => resolve(apiError(404, "not_found"));
        });
      },
      [`POST /admin/reports/${REPORT_ID}/dismiss`]: () => {
        dismissed = true;
        return apiError(404, "not_found");
      },
    });
    renderApp(`/admin/reports/${REPORT_ID}`);

    await user.click(await screen.findByRole("button", { name: "Dismiss" }));

    expect(await screen.findByText("This report isn't available anymore.")).toBeInTheDocument();

    resolveReload?.();

    expect(await screen.findByText("This report isn't available.")).toBeInTheDocument();
  });
});
