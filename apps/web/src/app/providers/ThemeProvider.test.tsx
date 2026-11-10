import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { THEME_STORAGE_KEY, ThemeProvider, useTheme } from "@/app/providers/ThemeProvider";
import { setSystemDark } from "@/test/match-media";

import indexHtml from "../../index.html?raw";

function Probe() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  return (
    <div>
      <p data-testid="theme">{theme}</p>
      <p data-testid="resolved">{resolvedTheme}</p>
      <button type="button" onClick={() => setTheme("dark")}>
        dark
      </button>
      <button type="button" onClick={() => setTheme("light")}>
        light
      </button>
      <button type="button" onClick={() => setTheme("system")}>
        system
      </button>
    </div>
  );
}

const html = document.documentElement;
const text = (id: string) => screen.getByTestId(id).textContent;

describe("ThemeProvider", () => {
  it("follows the system preference when nothing is stored", () => {
    setSystemDark(true);
    render(<ThemeProvider><Probe /></ThemeProvider>);

    expect(text("theme")).toBe("system");
    expect(text("resolved")).toBe("dark");
    expect(html).toHaveClass("dark");
    expect(html.style.colorScheme).toBe("dark");
  });

  it("restores and applies a stored explicit choice", () => {
    setSystemDark(true);
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");

    render(<ThemeProvider><Probe /></ThemeProvider>);

    expect(text("theme")).toBe("light");
    expect(html).not.toHaveClass("dark");
    expect(html.style.colorScheme).toBe("light");
  });

  it("persists an explicit choice and applies it immediately", () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);

    act(() => screen.getByRole("button", { name: "dark" }).click());

    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(html).toHaveClass("dark");
  });

  it("tracks system changes live in system mode only", () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(html).not.toHaveClass("dark");

    act(() => setSystemDark(true));
    expect(html).toHaveClass("dark");

    act(() => screen.getByRole("button", { name: "light" }).click());
    act(() => setSystemDark(true));
    expect(html).not.toHaveClass("dark");
  });

  it("ignores an invalid stored value", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "purple");

    render(<ThemeProvider><Probe /></ThemeProvider>);

    expect(text("theme")).toBe("system");
  });

  it("falls back to system and keeps working when storage throws", () => {
    setSystemDark(true);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(text("theme")).toBe("system");
    expect(html).toHaveClass("dark");

    act(() => screen.getByRole("button", { name: "light" }).click());
    expect(text("theme")).toBe("light");
    expect(html).not.toHaveClass("dark");
  });

  it("throws a clear error when used outside the provider", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(() => render(<Probe />)).toThrow(/ThemeProvider/);
  });
});

describe("pre-paint theme script in index.html", () => {
  const script = /<script id="theme-init">([\s\S]*?)<\/script>/.exec(indexHtml)?.[1] ?? "";

  function runScript() {
    new Function(script)();
  }

  it("reads the same storage key as the provider", () => {
    expect(script).toContain(JSON.stringify(THEME_STORAGE_KEY));
  });

  it.each([
    { stored: "dark", systemDark: false, dark: true },
    { stored: "light", systemDark: true, dark: false },
    { stored: "system", systemDark: true, dark: true },
    { stored: null, systemDark: true, dark: true },
    { stored: null, systemDark: false, dark: false },
  ])("stored=$stored, system dark=$systemDark -> dark=$dark", ({ stored, systemDark, dark }) => {
    if (stored) window.localStorage.setItem(THEME_STORAGE_KEY, stored);
    setSystemDark(systemDark);

    runScript();

    expect(html.classList.contains("dark")).toBe(dark);
    expect(html.style.colorScheme).toBe(dark ? "dark" : "light");
  });

  it("does not throw and follows the system when storage is blocked", () => {
    setSystemDark(true);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked", "SecurityError");
    });

    expect(runScript).not.toThrow();
    expect(html).toHaveClass("dark");
  });
});
