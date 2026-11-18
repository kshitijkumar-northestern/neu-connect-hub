import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConnectButton } from "@/features/connections/components/ConnectButton";
import { apiError, fakeApi, type Reply } from "@/test/fake-api";

const USER_ID = "22222222-2222-4222-8222-222222222221";
const SENT: Reply = [200, { id: "c1", state: "pending", direction: "outgoing", user_id: USER_ID }];

function deferred() {
  let resolve: (reply: Reply) => void = () => undefined;
  const promise = new Promise<Reply>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe("ConnectButton", () => {
  it("names the student in its accessible name, and starts with an empty, visually hidden live region", () => {
    render(<ConnectButton userId={USER_ID} displayName="Grace Hopper" />);

    expect(screen.getByRole("button", { name: "Connect with Grace Hopper" })).toHaveTextContent("Connect");
    // Mounted before anything is announced in it, and never display:none, which would take it
    // out of the accessibility tree.
    const live = screen.getByRole("status");
    expect(live).toBeEmptyDOMElement();
    expect(live).toHaveClass("sr-only");
    expect(live.className).not.toMatch(/(^|\s)(hidden|empty:hidden)(\s|$)/);
  });

  it("is busy and disabled while the request is on its way, then shows Request sent and announces it", async () => {
    const user = userEvent.setup();
    const reply = deferred();
    const api = fakeApi({ "POST /connections": () => reply.promise });
    const onSent = vi.fn();
    const onPendingChange = vi.fn();
    render(
      <ConnectButton userId={USER_ID} displayName="Grace Hopper" onSent={onSent} onPendingChange={onPendingChange} />,
    );

    const connect = screen.getByRole("button", { name: "Connect with Grace Hopper" });
    await user.click(connect);

    expect(connect).toHaveAttribute("aria-busy", "true");
    expect(connect).toBeDisabled();
    expect(onPendingChange).toHaveBeenLastCalledWith(true);
    expect(onSent).not.toHaveBeenCalled();

    reply.resolve(SENT);

    const sent = await screen.findByRole("button", { name: "Request sent to Grace Hopper" });
    expect(sent).toHaveTextContent("Request sent");
    expect(sent).toBeDisabled();
    expect(sent).toHaveAttribute("aria-busy", "false");
    expect(screen.getByRole("status")).toHaveTextContent("Connection request sent to Grace Hopper");
    expect(screen.getByRole("status")).not.toHaveClass("sr-only");
    expect(onSent).toHaveBeenCalledTimes(1);
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
    expect(api.callsTo("POST /connections")).toHaveLength(1);
    expect(api.callsTo("POST /connections")[0]?.body).toEqual({ user_id: USER_ID });
  });

  it("shows Already connected or requested for a 409, and stays usable", async () => {
    const user = userEvent.setup();
    fakeApi({ "POST /connections": apiError(409, "connection_exists") });
    const onSent = vi.fn();
    const onPendingChange = vi.fn();
    render(
      <ConnectButton userId={USER_ID} displayName="Grace Hopper" onSent={onSent} onPendingChange={onPendingChange} />,
    );

    await user.click(screen.getByRole("button", { name: "Connect with Grace Hopper" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already connected or requested");
    expect(screen.getByRole("button", { name: "Connect with Grace Hopper" })).toBeEnabled();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(onSent).not.toHaveBeenCalled();
    expect(onPendingChange).toHaveBeenLastCalledWith(false);
  });

  it("shows any other failure in the student's words and clears it on the next try", async () => {
    const user = userEvent.setup();
    let failing = true;
    fakeApi({ "POST /connections": () => (failing ? apiError(429, "rate_limited") : SENT) });
    render(<ConnectButton userId={USER_ID} displayName="Grace Hopper" />);

    await user.click(screen.getByRole("button", { name: "Connect with Grace Hopper" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a few minutes and try again.");

    failing = false;
    await user.click(screen.getByRole("button", { name: "Connect with Grace Hopper" }));

    expect(await screen.findByRole("button", { name: "Request sent to Grace Hopper" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("puts other actions beside the button, between the error and the live region", async () => {
    const user = userEvent.setup();
    fakeApi({ "POST /connections": apiError(409, "connection_exists") });
    render(
      <ConnectButton userId={USER_ID} displayName="Grace Hopper">
        <button type="button">Not now</button>
      </ConnectButton>,
    );

    await user.click(screen.getByRole("button", { name: "Connect with Grace Hopper" }));

    const alert = await screen.findByRole("alert");
    const connect = screen.getByRole("button", { name: "Connect with Grace Hopper" });
    const other = screen.getByRole("button", { name: "Not now" });
    const live = screen.getByRole("status");
    expect(other.parentElement).toBe(connect.parentElement);
    expect(alert.compareDocumentPosition(connect) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(other.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("never renders a score or a percentage", () => {
    render(<ConnectButton userId={USER_ID} displayName="Grace Hopper" />);

    expect(document.body.textContent).not.toMatch(/%/);
  });
});
