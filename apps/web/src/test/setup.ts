import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { installMatchMedia, resetMatchMedia } from "@/test/match-media";

// Some tests run in the node environment (no DOM); only patch browser APIs when there is one.
const hasDom = typeof window !== "undefined";

if (hasDom) {
  // jsdom lacks these browser APIs; Radix menus need ResizeObserver and scrollIntoView.
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView ??= () => {};
  installMatchMedia();
}

beforeEach(() => {
  if (!hasDom) return;
  window.localStorage.clear();
  document.documentElement.className = "";
  document.documentElement.style.colorScheme = "";
});

afterEach(() => {
  if (!hasDom) return;
  cleanup();
  resetMatchMedia();
  vi.unstubAllGlobals(); // e.g. a fake fetch from src/test/fake-api.ts
});
