"""Security headers on every HTTP response the API sends, including errors and refusals."""

from collections.abc import Collection

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# The API answers with JSON only: nothing it serves may run, load or embed anything, and no
# page may frame it. Browsers ignore this on JSON, but it disarms a response that a browser is
# tricked into rendering as a document.
API_CONTENT_SECURITY_POLICY = "default-src 'none'; frame-ancestors 'none'"
# Production only, where the API is always reached over https. Sent locally, it would make the
# browser refuse plain http to that host for a year.
STRICT_TRANSPORT_SECURITY = "max-age=31536000; includeSubDomains"
# Every answer under these is personal (a profile, a conversation, a chat frame), so no browser
# or shared cache may keep one.
NO_STORE_PATH_PREFIXES = ("/api/", "/socket.io/")

BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # Same-site, not same-origin: the web app on the site's own host reads the API on a
    # sibling host (api.<domain>), which is the same site.
    "Cross-Origin-Resource-Policy": "same-site",
}

RawHeaders = list[tuple[bytes, bytes]]


def _encoded(headers: dict[str, str]) -> tuple[frozenset[bytes], RawHeaders]:
    raw = [
        (name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()
    ]
    return frozenset(name for name, _ in raw), raw


class SecurityHeadersMiddleware:
    """Sets the headers on every response start, replacing whatever a route set, so a route
    can never loosen them. It wraps the whole composed app once (Socket.IO in front of
    FastAPI), so it covers Engine.IO's own long-polling answers and the 500 Starlette's
    outermost error handler sends, as well as every route.

    `csp_exempt_paths` exist for the interactive API docs only (served outside production),
    which load Swagger UI from a CDN and bootstrap it with an inline script.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool, csp_exempt_paths: Collection[str] = ()) -> None:
        self.app = app
        self.csp_exempt_paths = frozenset(csp_exempt_paths)
        base = dict(BASE_HEADERS)
        if hsts:
            base["Strict-Transport-Security"] = STRICT_TRANSPORT_SECURITY
        # The four combinations, built once: (with the CSP, with no-store).
        self._variants = {
            (csp, no_store): _encoded(
                base
                | ({"Content-Security-Policy": API_CONTENT_SECURITY_POLICY} if csp else {})
                | ({"Cache-Control": "no-store"} if no_store else {})
            )
            for csp in (True, False)
            for no_store in (True, False)
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        names, values = self._variants[
            (path not in self.csp_exempt_paths, path.startswith(NO_STORE_PATH_PREFIXES))
        ]

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                kept = [h for h in message.get("headers", ()) if h[0].lower() not in names]
                message["headers"] = kept + values
            await send(message)

        await self.app(scope, receive, send_with_headers)
