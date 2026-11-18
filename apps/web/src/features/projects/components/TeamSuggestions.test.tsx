import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { TeamSuggestion, TeamSuggestions as TeamSuggestionsData } from "@/features/projects/api";
import { TeamSuggestions } from "@/features/projects/components/TeamSuggestions";
import { apiError, fakeApi, type Reply } from "@/test/fake-api";

const SUGGESTIONS = "GET /projects/project-1/suggestions";

const GRACE_ID = "22222222-2222-4222-8222-222222222221";
const KATHERINE_ID = "22222222-2222-4222-8222-222222222222";

const GRACE: TeamSuggestion = {
  user_id: GRACE_ID,
  display_name: "Grace Hopper",
  headline: "Compiler enthusiast",
  role: { id: "role-1", title: "Backend developer" },
  covers: [{ slug: "python", name: "Python" }],
};

const KATHERINE: TeamSuggestion = {
  user_id: KATHERINE_ID,
  display_name: "Katherine Johnson",
  headline: "Orbital mechanics",
  role: { id: "role-2", title: "Designer" },
  covers: [
    { slug: "data-analysis", name: "Data analysis" },
    { slug: "figma", name: "Figma" },
    { slug: "react", name: "React" },
    { slug: "typescript", name: "TypeScript" },
    { slug: "ui-design", name: "UI design" },
  ],
};

function result(suggestions: TeamSuggestion[], uncovered: TeamSuggestionsData["uncovered"] = []): Reply {
  return [200, { suggestions, uncovered }];
}

function renderSection() {
  return render(
    <MemoryRouter>
      <TeamSuggestions projectId="project-1" />
    </MemoryRouter>,
  );
}

