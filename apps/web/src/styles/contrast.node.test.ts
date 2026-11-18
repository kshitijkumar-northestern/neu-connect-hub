// @vitest-environment node
// WCAG contrast of the design tokens actually shipped in globals.css, in both themes.
// Runs in the node environment (and type-checks with tsconfig.node.json): Vitest empties CSS
// imports, even ?raw, so the file is read from disk.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("./globals.css", import.meta.url)), "utf8");

// Which top-level rules apply to <html> in each theme (ThemeProvider puts "dark" on it). Rules
// that apply have equal specificity where they overlap, so source order decides, as in the cascade.
const APPLIES: Record<string, ReadonlyArray<"light" | "dark">> = {
  ":root": ["light", "dark"],
  ".dark": ["dark"],
  ":root:not(.dark)": ["light"],
};

/** Token values for one theme: every applying rule's custom properties, later ones winning. */
function tokens(theme: "light" | "dark"): Record<string, string> {
  const result: Record<string, string> = {};
  for (const [, selector, body] of css.matchAll(/(?:^|\n)([^\n{}@]+?)\s*\{([^}]*)\}/g)) {
    if (!APPLIES[selector?.trim() ?? ""]?.includes(theme)) continue;
    for (const [, name, value] of (body ?? "").matchAll(/--([\w-]+):\s*([^;]+);/g)) {
      if (name && value) result[name] = value.trim();
    }
  }
  return result;
}

type Rgba = [r: number, g: number, b: number, alpha: number];

/** An oklch() colour, optionally translucent (`/ 10%`), as gamma-encoded sRGB plus alpha
 * (OKLab -> linear sRGB, clamped to the gamut -> sRGB). */
function rgba(oklch: string): Rgba {
  const match = /^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+)%\s*)?\)$/.exec(oklch);
  if (!match) throw new Error(`Not an oklch() colour: ${oklch}`);
  const [L, C, H] = [Number(match[1]), Number(match[2]), (Number(match[3]) * Math.PI) / 180];
  const alpha = match[4] === undefined ? 1 : Number(match[4]) / 100;
  const a = C * Math.cos(H);
  const b = C * Math.sin(H);
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const encode = (v: number) => {
    const c = Math.min(1, Math.max(0, v));
    return c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055;
  };
  return [
    encode(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    encode(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    encode(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
    alpha,
  ];
}

/** `top` with its opacity scaled by `opacity` (Tailwind's `/10`), composited over an opaque
 * `base` the way browsers blend: in gamma-encoded sRGB. */
function over(top: Rgba, opacity: number, base: Rgba): Rgba {
  const alpha = top[3] * opacity;
  const mix = (i: 0 | 1 | 2) => alpha * top[i] + (1 - alpha) * base[i];
  return [mix(0), mix(1), mix(2), 1];
}

/** Relative luminance of an opaque sRGB colour. */
function luminance([r, g, b, alpha]: Rgba): number {
  if (alpha !== 1) throw new Error("Composite a translucent colour over its surface first");
  const linear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
}

function ratio(foreground: Rgba, background: Rgba): number {
  const [hi, lo] = [luminance(foreground), luminance(background)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

function contrast(foreground: string, background: string): number {
  return ratio(rgba(foreground), rgba(background));
}

const light = tokens("light");
const dark = tokens("dark");

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

// Text on a translucent tint, as the components draw it: [text token, text opacity, surface
// token, surface opacity, page token, minimum, what]. Surface opacity 0 means text straight on the page.
// Chrome's own blending rounds a little differently (it reads the preset's text-destructive/90 as
// 4.49:1 where this computes 4.52:1), so tinted pairs must clear the minimum by this margin.
const BLEND_MARGIN = 0.05;
type Tinted = [string, number, string, number, string, number, string];
const TINTED: Record<"light" | "dark", Tinted[]> = {
  light: [
    ["destructive", 1, "destructive", 0.1, "background", 4.5, "destructive button and badge (bg-destructive/10)"],
    ["destructive", 0.9, "card", 0, "card", 4.5, "destructive alert text (text-destructive/90)"],
    ["muted-foreground", 1, "input", 0.5, "background", 4.5, "select placeholder and input hint (bg-input/50)"],
    ["muted-foreground", 1, "muted", 1, "background", 4.5, "secondary text on a muted surface"],
  ],
  dark: [
    ["destructive", 1, "destructive", 0.2, "background", 4.5, "destructive button and badge (dark:bg-destructive/20)"],
    ["destructive", 0.9, "card", 0, "card", 4.5, "destructive alert text (text-destructive/90)"],
    ["muted-foreground", 1, "input", 0.5, "background", 4.5, "select placeholder and input hint (bg-input/50)"],
    ["muted-foreground", 1, "muted", 1, "background", 4.5, "secondary text on a muted surface"],
  ],
};

describe.each([
  ["light", light, TINTED.light],
  ["dark", dark, TINTED.dark],
] as const)("%s theme, on tinted surfaces", (_name, theme, pairs) => {
  it.each(pairs)("%s at %s on %s at %s over %s meets %s:1 (%s)", (fg, fgOpacity, surface, surfaceOpacity, page, minimum) => {
    for (const token of [fg, surface, page]) expect(theme[token], `--${token} is defined`).toBeDefined();
    const base = rgba(theme[page] as string);
    const background = over(rgba(theme[surface] as string), surfaceOpacity, base);
    const text = over(rgba(theme[fg] as string), fgOpacity, background);

    expect(ratio(text, background)).toBeGreaterThanOrEqual(minimum + BLEND_MARGIN);
  });
});

describe("contrast helper", () => {
  it("matches known WCAG values", () => {
    expect(contrast("oklch(0 0 0)", "oklch(1 0 0)")).toBeCloseTo(21, 1);
    expect(contrast("oklch(1 0 0)", "oklch(1 0 0)")).toBeCloseTo(1, 5);
  });

  it("composites a tint the way the browser does", () => {
    // Chrome paints the preset's bg-destructive/10 on white as #fde6e7, and bg-input/50 as #f2f2f2.
    const white = rgba("oklch(1 0 0)");
    const bytes = (colour: Rgba) => colour.slice(0, 3).map((c) => c * 255);
    const tint = bytes(over(rgba("oklch(0.577 0.245 27.325)"), 0.1, white));
    [0xfd, 0xe6, 0xe7].forEach((expected, i) => expect(Math.abs((tint[i] as number) - expected)).toBeLessThanOrEqual(1));
    const input = bytes(over(rgba("oklch(0.922 0 0)"), 0.5, white));
    [0xf2, 0xf2, 0xf2].forEach((expected, i) => expect(Math.abs((input[i] as number) - expected)).toBeLessThanOrEqual(1));
  });
});
