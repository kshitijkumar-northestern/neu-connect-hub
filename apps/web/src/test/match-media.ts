// A controllable stand-in for window.matchMedia, which jsdom does not implement.
type Listener = (event: MediaQueryListEvent) => void;

const DARK_QUERY = "(prefers-color-scheme: dark)";
let systemDark = false;
const listeners = new Set<Listener>();

export function setSystemDark(dark: boolean): void {
  systemDark = dark;
  for (const listener of listeners) listener({ matches: dark, media: DARK_QUERY } as MediaQueryListEvent);
}

export function resetMatchMedia(): void {
  systemDark = false;
  listeners.clear();
}

export function installMatchMedia(): void {
  window.matchMedia = (query: string) =>
    ({
      media: query,
      get matches() {
        return query === DARK_QUERY ? systemDark : false;
      },
      onchange: null,
      addEventListener: (_type: string, listener: Listener) => listeners.add(listener),
      removeEventListener: (_type: string, listener: Listener) => listeners.delete(listener),
      addListener: (listener: Listener) => listeners.add(listener),
      removeListener: (listener: Listener) => listeners.delete(listener),
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
