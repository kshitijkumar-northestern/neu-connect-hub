// Replaced at build time by webpack DefinePlugin (and by `define` in vitest.config.ts).
// Only public values belong here; backend configuration never reaches the browser.
declare const __API_BASE_URL__: string;
declare const __SOCKET_URL__: string;

export const config = {
  apiBaseUrl: __API_BASE_URL__,
  /** Empty means the page's own origin. */
  socketUrl: __SOCKET_URL__,
} as const;
