import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { THEME_STORAGE_KEY, ThemeProvider } from "@/app/providers/ThemeProvider";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

function renderToggle() {
  return render(
    <ThemeProvider>
      <ThemeToggle />
    </ThemeProvider>,
  );
}

describe("ThemeToggle", () => {
  it("has an accessible name that announces the current choice", () => {
    renderToggle();

    expect(screen.getByRole("button", { name: "Theme: System" })).toBeInTheDocument();
  });

  it("switches theme with the keyboard alone and returns focus to the trigger", async () => {
    const user = userEvent.setup();
    renderToggle();

    await user.tab();
    const trigger = screen.getByRole("button", { name: "Theme: System" });
    expect(trigger).toHaveFocus();

    await user.keyboard("{Enter}");
    const dark = await screen.findByRole("menuitemradio", { name: "Dark" });
    expect(screen.getByRole("menuitemradio", { name: "System" })).toHaveAttribute("aria-checked", "true");

    dark.focus();
    await user.keyboard("{Enter}");

    expect(document.documentElement).toHaveClass("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Theme: Dark" })).toHaveFocus();
  });

  it("marks the active choice when reopened", async () => {
    const user = userEvent.setup();
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    renderToggle();

    await user.click(screen.getByRole("button", { name: "Theme: Light" }));

    expect(await screen.findByRole("menuitemradio", { name: "Light" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("menuitemradio", { name: "Dark" })).toHaveAttribute("aria-checked", "false");
  });
});
