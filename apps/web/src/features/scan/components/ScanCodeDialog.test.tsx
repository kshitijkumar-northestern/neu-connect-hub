import { useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import jsQR from "jsqr";
import type { RouteObject } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { routes } from "@/app/routes";
import { ScanCodeDialog } from "@/features/scan/components/ScanCodeDialog";
import { ADA, fakeApi } from "@/test/fake-api";
import { FakeMediaStream, stubGetUserMedia } from "@/test/fake-webrtc";
import { renderApp } from "@/test/render-app";

vi.mock("jsqr", () => ({ default: vi.fn() }));

const TOKEN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLM1234"; // 43 chars of [A-Za-z0-9_-]
const CONNECT_LINK = () => `${window.location.origin}/connect#token=${TOKEN}`;
const RESOLVE = "POST /connect/resolve"; // the token goes in the body, never the URL
const PREVIEW = {
  user_id: "22222222-2222-4222-8222-222222222222",
  display_name: "Grace Hopper",
  headline: "Looking for teammates",
  connection_state: "none",
};

/** A QRCode-shaped value, as `jsQR` returns (only `.data` is ever read). */
function decodedAs(data: string) {
  return { data } as unknown as ReturnType<typeof jsQR>;
}

function Harness() {
  const [open, setOpen] = useState(true);
  return (
    <>
      {!open ? (
        <button type="button" onClick={() => setOpen(true)}>
          Reopen
        </button>
      ) : null}
      <ScanCodeDialog open={open} onOpenChange={setOpen} />
    </>
  );
}

/** The real route table plus the dialog under test, so a successful scan's navigation to
 * `/connect` lands on the real guarded route. */
function withScanDialog(): RouteObject[] {
  const [root] = routes;
  if (!root) throw new Error("routes must define a root route");
  return [
    {
      path: "/",
      element: root.element,
      errorElement: root.errorElement,
      children: [{ path: "scan-test", element: <Harness /> }, ...(root.children ?? [])],
    },
  ];
}

function installFakeCanvasContext() {
  const context = {
    drawImage: vi.fn(),
    getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(4), width: 1, height: 1 })),
  };
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = vi.fn(() => context) as unknown as typeof original;
  return () => {
    HTMLCanvasElement.prototype.getContext = original;
  };
}

/** jsdom never fires a real `<img>` load event for a blob URL, so this fakes one firing `onload`
 * on the next microtask with fixed dimensions — enough for the upload path's canvas draw. */
class FakeImage {
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  naturalWidth = 10;
  naturalHeight = 10;
  #src = "";
  get src() {
    return this.#src;
  }
  set src(value: string) {
    this.#src = value;
    queueMicrotask(() => this.onload?.());
  }
}

function installFakeImage() {
  const original = globalThis.Image;
  (globalThis as { Image: unknown }).Image = FakeImage;
  return () => {
    (globalThis as { Image: unknown }).Image = original;
  };
}

/** jsdom's `<video>` never reports a real frame (`readyState` stays `HAVE_NOTHING`), so the scan
 * loop would skip every tick as "nothing to decode yet" without this. */
function installFakeVideoReadyState() {
  const original = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, "readyState");
  Object.defineProperty(HTMLMediaElement.prototype, "readyState", {
    configurable: true,
    get: () => HTMLMediaElement.HAVE_CURRENT_DATA,
  });
  return () => {
    if (original) Object.defineProperty(HTMLMediaElement.prototype, "readyState", original);
  };
}

/** Controls `getUserMedia` resolution by hand, for tests racing the dialog closing (or a second
 * click) against a still-pending camera permission prompt. */
