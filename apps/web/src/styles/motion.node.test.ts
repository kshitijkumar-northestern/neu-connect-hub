// @vitest-environment node
// The reduced-motion rule in globals.css: with prefers-reduced-motion, every animation and
// transition (tw-animate-css's enter/exit keyframes on dialogs, menus and selects, and the
// components' transition-* utilities) is cut to next to nothing. Read from disk for the same
// reason as contrast.node.test.ts; the real-browser pass checks the computed durations.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("./globals.css", import.meta.url)), "utf8");

/** The body of the top-level `@media (prefers-reduced-motion: reduce)` block, braces balanced. */
function reducedMotionBlock(): string | undefined {
  const start = css.search(/@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{/);
  if (start < 0) return undefined;
  const open = css.indexOf("{", start);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === "{") depth++;
    if (css[i] === "}" && --depth === 0) return css.slice(open + 1, i);
  }
  return undefined;
}

/** A CSS time in seconds. */
function seconds(value: string): number {
  const match = /^([\d.]+)(ms|s)$/.exec(value.trim());
  if (!match) throw new Error(`Not a CSS time: ${value}`);
  return Number(match[1]) / (match[2] === "ms" ? 1000 : 1);
}

describe("reduced motion", () => {
  const block = reducedMotionBlock();

  it("has a prefers-reduced-motion: reduce block", () => {
    expect(block).toBeDefined();
  });

  it("applies to every element and pseudo-element", () => {
    expect(block).toMatch(/\*\s*,\s*\*::before\s*,\s*\*::after\s*\{/);
  });

  it.each(["animation-duration", "transition-duration"])("cuts %s to at most 0.01ms, overriding the utilities", (property) => {
    const match = new RegExp(`${property}:\\s*([^;!]+)\\s*!important;`).exec(block ?? "");
    expect(match, `${property} is set with !important`).not.toBeNull();
    expect(seconds(match?.[1] ?? "")).toBeLessThanOrEqual(0.00001);
  });

  it("plays an animation once at most, and scrolls without smoothing", () => {
    expect(block).toMatch(/animation-iteration-count:\s*1\s*!important;/);
    expect(block).toMatch(/scroll-behavior:\s*auto\s*!important;/);
  });
});