function deferred() {
  let resolve: (reply: Reply) => void = () => undefined;
  const promise = new Promise<Reply>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe("TeamSuggestions", () => {
  it("shows a loading state under its heading, then the suggestions in order", async () => {
    const reply = deferred();
    const api = fakeApi({ [SUGGESTIONS]: () => reply.promise });
    renderSection();

    expect(screen.getByRole("heading", { level: 2, name: "Suggested teammates" })).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("Finding teammates…");

    reply.resolve(result([GRACE, KATHERINE]));

    const list = await screen.findByRole("list", { name: "Suggested teammates" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(within(items[0]!).getByRole("link", { name: "Grace Hopper" })).toHaveAttribute(
      "href",
      `/people/${GRACE_ID}`,
    );
    expect(within(items[0]!).getByText("Compiler enthusiast")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Could fill: Backend developer")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Brings Python")).toBeInTheDocument();
    expect(within(items[1]!).getByRole("link", { name: "Katherine Johnson" })).toHaveAttribute(
      "href",
      `/people/${KATHERINE_ID}`,
    );
    expect(within(items[1]!).getByText("Could fill: Designer")).toBeInTheDocument();
    expect(screen.queryByText("Finding teammates…")).not.toBeInTheDocument();
    expect(api.callsTo(SUGGESTIONS)).toHaveLength(1);
  });

  it("names at most three skills, then folds the rest into +N more", async () => {
    fakeApi({ [SUGGESTIONS]: result([KATHERINE]) });
    renderSection();

    expect(await screen.findByText("Brings Data analysis, Figma, React +2 more")).toBeInTheDocument();
  });

  it("names exactly three skills without a +N more", async () => {
    fakeApi({ [SUGGESTIONS]: result([{ ...KATHERINE, covers: KATHERINE.covers.slice(0, 3) }]) });
    renderSection();

    expect(await screen.findByText("Brings Data analysis, Figma, React")).toBeInTheDocument();
    expect(screen.queryByText(/more/)).not.toBeInTheDocument();
  });

  it("lists the needed skills nobody suggested brings", async () => {
    fakeApi({
      [SUGGESTIONS]: result(
        [GRACE],
        [
          { slug: "go", name: "Go" },
          { slug: "rust", name: "Rust" },
        ],
      ),
    });
    renderSection();

    expect(await screen.findByText("Still uncovered: Go, Rust")).toBeInTheDocument();
  });

  it("leaves the uncovered line out when every needed skill is covered", async () => {
    fakeApi({ [SUGGESTIONS]: result([GRACE]) });
    renderSection();

    await screen.findByRole("list", { name: "Suggested teammates" });
    expect(screen.queryByText(/Still uncovered/)).not.toBeInTheDocument();
  });

  it("explains the opt-in requirement and links to the profile page", async () => {
    fakeApi({ [SUGGESTIONS]: apiError(403, "discovery_opt_in_required") });
    renderSection();

    expect(await screen.findByText("Turn on discovery in your profile to see suggestions.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to your profile" })).toHaveAttribute("href", "/profile");
    expect(screen.queryByRole("list", { name: "Suggested teammates" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("tells a non-student account that suggestions are for students, with no retry", async () => {
    fakeApi({ [SUGGESTIONS]: apiError(403, "discovery_students_only") });
    renderSection();

    expect(await screen.findByText("Suggestions are for student accounts.")).toBeInTheDocument();
    expect(screen.queryByText(/Turn on discovery/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("says so plainly when nobody matches, without an uncovered line", async () => {
    fakeApi({ [SUGGESTIONS]: result([], [{ slug: "python", name: "Python" }]) });
    renderSection();

    expect(
      await screen.findByText("No one matches the skills your team still needs right now."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Suggested teammates" })).not.toBeInTheDocument();
    expect(screen.queryByText(/Still uncovered/)).not.toBeInTheDocument();
  });

  it("shows an error with a retry that recovers", async () => {
    const user = userEvent.setup();
    let failing = true;
    const api = fakeApi({
      [SUGGESTIONS]: () => (failing ? apiError(500, "server_error") : result([GRACE])),
    });
    renderSection();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Can't load suggestions right now. Check your connection and try again.",
    );
    failing = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("link", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(api.callsTo(SUGGESTIONS)).toHaveLength(2);
  });

  it("says to wait on a 429, with no retry that would only hit the limit again", async () => {
    fakeApi({ [SUGGESTIONS]: apiError(429, "rate_limited") });
    renderSection();

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a few minutes and try again.");
    expect(screen.queryByText(/Check your connection/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("asks an owner with no weekly availability to add some, linking to the profile, with no retry", async () => {
    fakeApi({ [SUGGESTIONS]: apiError(409, "availability_required") });
    renderSection();

    expect(await screen.findByText("Add your weekly availability to see suggested teammates.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add your availability" })).toHaveAttribute("href", "/profile");
    expect(screen.queryByRole("list", { name: "Suggested teammates" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hides itself when the project is no longer open to suggestions", async () => {
    // Closed or filled since the page loaded: there's nothing to suggest, and retrying can't help.
    const api = fakeApi({ [SUGGESTIONS]: apiError(404, "not_found") });
    const { container } = renderSection();

    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(api.callsTo(SUGGESTIONS)).toHaveLength(1);
  });

  it("disables Connect with aria-busy while pending, then shows Request sent with a live region", async () => {
    const user = userEvent.setup();
    const connection = deferred();
    const api = fakeApi({ [SUGGESTIONS]: result([GRACE]), "POST /connections": () => connection.promise });
    renderSection();

    const item = within(await screen.findByRole("list", { name: "Suggested teammates" })).getByRole("listitem");
    // Mounted before anything is announced in it, and visually hidden rather than display:none,
    // which would take it out of the accessibility tree.
    const live = within(item).getByRole("status");
    expect(live).toBeEmptyDOMElement();
    expect(live).toHaveClass("sr-only");
    expect(live.className).not.toMatch(/(^|\s)(hidden|empty:hidden)(\s|$)/);

    const connect = within(item).getByRole("button", { name: "Connect with Grace Hopper" });
    await user.click(connect);

    expect(connect).toHaveAttribute("aria-busy", "true");
    expect(connect).toBeDisabled();

    connection.resolve([200, { id: "c1", state: "pending", direction: "outgoing", user_id: GRACE_ID }]);

    const sent = await within(item).findByRole("button", { name: "Request sent to Grace Hopper" });
    expect(sent).toHaveTextContent("Request sent");
    expect(sent).toBeDisabled();
    expect(sent).toHaveAttribute("aria-busy", "false");
    expect(live).toHaveTextContent("Connection request sent to Grace Hopper");
    expect(live).not.toHaveClass("sr-only");
    expect(api.callsTo("POST /connections")).toHaveLength(1);
    expect(api.callsTo("POST /connections")[0]?.body).toEqual({ user_id: GRACE_ID });
    // A teammate suggestion isn't a Discover match: no ranking feedback is sent for it.
    expect(api.callsTo("POST /discovery/feedback")).toHaveLength(0);
  });

  it("shows a friendly message for a 409 and keeps Connect usable", async () => {
    const user = userEvent.setup();
    fakeApi({ [SUGGESTIONS]: result([GRACE]), "POST /connections": apiError(409, "connection_exists") });
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Connect with Grace Hopper" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Already connected or requested");
    expect(screen.getByRole("button", { name: "Connect with Grace Hopper" })).toBeEnabled();
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("shows any other Connect failure and clears it on the next try", async () => {
    const user = userEvent.setup();
    let failing = true;
    fakeApi({
      [SUGGESTIONS]: result([GRACE]),
      "POST /connections": () =>
        failing
          ? apiError(429, "rate_limited")
          : [200, { id: "c1", state: "pending", direction: "outgoing", user_id: GRACE_ID }],
    });
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Connect with Grace Hopper" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts. Wait a few minutes and try again.");

    failing = false;
    await user.click(screen.getByRole("button", { name: "Connect with Grace Hopper" }));

    expect(await screen.findByRole("button", { name: "Request sent to Grace Hopper" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps each student's Connect state to their own item", async () => {
    const user = userEvent.setup();
    fakeApi({
      [SUGGESTIONS]: result([GRACE, KATHERINE]),
      "POST /connections": [200, { id: "c1", state: "pending", direction: "outgoing", user_id: GRACE_ID }],
    });
    renderSection();

    await user.click(await screen.findByRole("button", { name: "Connect with Grace Hopper" }));

    expect(await screen.findByRole("button", { name: "Request sent to Grace Hopper" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Connect with Katherine Johnson" })).toBeEnabled();
  });

  it("never renders a score or a percentage", async () => {
    fakeApi({ [SUGGESTIONS]: result([GRACE, KATHERINE], [{ slug: "go", name: "Go" }]) });
    renderSection();

    await screen.findByRole("list", { name: "Suggested teammates" });
    expect(document.body.textContent).not.toMatch(/%/);
    expect(document.body.textContent).not.toMatch(/\b0\.\d/);
    expect(document.body.textContent).not.toMatch(/score/i);
  });
});
