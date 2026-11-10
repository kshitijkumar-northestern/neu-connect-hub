import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { RecordedCall, Reply, Route } from "@/test/fake-api";
import { ADA, apiError, fakeApi } from "@/test/fake-api";
import { renderApp } from "@/test/render-app";

const SKILLS = [
  { slug: "figma", name: "Figma", category: "Design" },
  { slug: "python", name: "Python", category: "Programming" },
  { slug: "react", name: "React", category: "Programming" },
];
const INTERESTS = [
  { slug: "ai", name: "AI" },
  { slug: "music", name: "Music" },
];

const FULL_PROFILE = {
  user_id: ADA.id,
  display_name: "Ada Lovelace",
  headline: "Full-stack builder",
  bio: "I like building things.\nAsk me about compilers.",
  goals: ["studying", "mentorship"],
  timezone: "America/New_York",
  skills: [{ slug: "python", name: "Python", level: "advanced" }],
  seeking_skills: [{ slug: "figma", name: "Figma" }],
  interests: [{ slug: "music", name: "Music" }],
  availability: [{ weekday: 0, start_min: 540, end_min: 1020 }],
  discoverable: true,
  feedback_consent: true,
  updated_at: "2026-10-01T00:00:00Z",
};

/** Echoes the PUT body back as an OwnProfile, close enough for the page to re-render from. */
function echoProfile(call: RecordedCall): Reply {
  const body = call.body as {
    display_name: string;
    headline: string;
    bio: string;
    goals: string[];
    timezone: string;
    skills: { slug: string; level: string }[];
    seeking_skills: string[];
    interests: string[];
    availability: unknown[];
    discoverable: boolean;
    feedback_consent: boolean;
  };
  return [
    200,
    {
      user_id: ADA.id,
      display_name: body.display_name,
      headline: body.headline,
      bio: body.bio,
      goals: body.goals,
      timezone: body.timezone,
      skills: body.skills.map((skill) => ({ slug: skill.slug, name: skill.slug, level: skill.level })),
      seeking_skills: body.seeking_skills.map((slug) => ({ slug, name: slug })),
      interests: body.interests.map((slug) => ({ slug, name: slug })),
      availability: body.availability,
      discoverable: body.discoverable,
      feedback_consent: body.feedback_consent,
      updated_at: "2026-11-09T00:00:00Z",
    },
  ];
}

function routes(overrides: Record<string, Route> = {}): Record<string, Route> {
  return {
    "GET /me": [200, ADA],
    "GET /skills": [200, SKILLS],
    "GET /interests": [200, INTERESTS],
    "PUT /me/profile": (call: RecordedCall) => echoProfile(call),
    ...overrides,
  };
}

describe("ProfilePage: a brand-new profile", () => {
  it("shows an empty form with an introduction and sends the exact PUT body on save", async () => {
    const user = userEvent.setup();
    const api = fakeApi(routes({ "GET /me/profile": apiError(404, "profile_not_found") }));
    renderApp("/profile");

    expect(await screen.findByText("Tell other students about yourself.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Profile saved");
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    expect(api.callsTo("PUT /me/profile")[0]?.body).toEqual({
      display_name: "Ada Lovelace",
      headline: "",
      bio: "",
      goals: [],
      timezone,
      skills: [],
      seeking_skills: [],
      interests: [],
      availability: [],
      discoverable: false,
      feedback_consent: false,
    });
  });

  it("requires a display name before sending anything", async () => {
    const user = userEvent.setup();
    const api = fakeApi(routes({ "GET /me/profile": apiError(404, "profile_not_found") }));
    renderApp("/profile");

    await screen.findByText("Tell other students about yourself.");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText("Enter your name.")).toBeInTheDocument();
    expect(screen.getByLabelText("Display name")).toHaveAttribute("aria-invalid", "true");
    expect(api.callsTo("PUT /me/profile")).toHaveLength(0);
  });
});

