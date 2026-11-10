"""Request body size limit, enforced before FastAPI parses anything."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body

TOO_LARGE = "The request is too large."


class BodySizeLimitMiddleware:
    """Refuses bodies over `max_bytes`: by Content-Length up front, or while streaming when the
    length isn't declared (chunked uploads)."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            request_id = scope.get("state", {}).get("request_id")
            response = JSONResponse(error_body("payload_too_large", TOO_LARGE, request_id), 413)
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    # FastAPI re-raises HTTPException from body parsing; the app's handler
                    # turns it into the usual error envelope.
                    raise HTTPException(413, TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)
