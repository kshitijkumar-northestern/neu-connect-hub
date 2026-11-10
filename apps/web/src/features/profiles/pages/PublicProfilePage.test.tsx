import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const OTHER_ID = "22222222-2222-4222-8222-222222222222";

const OTHER_PROFILE = {
  user_id: OTHER_ID,
  display_name: "Grace Hopper",
  headline: "Compiler enthusiast",
  bio: "Building things that build things.",
  goals: ["research"],
  timezone: "America/New_York",
  skills: [{ slug: "cobol", name: "COBOL", level: "advanced" }],
  seeking_skills: [{ slug: "rust", name: "Rust" }],
  interests: [{ slug: "music", name: "Music" }],
  availability: [{ weekday: 2, start_min: 540, end_min: 1020 }],
};

describe("PublicProfilePage", () => {
  it("renders another student's profile", async () => {
    fakeApi({ "GET /me": [200, ADA], [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE] });
    renderApp(`/people/${OTHER_ID}`);

    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.getByText("Compiler enthusiast")).toBeInTheDocument();
    expect(screen.getByText("Building things that build things.")).toBeInTheDocument();
    expect(screen.getByText("Doing research")).toBeInTheDocument();
    expect(screen.getByText("COBOL · Advanced")).toBeInTheDocument();
    expect(screen.getByText("Rust")).toBeInTheDocument();
    expect(screen.getByText("Music")).toBeInTheDocument();
    expect(screen.getByText("America/New_York")).toBeInTheDocument();
    expect(screen.getByText("Wednesday")).toBeInTheDocument();
    expect(screen.getByText("9:00am–5:00pm", { exact: false })).toBeInTheDocument();
  });

  it("shows only the neutral message for a missing, hidden or blocked profile", async () => {
    fakeApi({ "GET /me": [200, ADA], [`GET /profiles/${OTHER_ID}`]: apiError(404, "not_found") });
    renderApp(`/people/${OTHER_ID}`);

    expect(await screen.findByText("This profile isn't available.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Block" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Report" })).not.toBeInTheDocument();
  });

  it("shows no block or report actions on your own profile, with a link back to edit it", async () => {
    fakeApi({ "GET /me": [200, ADA], [`GET /profiles/${ADA.id}`]: [200, { ...OTHER_PROFILE, user_id: ADA.id }] });
    renderApp(`/people/${ADA.id}`);

    expect(await screen.findByText("This is how other students see your profile.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit your profile" })).toHaveAttribute("href", "/profile");
    expect(screen.queryByRole("button", { name: "Block" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Report" })).not.toBeInTheDocument();
  });

  it("blocks a student after a confirmation dialog", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "POST /blocks": [204],
    });
    renderApp(`/people/${OTHER_ID}`);

    await user.click(await screen.findByRole("button", { name: "Block" }));
    expect(await screen.findByRole("heading", { name: "Block this student?" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Block" }));

    expect(await screen.findByText(/now hidden from you/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Manage blocked students" })).toHaveAttribute("href", "/settings");
    expect(screen.queryByRole("button", { name: "Block" })).not.toBeInTheDocument();
    expect(api.callsTo("POST /blocks")[0]?.body).toEqual({ user_id: OTHER_ID });
  });

  it("sends a report, optionally blocking the student too", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "POST /reports": [202, { status: "report_received" }],
      "POST /blocks": [204],
    });
    renderApp(`/people/${OTHER_ID}`);

    await user.click(await screen.findByRole("button", { name: "Report" }));
    await user.click(screen.getByLabelText("Reason"));
    await user.click(await screen.findByRole("option", { name: "Harassment" }));
    await user.type(screen.getByLabelText("Details (optional)"), "Kept messaging me after I said stop.");
    await user.click(screen.getByLabelText("Also block this student"));
    await user.click(screen.getByRole("button", { name: "Send report" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Thanks. Our moderators will review this report.");
    expect(api.callsTo("POST /reports")[0]?.body).toEqual({
      user_id: OTHER_ID,
      reason: "harassment",
      details: "Kept messaging me after I said stop.",
    });
    expect(api.callsTo("POST /blocks")[0]?.body).toEqual({ user_id: OTHER_ID });

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(await screen.findByText(/now hidden from you/)).toBeInTheDocument();
  });
});

describe("PublicProfilePage: connections", () => {
  const CONNECTION_ID = "44444444-4444-4444-8444-444444444444";

  it("sends a connection request and can withdraw it", async () => {
    const user = userEvent.setup();
    const api = fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "GET /connections": [200, []],
      "POST /connections": [
        201,
        {
          id: CONNECTION_ID,
          state: "pending",
          direction: "outgoing",
          user_id: OTHER_ID,
          display_name: "Grace Hopper",
          created_at: "2026-11-05T00:00:00Z",
          responded_at: null,
          conversation_id: null,
        },
      ],
      [`POST /connections/${CONNECTION_ID}/withdraw`]: [204],
    });
    renderApp(`/people/${OTHER_ID}`);

    await user.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Request sent")).toBeInTheDocument();
    expect(api.callsTo("POST /connections")[0]?.body).toEqual({ user_id: OTHER_ID });

    await user.click(screen.getByRole("button", { name: "Withdraw" }));
    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
    expect(api.callsTo(`POST /connections/${CONNECTION_ID}/withdraw`)).toHaveLength(1);
  });

  it("accepts an incoming request and shows Connected", async () => {
    const user = userEvent.setup();
    const INCOMING = {
      id: CONNECTION_ID,
      state: "pending",
      direction: "incoming",
      user_id: OTHER_ID,
      display_name: "Grace Hopper",
      created_at: "2026-11-05T00:00:00Z",
      responded_at: null,
      conversation_id: null,
    };
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "GET /connections": [200, [INCOMING]],
      [`POST /connections/${CONNECTION_ID}/accept`]: [
        200,
        { ...INCOMING, state: "accepted", responded_at: "2026-11-05T01:00:00Z", conversation_id: "conv-1" },
      ],
    });
    renderApp(`/people/${OTHER_ID}`);

    expect(await screen.findByText("Grace Hopper wants to connect")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));

    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Message" })).toHaveAttribute("href", "/messages/conv-1");
  });

  it("declines an incoming request and goes back to Connect", async () => {
    const user = userEvent.setup();
    const INCOMING = {
      id: CONNECTION_ID,
      state: "pending",
      direction: "incoming",
      user_id: OTHER_ID,
      display_name: "Grace Hopper",
      created_at: "2026-11-05T00:00:00Z",
      responded_at: null,
      conversation_id: null,
    };
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "GET /connections": [200, [INCOMING]],
      [`POST /connections/${CONNECTION_ID}/decline`]: [204],
    });
    renderApp(`/people/${OTHER_ID}`);

    await user.click(await screen.findByRole("button", { name: "Decline" }));
    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
  });

  it("reloads and shows the existing state when a request conflicts", async () => {
    const user = userEvent.setup();
    let requested = false;
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "GET /connections": () =>
        requested
          ? [
              200,
              [
                {
                  id: CONNECTION_ID,
                  state: "accepted",
                  direction: "outgoing",
                  user_id: OTHER_ID,
                  display_name: "Grace Hopper",
                  created_at: "2026-11-01T00:00:00Z",
                  responded_at: "2026-11-02T00:00:00Z",
                  conversation_id: "conv-2",
                },
              ],
            ]
          : [200, []],
      "POST /connections": () => {
        requested = true;
        return apiError(409, "connection_exists");
      },
    });
    renderApp(`/people/${OTHER_ID}`);

    await user.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByText("Connected")).toBeInTheDocument();
  });

  it("shows an error when the request fails unexpectedly", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "GET /connections": [200, []],
      "POST /connections": apiError(500, "internal_error"),
    });
    renderApp(`/people/${OTHER_ID}`);

    await user.click(await screen.findByRole("button", { name: "Connect" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong. Please try again.");
  });

  it("hides the connection control once the student is blocked", async () => {
    const user = userEvent.setup();
    fakeApi({
      "GET /me": [200, ADA],
      [`GET /profiles/${OTHER_ID}`]: [200, OTHER_PROFILE],
      "GET /connections": [200, []],
      "POST /blocks": [204],
    });
    renderApp(`/people/${OTHER_ID}`);

    expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Block" }));
    await user.click(screen.getByRole("button", { name: "Block" }));

    expect(await screen.findByText(/now hidden from you/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Connect" })).not.toBeInTheDocument();
  });
});
