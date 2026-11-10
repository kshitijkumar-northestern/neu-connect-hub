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
