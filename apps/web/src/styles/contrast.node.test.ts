// @vitest-environment node
// WCAG contrast of the design tokens actually shipped in globals.css, in both themes.
// Runs in the node environment (and type-checks with tsconfig.node.json): Vitest empties CSS
// imports, even ?raw, so the file is read from disk.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("./globals.css", import.meta.url)), "utf8");

/** Token values per theme. Later declarations win, exactly as in the cascade. */
function tokens(selector: ":root" | ".dark"): Record<string, string> {
  const result: Record<string, string> = {};
  const blocks = css.matchAll(new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`, "g"));
  for (const [, body] of blocks) {
    for (const [, name, value] of (body ?? "").matchAll(/--([\w-]+):\s*([^;]+);/g)) {
      if (name && value) result[name] = value.trim();
    }
  }
  return result;
}

/** Relative luminance of an opaque oklch() colour (via OKLab -> linear sRGB). */
function luminance(oklch: string): number {
  const match = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\)$/.exec(oklch);
  if (!match) throw new Error(`Not an opaque oklch() colour: ${oklch}`);
  const [L, C, H] = [Number(match[1]), Number(match[2]), (Number(match[3]) * Math.PI) / 180];
  const a = C * Math.cos(H);
  const b = C * Math.sin(H);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  const r = clamp(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
  const g = clamp(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
  const bl = clamp(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s);
  return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
}

function contrast(foreground: string, background: string): number {
  const [hi, lo] = [luminance(foreground), luminance(background)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

const light = { ...tokens(":root") };
const dark = { ...light, ...tokens(".dark") };

// [foreground token, background token, minimum ratio, what it is]
const PAIRS: Array<[string, string, number, string]> = [
  ["foreground", "background", 4.5, "body text"],
  ["muted-foreground", "background", 4.5, "secondary text"],
  ["card-foreground", "card", 4.5, "text on cards"],
  ["primary-foreground", "primary", 4.5, "primary button label"],
  ["destructive", "background", 4.5, "error text"],
  ["ring", "background", 3, "focus indicator (WCAG 1.4.11)"],
];

describe.each([
  ["light", light],
  ["dark", dark],
])("%s theme", (_name, theme) => {
  it.each(PAIRS)("%s on %s meets %s:1 (%s)", (fg, bg, minimum) => {
    const [fgValue, bgValue] = [theme[fg], theme[bg]];
    expect(fgValue, `--${fg} is defined`).toBeDefined();
    expect(bgValue, `--${bg} is defined`).toBeDefined();

    expect(contrast(fgValue as string, bgValue as string)).toBeGreaterThanOrEqual(minimum);
  });
});

describe("contrast helper", () => {
  it("matches known WCAG values", () => {
    expect(contrast("oklch(0 0 0)", "oklch(1 0 0)")).toBeCloseTo(21, 1);
    expect(contrast("oklch(1 0 0)", "oklch(1 0 0)")).toBeCloseTo(1, 5);
  });
});
