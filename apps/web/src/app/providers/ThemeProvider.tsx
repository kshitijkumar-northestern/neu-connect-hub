import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

/** Must match the key read by the pre-paint script in src/index.html. */
export const THEME_STORAGE_KEY = "nu-theme";

export type Theme = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const THEMES: readonly Theme[] = ["light", "dark", "system"];
const DARK_QUERY = "(prefers-color-scheme: dark)";

interface ThemeContextValue {
  /** The user's choice. */
  theme: Theme;
  /** What is actually shown: the choice, or the system preference in system mode. */
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return THEMES.includes(stored as Theme) ? (stored as Theme) : "system";
  } catch {
    return "system"; // storage blocked (private mode, disabled site data)
  }
}

function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The choice still applies for this page view; it just isn't remembered.
  }
}

function systemPrefersDark(): boolean {
  return window.matchMedia(DARK_QUERY).matches;
}

function applyTheme(resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(readStoredTheme);
  const [systemDark, setSystemDark] = useState<boolean>(systemPrefersDark);

  useEffect(() => {
    const query = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const resolvedTheme: ResolvedTheme = theme === "system" ? (systemDark ? "dark" : "light") : theme;

  useEffect(() => {
    applyTheme(resolvedTheme);
  }, [resolvedTheme]);

  const setTheme = useCallback((next: Theme) => {
    storeTheme(next);
    setThemeState(next);
  }, []);

  const value = useMemo(() => ({ theme, resolvedTheme, setTheme }), [theme, resolvedTheme, setTheme]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used inside <ThemeProvider>");
  return context;
}
