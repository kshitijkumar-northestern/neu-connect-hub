import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import type { Match } from "@/features/discovery/api";
import { MatchCard } from "@/features/discovery/components/MatchCard";
import { apiError, fakeApi, type Reply } from "@/test/fake-api";

const ADA_MATCH: Match = {
  user_id: "11111111-1111-4111-8111-111111111112",
  display_name: "Ada Lovelace",
  headline: "Building things",
  reasons: [
    { kind: "skills_covered", items: [{ slug: "ui-design", name: "UI design" }, { slug: "figma", name: "Figma" }] },
    { kind: "shared_goals", goals: ["research"] },
  ],
};

function renderCard(onNotNow = vi.fn()) {
  return render(
    <MemoryRouter>
      <MatchCard match={ADA_MATCH} onNotNow={onNotNow} />
    </MemoryRouter>,
  );
}

describe("MatchCard", () => {
  it("links the name to the public profile and shows the headline and reasons", () => {
    renderCard();

    expect(screen.getByRole("link", { name: "Ada Lovelace" })).toHaveAttribute(
      "href",
      `/people/${ADA_MATCH.user_id}`,
    );
    expect(screen.getByText("Building things")).toBeInTheDocument();
    expect(screen.getByText("Can help with UI design, Figma")).toBeInTheDocument();
    expect(screen.getByText("Shares your goal: Doing research")).toBeInTheDocument();
  });

  it("gives each action an accessible name that includes the student's name", () => {
    renderCard();

    expect(screen.getByRole("button", { name: "Connect with Ada Lovelace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Not now: Ada Lovelace" })).toBeInTheDocument();
  });

  it("disables Connect with aria-busy while pending, then shows Request sent with a live region", async () => {
    const user = userEvent.setup();
    let resolveConnect: (reply: Reply) => void = () => undefined;
    const pending = new Promise<Reply>((resolve) => {
      resolveConnect = resolve;
    });
    const api = fakeApi({
      "POST /connections": () => pending,
      "POST /discovery/feedback": [204],
    });
    renderCard();

    // The live region is there before anything is announced in it, and never display:none
    // (which would take it out of the accessibility tree): visually hidden while empty instead.
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
    expect(screen.getByRole("status")).toHaveClass("sr-only");
    expect(screen.getByRole("status").className).not.toMatch(/(^|\s)(hidden|empty:hidden)(\s|$)/);
    const connect = screen.getByRole("button", { name: "Connect with Ada Lovelace" });
    await user.click(connect);

    expect(connect).toHaveAttribute("aria-busy", "true");
    expect(connect).toBeDisabled();
    // A dismissal now would contradict the request already on its way.
    expect(screen.getByRole("button", { name: "Not now: Ada Lovelace" })).toBeDisabled();

    resolveConnect([200, { id: "c1", state: "pending", direction: "outgoing", user_id: ADA_MATCH.user_id }]);

    const sent = await screen.findByRole("button", { name: "Request sent to Ada Lovelace" });
    expect(sent).toHaveTextContent("Request sent");
    expect(sent).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Connection request sent to Ada Lovelace");
    expect(screen.queryByRole("button", { name: "Not now: Ada Lovelace" })).not.toBeInTheDocument();
    expect(api.callsTo("POST /connections")).toHaveLength(1);
    expect(await vi.waitFor(() => api.callsTo("POST /discovery/feedback"))).toHaveLength(1);
    expect(api.callsTo("POST /discovery/feedback")[0]?.body).toEqual({
      candidate_id: ADA_MATCH.user_id,
      action: "connected",
    });
  });

  it("shows a friendly message for a 409 and keeps the Connect button usable", async () => {
    const user = userEvent.setup();
    fakeApi({ "POST /connections": () => apiError(409, "connection_exists") });
    renderCard();

    await user.click(screen.getByRole("button", { name: "Connect with Ada Lovelace" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already connected or requested");
  });

  it("calls onNotNow and fires dismissed feedback when Not now is clicked", async () => {
    const user = userEvent.setup();
    const onNotNow = vi.fn();
    const api = fakeApi({ "POST /discovery/feedback": [204] });
    renderCard(onNotNow);

    await user.click(screen.getByRole("button", { name: "Not now: Ada Lovelace" }));

    expect(onNotNow).toHaveBeenCalledWith(ADA_MATCH.user_id);
    expect(await vi.waitFor(() => api.callsTo("POST /discovery/feedback"))).toHaveLength(1);
    expect(api.callsTo("POST /discovery/feedback")[0]?.body).toEqual({
      candidate_id: ADA_MATCH.user_id,
      action: "dismissed",
    });
  });

  it("never renders a percentage or score", () => {
    renderCard();

    expect(document.body.textContent).not.toMatch(/%/);
  });
});