function stubPendingGetUserMedia(): { getUserMedia: ReturnType<typeof vi.fn>; resolve: (stream: FakeMediaStream) => void } {
  let resolve!: (stream: FakeMediaStream) => void;
  const getUserMedia = vi.fn(() => new Promise<FakeMediaStream>((res) => (resolve = res)));
  Object.defineProperty(globalThis.navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
  return { getUserMedia, resolve: (stream) => resolve(stream) };
}

let uninstallCanvas: () => void;
let uninstallImage: () => void;
let uninstallReadyState: () => void;

beforeEach(() => {
  uninstallCanvas = installFakeCanvasContext();
  uninstallImage = installFakeImage();
  uninstallReadyState = installFakeVideoReadyState();
});

afterEach(() => {
  uninstallCanvas();
  uninstallImage();
  uninstallReadyState();
});

describe("ScanCodeDialog", () => {
  it("does not request the camera until Start camera is pressed", async () => {
    const getUserMedia = stubGetUserMedia();
    fakeApi({ "GET /me": [200, ADA] });
    renderApp("/scan-test", withScanDialog());

    expect(await screen.findByRole("heading", { name: "Scan a code" })).toBeInTheDocument();
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("shows the fallback copy when the camera is denied", async () => {
    stubGetUserMedia(new Error("Permission denied"));
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    renderApp("/scan-test", withScanDialog());

    await user.click(await screen.findByRole("button", { name: "Start camera" }));

    expect(
      await screen.findByText("We can't use your camera. Paste the link or upload a photo of the code instead."),
    ).toBeInTheDocument();
    // Still lets the student try again.
    expect(screen.getByRole("button", { name: "Start camera" })).toBeInTheDocument();
  });

  it("navigates to the decoded link and stops every track once the camera decodes a valid code", async () => {
    vi.mocked(jsQR).mockReturnValue(decodedAs(CONNECT_LINK()));
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    const api = fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, PREVIEW] });
    const user = userEvent.setup();
    const { router } = renderApp("/scan-test", withScanDialog());

    await user.click(await screen.findByRole("button", { name: "Start camera" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/connect"));
    // ConnectPage's own effect resolves the token (StrictMode may run it twice); what matters
    // here is that the dialog handed the right token off at all, and stopped the camera.
    await waitFor(() => expect(api.callsTo(RESOLVE).length).toBeGreaterThan(0));
    expect(api.callsTo(RESOLVE)[0]?.body).toEqual({ token: TOKEN });
    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
  });

  it("shows the invalid-code message and keeps scanning after a camera frame that isn't a link", async () => {
    vi.mocked(jsQR).mockReturnValue(decodedAs("https://example.com/not-a-connect-link"));
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    renderApp("/scan-test", withScanDialog());

    await user.click(await screen.findByRole("button", { name: "Start camera" }));

    expect(await screen.findByText("That isn't a Scan to Connect code.")).toBeInTheDocument();
    // The camera is still running, not reset back to the idle "Start camera" prompt.
    expect(screen.queryByRole("button", { name: "Start camera" })).not.toBeInTheDocument();
    expect(stream.getTracks().every((track) => track.readyState === "live")).toBe(true);
  });

  it("accepts a pasted link", async () => {
    const api = fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, PREVIEW] });
    const user = userEvent.setup();
    const { router } = renderApp("/scan-test", withScanDialog());

    await user.type(await screen.findByLabelText("Paste a link"), CONNECT_LINK());
    await user.click(screen.getByRole("button", { name: "Open" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/connect"));
    expect(api.callsTo(RESOLVE).length).toBeGreaterThan(0);
  });

  it("shows the invalid-code message for a pasted link that isn't a Scan to Connect code", async () => {
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    renderApp("/scan-test", withScanDialog());

    await user.type(await screen.findByLabelText("Paste a link"), "https://example.com/nope");
    await user.click(screen.getByRole("button", { name: "Open" }));

    expect(await screen.findByText("That isn't a Scan to Connect code.")).toBeInTheDocument();
  });

  it("decodes an uploaded photo", async () => {
    vi.mocked(jsQR).mockReturnValue(decodedAs(CONNECT_LINK()));
    const api = fakeApi({ "GET /me": [200, ADA], [RESOLVE]: [200, PREVIEW] });
    const user = userEvent.setup();
    const { router } = renderApp("/scan-test", withScanDialog());

    const file = new File(["fake-bytes"], "code.png", { type: "image/png" });
    await user.upload(await screen.findByLabelText("Upload a photo of a code"), file);

    await waitFor(() => expect(router.state.location.pathname).toBe("/connect"));
    expect(api.callsTo(RESOLVE).length).toBeGreaterThan(0);
  });

  it("stops every camera track when the dialog is closed", async () => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    renderApp("/scan-test", withScanDialog());

    await user.click(await screen.findByRole("button", { name: "Start camera" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Start camera" })).not.toBeInTheDocument());

    await user.keyboard("{Escape}");

    await waitFor(() => expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true));
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeInTheDocument();
  });

  it("stops every camera track on unmount", async () => {
    const stream = new FakeMediaStream();
    stubGetUserMedia(() => stream);
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    const view = renderApp("/scan-test", withScanDialog());

    await user.click(await screen.findByRole("button", { name: "Start camera" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Start camera" })).not.toBeInTheDocument());

    view.unmount();

    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
  });

  it("stops the stream and never starts the scan loop when the dialog closes while the camera permission is pending", async () => {
    const stream = new FakeMediaStream();
    const { resolve } = stubPendingGetUserMedia();
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    renderApp("/scan-test", withScanDialog());

    await user.click(await screen.findByRole("button", { name: "Start camera" }));
    await screen.findByRole("button", { name: "Starting camera…" });
    await user.keyboard("{Escape}"); // closes while getUserMedia is still pending
    await act(async () => {
      resolve(stream);
      await Promise.resolve();
      await Promise.resolve();
    });
    // Gives the scan loop a few ticks' worth of real time to prove it never started: if it had,
    // it would have called the (mocked) decoder by now.
    await new Promise((r) => setTimeout(r, 50));

    expect(stream.getTracks().every((track) => track.readyState === "ended")).toBe(true);
    expect(vi.mocked(jsQR)).not.toHaveBeenCalled();
  });

  it("disables Start camera while a request is pending, so a double click opens only one stream", async () => {
    const { getUserMedia, resolve } = stubPendingGetUserMedia();
    fakeApi({ "GET /me": [200, ADA] });
    const user = userEvent.setup();
    renderApp("/scan-test", withScanDialog());

    const button = await screen.findByRole("button", { name: "Start camera" });
    await user.click(button);
    expect(await screen.findByRole("button", { name: "Starting camera…" })).toBeDisabled();
    // A second click lands on the now-disabled button: no second getUserMedia call.
    await user.click(screen.getByRole("button", { name: "Starting camera…" }));

    await act(async () => {
      resolve(new FakeMediaStream());
      await Promise.resolve();
    });

    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });
});
