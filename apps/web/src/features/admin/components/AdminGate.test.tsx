import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import type { RouteObject } from "react-router";
import { describe, expect, it } from "vitest";

import { routes } from "@/app/routes";
import { useAdminGate } from "@/features/admin/components/AdminGate";
import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const ADMIN = { ...ADA, role: "admin" as const };

const NOT_ENROLLED = { enrolled: false, verified: false, recovery_codes_left: 0 };
const ENROLLED_NOT_VERIFIED = { enrolled: true, verified: false, recovery_codes_left: 10 };
const VERIFIED = { enrolled: true, verified: true, recovery_codes_left: 10 };

/** Replaces the admin route's index child with `element`, to probe `useAdminGate()` directly. */
function withAdminChild(element: ReactNode): RouteObject[] {
  const [root] = routes;
  if (!root) throw new Error("routes must define a root route");
  const children = (root.children ?? []).map((child) =>
    child.path === "admin" ? { ...child, children: [{ index: true, element }] } : child,
  ) as RouteObject[];
  return [{ ...root, children }] as RouteObject[];
}

function ReverifyProbe() {
  const { reverify } = useAdminGate();
  return (
    <button type="button" onClick={reverify}>
      Reverify
    </button>
  );
}

describe("AdminGate: non-admins", () => {
  it("shows the not-found page and never requests MFA status", async () => {
    const api = fakeApi({ "GET /me": [200, ADA] });
    renderApp("/admin");

    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(api.callsTo("GET /admin/mfa")).toHaveLength(0);
  });
});

describe("AdminGate: MFA states", () => {
  it("shows the enrolment screen when the admin hasn't set up an authenticator", async () => {
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, NOT_ENROLLED] });
    renderApp("/admin");

    expect(await screen.findByRole("heading", { name: "Set up two-factor authentication" })).toBeInTheDocument();
  });

  it("shows the verification screen when enrolled but not verified this session", async () => {
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, ENROLLED_NOT_VERIFIED] });
    renderApp("/admin");

    expect(await screen.findByRole("heading", { name: "Verify it's you" })).toBeInTheDocument();
  });

  it("renders the admin area once verified", async () => {
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, VERIFIED] });
    renderApp("/admin");

    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
  });

  it("shows a retry message when the status can't be loaded", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": apiError(503, "not_ready") });
    renderApp("/admin");

    expect(await screen.findByRole("alert")).toHaveTextContent(/Can't load your admin access/);

    api.setRoute("GET /admin/mfa", [200, VERIFIED]);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
  });
});

describe("AdminGate: reverify", () => {
  it("re-checks the status, used after a 403 mfa_required", async () => {
    const user = userEvent.setup();
    const api = fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, VERIFIED] });
    renderApp("/admin", withAdminChild(<ReverifyProbe />));

    expect(await screen.findByRole("button", { name: "Reverify" })).toBeInTheDocument();
    // StrictMode renders effects twice, so the initial load may already show up more than once;
    // what matters is that clicking "Reverify" asks again on top of that.
    const callsBeforeReverify = api.callsTo("GET /admin/mfa").length;

    api.setRoute("GET /admin/mfa", [200, ENROLLED_NOT_VERIFIED]);
    await user.click(screen.getByRole("button", { name: "Reverify" }));

    expect(await screen.findByRole("heading", { name: "Verify it's you" })).toBeInTheDocument();
    expect(api.callsTo("GET /admin/mfa").length).toBeGreaterThan(callsBeforeReverify);
  });
});
