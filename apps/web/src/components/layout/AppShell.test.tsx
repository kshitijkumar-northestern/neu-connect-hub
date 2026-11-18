import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it } from "vitest";

import { THEME_STORAGE_KEY, ThemeProvider } from "@/app/providers/ThemeProvider";
import { AppShell } from "@/components/layout/AppShell";
import { BrandMark } from "@/components/layout/BrandMark";
import logoOnDark from "@/assets/brand/logo-dark.svg";
import logoOnLight from "@/assets/brand/logo-light.svg";
import { AuthProvider } from "@/features/auth/AuthProvider";
import { expectNoAxeViolations, OPEN_POPUP } from "@/test/axe";
import { ADA, apiError, fakeApi } from "@/test/fake-api";
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

  it("has no axe violations, and the brand link is named once, not by the mark and the text both", async () => {
    renderShell();

    const home = within(screen.getByRole("banner")).getByRole("link", { name: "NU Connect Hub home" });
    expect(within(home).queryByRole("img")).not.toBeInTheDocument();
    expect(await within(screen.getByRole("banner")).findByRole("link", { name: "Sign in" })).toBeInTheDocument();
    await expectNoAxeViolations();
  });

  it("the open account and theme menus have no axe violations and don't hide the page", async () => {
    const user = userEvent.setup();
    fakeApi({ "GET /me": [200, { ...ADA, role: "admin" }] });
    renderShell();
    // A menu is not a modal: hiding the rest of the page (aria-hidden) while it's open would leave
    // its own trigger focusable but hidden from assistive technology.
    const hidden = () => document.querySelectorAll("[aria-hidden=true]:has(a, button)");

    await user.click(await screen.findByRole("button", { name: /^Account:/ }));
    expect(await screen.findByRole("menuitem", { name: "Sign out" })).toBeInTheDocument();
    expect(hidden()).toHaveLength(0);
    await expectNoAxeViolations(document.body, OPEN_POPUP);
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /^Account:/ })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: /^Theme:/ }));
    expect(await screen.findByRole("menuitemradio", { name: "Dark" })).toBeInTheDocument();
    expect(hidden()).toHaveLength(0);
    await expectNoAxeViolations(document.body, OPEN_POPUP);
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
