// The token NU Connect Hub's web origin puts after "#token=" in a /connect link: 43 characters of
// unpadded base64url (apps/api/app/modules/scan/service.py encodes it the same way).
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/;

/**
 * Pulls the token out of whatever a scanned QR code, a pasted link or an uploaded photo decoded
 * to. Returns it only when `text` is an absolute URL on this web origin, whose path is exactly
 * `/connect`, carries no query string, and whose hash is `#token=` followed by exactly 43
 * `[A-Za-z0-9_-]` characters — nothing more, nothing less. Everything else (another origin, a
 * `javascript:` URL, extra query params, a short/long/padded token, or a bare token with no URL
 * around it) is null, never thrown.
 */
export function parseConnectLink(text: string): string | null {
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }

  if (url.origin !== window.location.origin) return null;
  if (url.pathname !== "/connect") return null;
  if (url.search !== "") return null;

  const prefix = "#token=";
  if (!url.hash.startsWith(prefix)) return null;
  const token = url.hash.slice(prefix.length);
  return TOKEN_PATTERN.test(token) ? token : null;
}
