import { describe, expect, it } from "vitest";

import { relativeTime } from "@/features/messages/time";

const NOW = new Date("2026-11-10T12:00:00Z");

describe("relativeTime", () => {
  it("describes a moment in the last minute as just now", () => {
    expect(relativeTime("2026-11-10T11:59:40Z", NOW)).toBe("Just now");
  });

  it("describes minutes and hours ago", () => {
    expect(relativeTime("2026-11-10T11:55:00Z", NOW)).toBe("5 minutes ago");
    expect(relativeTime("2026-11-10T09:00:00Z", NOW)).toBe("3 hours ago");
  });

  it("describes yesterday and older dates", () => {
    expect(relativeTime("2026-11-09T11:00:00Z", NOW)).toBe("Yesterday");
    expect(relativeTime("2026-10-01T12:00:00Z", NOW)).toBe("Oct 1, 2026");
  });
});
