import { describe, expect, it } from "vitest";

import { formatTimeRange, minutesToTime, timeToEndMinutes, timeToStartMinutes } from "@/features/profiles/time";

describe("timeToStartMinutes", () => {
  it("converts HH:MM to minutes since midnight", () => {
    expect(timeToStartMinutes("00:00")).toBe(0);
    expect(timeToStartMinutes("09:30")).toBe(570);
    expect(timeToStartMinutes("23:59")).toBe(1439);
  });
});

describe("timeToEndMinutes", () => {
  it("converts HH:MM to minutes since midnight", () => {
    expect(timeToEndMinutes("09:30")).toBe(570);
    expect(timeToEndMinutes("23:59")).toBe(1439);
  });

  it("treats 00:00 as midnight (1440), not the start of day", () => {
    expect(timeToEndMinutes("00:00")).toBe(1440);
  });
});

describe("minutesToTime", () => {
  it("converts minutes since midnight to HH:MM", () => {
    expect(minutesToTime(0)).toBe("00:00");
    expect(minutesToTime(570)).toBe("09:30");
    expect(minutesToTime(1439)).toBe("23:59");
  });

  it("displays midnight (1440) as 00:00", () => {
    expect(minutesToTime(1440)).toBe("00:00");
  });
});

describe("formatTimeRange", () => {
  it("formats a range readably, with midnight shown as 12am the next moment", () => {
    expect(formatTimeRange(570, 1020)).toBe("9:30am–5:00pm");
    expect(formatTimeRange(0, 1440)).toBe("12:00am–12:00am");
  });
});
