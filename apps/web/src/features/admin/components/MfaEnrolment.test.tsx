import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const ADMIN = { ...ADA, role: "admin" as const };
const NOT_ENROLLED = { enrolled: false, verified: false, recovery_codes_left: 0 };
const ENROLMENT = { secret: "JBSWY3DPEHPK3PXP", otpauth_url: "otpauth://totp/NU%20Connect%20Hub:ada@northeastern.edu" };
const RECOVERY_CODES = Array.from({ length: 10 }, (_, index) => `ABCD-EFGH-${String(index).padStart(4, "0")}`);

afterEach(() => {
  if (Object.getOwnPropertyDescriptor(navigator, "clipboard")) {
    // @ts-expect-error -- deleting the own property we defined restores jsdom's own accessor.
    delete navigator.clipboard;
  }
});

function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

async function startEnrolment(user: ReturnType<typeof userEvent.setup>) {
  expect(await screen.findByRole("heading", { name: "Set up two-factor authentication" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Set up your authenticator" }));
  expect(await screen.findByRole("img", { name: "QR code to scan with your authenticator app" })).toBeInTheDocument();
}

describe("MfaEnrolment", () => {
  it("shows the QR code and the secret for manual entry", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, NOT_ENROLLED],
      "POST /admin/mfa/enroll": [200, ENROLMENT],
    });
    renderApp("/admin");

    await startEnrolment(user);
    expect(screen.getByText(ENROLMENT.secret)).toBeInTheDocument();
  });

  it("confirms with a code and shows the recovery codes once, then continues in", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, NOT_ENROLLED],
      "POST /admin/mfa/enroll": [200, ENROLMENT],
      "POST /admin/mfa/confirm": [200, { recovery_codes: RECOVERY_CODES }],
    });
    renderApp("/admin");

    await startEnrolment(user);
    await user.type(screen.getByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("heading", { name: "Save your recovery codes" })).toBeInTheDocument();
    for (const code of RECOVERY_CODES) expect(screen.getByText(code)).toBeInTheDocument();
    expect(api.callsTo("POST /admin/mfa/confirm")[0]?.body).toEqual({ code: "123456" });

    // Confirming already marked this session verified: the next status read says so.
    api.setRoute("GET /admin/mfa", [200, { enrolled: true, verified: true, recovery_codes_left: 10 }]);
    await user.click(screen.getByRole("button", { name: "I've saved these codes" }));

    expect(await screen.findByRole("heading", { name: "Reports" })).toBeInTheDocument();
  });

  it("copies the recovery codes to the clipboard", async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, NOT_ENROLLED],
      "POST /admin/mfa/enroll": [200, ENROLMENT],
      "POST /admin/mfa/confirm": [200, { recovery_codes: RECOVERY_CODES }],
    });
    renderApp("/admin");

    await startEnrolment(user);
    await user.type(screen.getByLabelText("Authentication code"), "123456");
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await screen.findByRole("heading", { name: "Save your recovery codes" });

    await user.click(screen.getByRole("button", { name: "Copy codes" }));

    expect(writeText).toHaveBeenCalledWith(RECOVERY_CODES.join("\n"));
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("shows the error copy for a wrong code", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADMIN],
      "GET /admin/mfa": [200, NOT_ENROLLED],
      "POST /admin/mfa/enroll": [200, ENROLMENT],
      "POST /admin/mfa/confirm": apiError(422, "invalid_code"),
    });
    renderApp("/admin");

    await startEnrolment(user);
    await user.type(screen.getByLabelText("Authentication code"), "000000");
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("That code didn't work. Check the time on your device and try again.")).toBeInTheDocument();
  });
});
