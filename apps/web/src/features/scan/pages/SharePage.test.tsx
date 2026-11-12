import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const ACTIVE_CODE = {
  status: "active" as const,
  // Noon UTC, not midnight: a midnight timestamp can land on the previous day once formatted in
  // a timezone behind UTC, which would make the "Expires" assertion flaky.
  url: "http://localhost/connect#token=" + "a".repeat(43),
  expires_at: "2026-12-05T12:00:00Z",
  shareable: true,
};

const OFF_CODE = { status: "off" as const, url: null, expires_at: null, shareable: true };
const EXPIRED_CODE = { status: "expired" as const, url: null, expires_at: "2026-10-01T12:00:00Z", shareable: true };

afterEach(() => {
  // Test-only cleanup of the stub `stubClipboard` installs below.
  if (Object.getOwnPropertyDescriptor(navigator, "clipboard")) {
    // @ts-expect-error -- deleting the own property we defined restores jsdom's own accessor.
    delete navigator.clipboard;
  }
});

/** `userEvent.setup()` installs its own clipboard handling and resets `navigator.clipboard` to
 * jsdom's built-in implementation as a side effect, so this must run after `setup()`, not before. */
function stubClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
  return writeText;
}

describe("SharePage: active code", () => {
  it("shows the QR code, the link, and when it expires", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /me/connection-code": [200, ACTIVE_CODE] });
    renderApp("/share");

    expect(await screen.findByRole("heading", { name: "Share your profile" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "QR code for your profile link" })).toBeInTheDocument();
    expect(screen.getByDisplayValue(ACTIVE_CODE.url)).toBeInTheDocument();
    expect(screen.getByText(/Expires/)).toHaveTextContent("Expires Dec 5, 2026");
  });

  it("copies the link to the clipboard", async () => {
    const user = userEvent.setup();
    const writeText = stubClipboard();
    fakeApi({ "GET /me": [200, ADA], "GET /me/connection-code": [200, ACTIVE_CODE] });
    renderApp("/share");

    await user.click(await screen.findByRole("button", { name: "Copy link" }));

    expect(writeText).toHaveBeenCalledWith(ACTIVE_CODE.url);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("rotates the code after a confirm", async () => {
    const user = userEvent.setup();
    const NEW_CODE = { ...ACTIVE_CODE, url: "http://localhost/connect#token=" + "b".repeat(43) };
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, ACTIVE_CODE],
      "POST /me/connection-code/rotate": [201, NEW_CODE],
    });
    renderApp("/share");

    await user.click(await screen.findByRole("button", { name: "Get a new code" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Get a new code" }));

    expect(await screen.findByDisplayValue(NEW_CODE.url)).toBeInTheDocument();
    expect(api.callsTo("POST /me/connection-code/rotate")).toHaveLength(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("does not rotate without confirming", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, ACTIVE_CODE],
      "POST /me/connection-code/rotate": [201, ACTIVE_CODE],
    });
    renderApp("/share");

    await user.click(await screen.findByRole("button", { name: "Get a new code" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(api.callsTo("POST /me/connection-code/rotate")).toHaveLength(0);
  });

  it("shows a server error without losing the current code", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, ACTIVE_CODE],
      "POST /me/connection-code/rotate": apiError(429, "rate_limited"),
    });
    renderApp("/share");

    await user.click(await screen.findByRole("button", { name: "Get a new code" }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Get a new code" }));

    expect(await screen.findByText("Too many attempts. Wait a few minutes and try again.")).toBeInTheDocument();
    expect(screen.getByDisplayValue(ACTIVE_CODE.url)).toBeInTheDocument();
  });

  it("turns off sharing", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, ACTIVE_CODE],
      "DELETE /me/connection-code": [204],
    });
    renderApp("/share");

    await user.click(await screen.findByRole("button", { name: "Turn off sharing" }));

    expect(await screen.findByRole("button", { name: "Create a code" })).toBeInTheDocument();
    expect(api.callsTo("DELETE /me/connection-code")).toHaveLength(1);
  });

  it("warns when the profile is hidden from new connections and links to the profile page", async () => {
    fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, { ...ACTIVE_CODE, shareable: false }],
    });
    renderApp("/share");

    expect(
      await screen.findByText(
        "Your profile is hidden from discovery, so only students you're already connected with will see it when they scan your code.",
      ),
    ).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Turn on discovery" });
    expect(link).toHaveAttribute("href", "/profile");
  });
});

describe("SharePage: off and expired", () => {
  it("explains sharing is off and offers to create a code", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      "GET /me/connection-code": [200, OFF_CODE],
      "POST /me/connection-code/rotate": [201, { ...ACTIVE_CODE }],
    });
    renderApp("/share");

    expect(await screen.findByRole("button", { name: "Create a code" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create a code" }));

    expect(await screen.findByDisplayValue(ACTIVE_CODE.url)).toBeInTheDocument();
    expect(api.callsTo("POST /me/connection-code/rotate")).toHaveLength(1);
  });

  it("explains an expired code and offers to create a new one", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /me/connection-code": [200, EXPIRED_CODE] });
    renderApp("/share");

    expect(await screen.findByRole("button", { name: "Create a code" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "QR code for your profile link" })).not.toBeInTheDocument();
  });
});

describe("SharePage: loading, error and scanning", () => {
  it("shows a loading state, then an error if the code can't be fetched", async () => {
    fakeApi({ "GET /me": [200, ADA], "GET /me/connection-code": apiError(500, "server_error") });
    renderApp("/share");

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("opens the scan dialog from a button press", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, ADA], "GET /me/connection-code": [200, OFF_CODE] });
    renderApp("/share");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "Scan a code" }));

    expect(await screen.findByRole("dialog", { name: "Scan a code" })).toBeInTheDocument();
  });
});
