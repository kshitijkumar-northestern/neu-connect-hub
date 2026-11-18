import { describe, expect, it } from "vitest";

import type { MatchReason } from "@/features/discovery/api";
import { describeReason } from "@/features/discovery/reasons";

describe("describeReason", () => {
  it("describes covered skills by name", () => {
    const reason: MatchReason = {
      kind: "skills_covered",
      items: [
        { slug: "ui-design", name: "UI design" },
        { slug: "figma", name: "Figma" },
      ],
    };
    expect(describeReason(reason)).toBe("Can help with UI design, Figma");
  });

  it("cuts skill names off at three, with a count of the rest", () => {
    const reason: MatchReason = {
      kind: "skills_covered",
      items: [
        { slug: "a", name: "A" },
        { slug: "b", name: "B" },
        { slug: "c", name: "C" },
        { slug: "d", name: "D" },
        { slug: "e", name: "E" },
      ],
    };
    expect(describeReason(reason)).toBe("Can help with A, B, C +2 more");
  });

  it("describes shared interests by name", () => {
    const reason: MatchReason = {
      kind: "shared_interests",
      items: [{ slug: "music-and-arts", name: "Music and arts" }],
    };
    expect(describeReason(reason)).toBe("Also into Music and arts");
  });

  it("describes an availability overlap in the viewer's time, with a pluralised weekday", () => {
    const reason: MatchReason = {
      kind: "availability_overlap",
      window: { weekday: 1, start_min: 1080, end_min: 1200 },
    };
    expect(describeReason(reason)).toBe("You're both free Tuesdays 6:00pm–8:00pm (your time)");
  });

  it("shows an end at midnight as 12:00am", () => {
    const reason: MatchReason = {
      kind: "availability_overlap",
      window: { weekday: 6, start_min: 1320, end_min: 1440 },
    };
    expect(describeReason(reason)).toBe("You're both free Sundays 10:00pm–12:00am (your time)");
  });

  it("describes shared goals via the action-oriented labels", () => {
    const reason: MatchReason = { kind: "shared_goals", goals: ["research"] };
    expect(describeReason(reason)).toBe("Shares your goal: Doing research");
  });

  it("joins more than one shared goal", () => {
    const reason: MatchReason = { kind: "shared_goals", goals: ["studying", "mentorship"] };
    expect(describeReason(reason)).toBe("Shares your goals: Studying together, Mentorship");
  });

  it("never renders anything that looks like a score or a percentage", () => {
    const reasons: MatchReason[] = [
      { kind: "skills_covered", items: [{ slug: "a", name: "A" }] },
      { kind: "shared_interests", items: [{ slug: "b", name: "B" }] },
      { kind: "availability_overlap", window: { weekday: 0, start_min: 0, end_min: 60 } },
      { kind: "shared_goals", goals: ["research"] },
    ];
    for (const reason of reasons) {
      expect(describeReason(reason)).not.toMatch(/%/);
      expect(describeReason(reason)).not.toMatch(/\b0\.\d/);
    }
  });

  it("says all day for a window that covers the whole day, instead of 12:00am–12:00am", () => {
    const reason: MatchReason = { kind: "availability_overlap", window: { weekday: 0, start_min: 0, end_min: 1440 } };
    expect(describeReason(reason)).toBe("You're both free all day Mondays (your time)");
  });

  it("leaves the weekday out rather than guessing when it's out of range", () => {
    const reason: MatchReason = { kind: "availability_overlap", window: { weekday: 9, start_min: 600, end_min: 720 } };
    expect(describeReason(reason)).toBe("You're both free 10:00am–12:00pm (your time)");
  });
});
