import { act, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { THEME_STORAGE_KEY, ThemeProvider } from "@/app/providers/ThemeProvider";
import { AppShell } from "@/components/layout/AppShell";
import { BrandMark } from "@/components/layout/BrandMark";
import logoOnDark from "@/assets/brand/logo-dark.svg";
import logoOnLight from "@/assets/brand/logo-light.svg";
import { AuthProvider } from "@/features/auth/AuthProvider";
import { apiError, fakeApi } from "@/test/fake-api";
import { setSystemDark } from "@/test/match-media";

beforeEach(() => {
  fakeApi({ "GET /me": apiError(401, "unauthorized") });
});

function renderShell() {
  return render(
    <ThemeProvider>
      <AuthProvider>
        <MemoryRouter>
          <AppShell>
            <p>page content</p>
          </AppShell>
        </MemoryRouter>
      </AuthProvider>
    </ThemeProvider>,
  );
}

describe("AppShell", () => {
  it("has a header with the brand linking home and the theme toggle, and a main region", () => {
    renderShell();

    const header = screen.getByRole("banner");
    const home = within(header).getByRole("link", { name: "NU Connect Hub home" });
    expect(home).toHaveAttribute("href", "/");
    expect(within(header).getByRole("button", { name: /^Theme:/ })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("page content")).toBeInTheDocument();
  });
});

describe("BrandMark", () => {
  function renderMark() {
    return render(
      <ThemeProvider>
        <BrandMark />
      </ThemeProvider>,
    );
  }

  it("uses the dark mark on light surfaces", () => {
    renderMark();

    const mark = screen.getByRole("img", { name: "NU Connect Hub" });
    expect(mark.getAttribute("src")).toBe(logoOnLight);
  });

  it("uses the white mark on dark surfaces and swaps when the system theme changes", () => {
    renderMark();

    act(() => setSystemDark(true));
    expect(screen.getByRole("img", { name: "NU Connect Hub" }).getAttribute("src")).toBe(logoOnDark);
  });

  it("follows an explicit theme choice over the system", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "dark");
    renderMark();

    expect(screen.getByRole("img", { name: "NU Connect Hub" }).getAttribute("src")).toBe(logoOnDark);
  });

  it("is never framed: no border, background or ring on the mark", () => {
    renderMark();

    const classes = screen.getByRole("img", { name: "NU Connect Hub" }).className;
    expect(classes).not.toMatch(/\b(border|ring|bg-|shadow|outline)/);
  });
});
