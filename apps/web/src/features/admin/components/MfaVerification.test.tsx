import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const ADMIN = { ...ADA, role: "admin" as const };

function enrolledNotVerified(recoveryCodesLeft: number) {
  return { enrolled: true, verified: false, recovery_codes_left: recoveryCodesLeft };
}

describe("MfaVerification: by authenticator code", () => {
  it("verifies with a 6-digit code and continues to the admin area", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, enrolledNotVerified(10)],
      "POST /admin/mfa/verify": [200, { enrolled: true, verified: true, recovery_codes_left: 10 }],
    });
    renderApp("/admin");

    const field = await screen.findByLabelText("Authentication code");
    expect(field).toHaveAttribute("inputmode", "numeric");
    expect(field).toHaveAttribute("autocomplete", "one-time-code");

    await user.type(field, "654321");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
    expect(api.callsTo("POST /admin/mfa/verify")[0]?.body).toEqual({ code: "654321" });
  });

  it("shows the error copy for a wrong code", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, enrolledNotVerified(10)],
      "POST /admin/mfa/verify": apiError(422, "invalid_code"),
    });
    renderApp("/admin");

    await user.type(await screen.findByLabelText("Authentication code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("That code didn't work. Check the time on your device and try again.")).toBeInTheDocument();
  });

  it("shows the rate-limit copy when throttled", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, enrolledNotVerified(10)],
      "POST /admin/mfa/verify": apiError(429, "rate_limited"),
    });
    renderApp("/admin");

    await user.type(await screen.findByLabelText("Authentication code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Too many attempts. Wait a few minutes and try again.")).toBeInTheDocument();
  });
});

describe("MfaVerification: by recovery code", () => {
  it("toggles to a recovery code and verifies with it", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, enrolledNotVerified(9)],
      "POST /admin/mfa/verify": [200, { enrolled: true, verified: true, recovery_codes_left: 8 }],
    });
    renderApp("/admin");

    await user.click(await screen.findByRole("button", { name: "Use a recovery code instead" }));
    expect(screen.queryByLabelText("Authentication code")).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Recovery code"), "ABCD-EFGH-1234");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
    expect(api.callsTo("POST /admin/mfa/verify")[0]?.body).toEqual({ recovery_code: "ABCD-EFGH-1234" });
  });

  it("switches back to the code field", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, enrolledNotVerified(10)] });
    renderApp("/admin");

    await user.click(await screen.findByRole("button", { name: "Use a recovery code instead" }));
    await user.click(screen.getByRole("button", { name: "Use an authentication code instead" }));

    expect(await screen.findByLabelText("Authentication code")).toBeInTheDocument();
  });
});

describe("MfaVerification: low recovery codes warning", () => {
  it("warns when 2 or fewer recovery codes remain", async () => {
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, enrolledNotVerified(2)] });
    renderApp("/admin");

    expect(await screen.findByText("Only 2 recovery codes left.")).toBeInTheDocument();
  });

  it("warns distinctly when there are none left", async () => {
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, enrolledNotVerified(0)] });
    renderApp("/admin");

    expect(await screen.findByText(/You have no recovery codes left/)).toBeInTheDocument();
  });

  it("stays quiet with plenty of recovery codes left", async () => {
    fakeApi({ "GET /me": [200, ADMIN], "GET /admin/mfa": [200, enrolledNotVerified(10)] });
    renderApp("/admin");

    await screen.findByRole("heading", { name: "Verify it's you" });
    expect(screen.queryByText(/recovery codes left/i)).not.toBeInTheDocument();
  });
});