describe("ProfilePage: an existing profile", () => {
  it("prefills every field and resubmits the same values", async () => {
    const user = userEvent.setup();
    const api = fakeApi(routes({ "GET /me/profile": [200, FULL_PROFILE] }));
    renderApp("/profile");

    expect(await screen.findByLabelText("Display name")).toHaveValue("Ada Lovelace");
    expect(screen.getByLabelText("Headline")).toHaveValue("Full-stack builder");
    expect(screen.getByLabelText("Bio")).toHaveValue("I like building things.\nAsk me about compilers.");
    expect(screen.getByRole("checkbox", { name: "Studying together" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Mentorship" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Building a project" })).not.toBeChecked();

    const haveSkills = screen.getByRole("group", { name: "Skills you have" });
    expect(haveSkills).toHaveTextContent("Python · Advanced");
    const seekingSkills = screen.getByRole("group", { name: "Skills you're looking for" });
    expect(seekingSkills).toHaveTextContent("Figma");

    expect(screen.getByRole("button", { name: "Music" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "AI" })).toHaveAttribute("aria-pressed", "false");

    expect(screen.getByLabelText("Monday range start time")).toHaveValue("09:00");
    expect(screen.getByLabelText("Monday range end time")).toHaveValue("17:00");

    expect(screen.getByRole("switch", { name: "Show my profile to other students" })).toBeChecked();
    expect(screen.getByRole("switch", { name: "Let NU Connect Hub learn from my match feedback" })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Profile saved");
    expect(api.callsTo("PUT /me/profile")[0]?.body).toEqual({
      display_name: "Ada Lovelace",
      headline: "Full-stack builder",
      bio: "I like building things.\nAsk me about compilers.",
      goals: ["studying", "mentorship"],
      timezone: "America/New_York",
      skills: [{ slug: "python", level: "advanced" }],
      seeking_skills: ["figma"],
      interests: ["music"],
      availability: [{ weekday: 0, start_min: 540, end_min: 1020 }],
      discoverable: true,
      feedback_consent: true,
    });
  });

  it("offers a preview link to the public view of the same profile", async () => {
    fakeApi(routes({ "GET /me/profile": [200, FULL_PROFILE] }));
    renderApp("/profile");

    expect(await screen.findByLabelText("Display name")).toHaveValue("Ada Lovelace");
    expect(screen.getByRole("link", { name: /Preview how other students see your profile/ })).toHaveAttribute(
      "href",
      `/people/${ADA.id}`,
    );
  });

  it("adds a skill with a level from the picker", async () => {
    const user = userEvent.setup();
    fakeApi(routes({ "GET /me/profile": apiError(404, "profile_not_found") }));
    renderApp("/profile");

    await screen.findByText("Tell other students about yourself.");
    const haveSkills = screen.getByRole("group", { name: "Skills you have" });
    await user.click(within(haveSkills).getByLabelText("Skill"));
    await user.click(await screen.findByRole("option", { name: "React" }));
    await user.click(within(haveSkills).getByLabelText("Level"));
    await user.click(await screen.findByRole("option", { name: "Advanced" }));
    await user.click(within(haveSkills).getByRole("button", { name: "Add" }));

    expect(haveSkills).toHaveTextContent("React · Advanced");
  });

  it("puts a server field error on the matching field", async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      routes({
        "GET /me/profile": apiError(404, "profile_not_found"),
        "PUT /me/profile": [
          422,
          {
            error: {
              code: "validation_error",
              message: "The request is invalid.",
              request_id: "req-test",
              fields: [{ location: "body.headline", message: "Use at most 120 characters." }],
            },
          },
        ],
      }),
    );
    renderApp("/profile");

    await screen.findByText("Tell other students about yourself.");
    await user.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText("Use at most 120 characters.")).toBeInTheDocument();
    expect(screen.getByLabelText("Headline")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(api.callsTo("PUT /me/profile")).toHaveLength(1);
  });

  it("blocks submission on overlapping availability ranges before sending anything", async () => {
    const user = userEvent.setup();
    const api = fakeApi(routes({ "GET /me/profile": apiError(404, "profile_not_found") }));
    renderApp("/profile");

    await screen.findByText("Tell other students about yourself.");
    await user.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await user.click(screen.getByRole("button", { name: "Add a time for Monday" }));
    await user.click(screen.getByRole("button", { name: "Add a time for Monday" }));
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText("Time ranges on the same day can't overlap.")).toBeInTheDocument();
    expect(api.callsTo("PUT /me/profile")).toHaveLength(0);
  });

  it("shows a form error when the saved skill list is out of date", async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      routes({
        "GET /me/profile": apiError(404, "profile_not_found"),
        "PUT /me/profile": apiError(422, "unknown_skill"),
      }),
    );
    renderApp("/profile");

    await screen.findByText("Tell other students about yourself.");
    await user.type(screen.getByLabelText("Display name"), "Ada Lovelace");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/isn't on the list/);
    expect(api.callsTo("PUT /me/profile")).toHaveLength(1);
  });
});
