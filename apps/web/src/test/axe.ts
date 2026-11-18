// axe-core accessibility checks for pages rendered in jsdom.
import type { RunOptions } from "axe-core";
import { expect } from "vitest";
import { configureAxe } from "vitest-axe";
// The package's "vitest-axe/matchers" entry declares the matcher as a type-only export.
import { toHaveNoViolations } from "vitest-axe/dist/matchers";

// Every default rule except colour contrast: jsdom has no layout or computed colours, so axe
// can't judge contrast here. The real-browser axe pass checks contrast in both themes.
const axe = configureAxe({ rules: { "color-contrast": { enabled: false } } });

/** Fails with axe's report if the document as rendered, portals (dialogs, menus) included, has
 * any accessibility violation. Call it once the page has settled into the state under test. */
export async function expectNoAxeViolations(root: Element = document.body, options: RunOptions = {}): Promise<void> {
  const { pass, message } = toHaveNoViolations(await axe(root, options));
  expect(pass, message()).toBe(true);
}

/** For a state with an open menu or listbox: Radix portals the popup to the end of <body>,
 * outside the page's landmarks, though it belongs to its trigger, so the landmark rule is set
 * aside while it's open. Open dialogs pass without it. */
export const OPEN_POPUP: RunOptions = { rules: { region: { enabled: false } } };
