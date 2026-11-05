"""One JSON error envelope for every failure: {"error": {"code", "message", "request_id"}}."""

import logging
import re
import uuid
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, ExceptionHandler, Message, Receive, Scope, Send

from app.core.logging import request_id_var

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

_CODES_BY_STATUS = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    429: "rate_limited",
}
INTERNAL_ERROR_MESSAGE = "Something went wrong on our side."


class AppError(Exception):
    """A deliberate failure with a stable code that clients can rely on."""

    def __init__(
        self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def error_body(
    code: str, message: str, request_id: str | None, fields: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": request_id}
    if fields is not None:
        error["fields"] = fields
    return {"error": error}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


async def _app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        error_body(exc.code, exc.message, _request_id(request)),
        exc.status_code,
        headers=exc.headers,
    )


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _CODES_BY_STATUS.get(exc.status_code, f"http_{exc.status_code}")
    safe_detail = isinstance(exc.detail, str) and exc.status_code < 500
    message = exc.detail if safe_detail else "Request failed."
    return JSONResponse(
        error_body(code, message, _request_id(request)),
        exc.status_code,
        headers=getattr(exc, "headers", None),
    )


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Only location and message: submitted values (which may be passwords) are never echoed.
    fields = [
        {"location": ".".join(str(part) for part in err["loc"]), "message": str(err["msg"])}
        for err in exc.errors()
    ]
    return JSONResponse(
        error_body("validation_error", "The request is invalid.", _request_id(request), fields), 422
    )


def install_error_handlers(app: FastAPI) -> None:
    # Starlette types handlers as taking any Exception; each one is only called for its class.
    app.add_exception_handler(AppError, cast(ExceptionHandler, _app_error))
    app.add_exception_handler(StarletteHTTPException, cast(ExceptionHandler, _http_error))
    app.add_exception_handler(RequestValidationError, cast(ExceptionHandler, _validation_error))


class RequestContextMiddleware:
    """Assigns a request ID to every HTTP request and turns unhandled errors into a 500 envelope.

    A valid incoming X-Request-ID (from a trusted proxy) is kept; anything else is replaced.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = (
            dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode(), b"").decode("latin-1")
        )
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        token = request_id_var.set(request_id)
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in headers:
                    headers.append(REQUEST_ID_HEADER, request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("Unhandled error", extra={"request_id": request_id})
            if response_started:
                raise
            response = JSONResponse(
                error_body("internal_error", INTERNAL_ERROR_MESSAGE, request_id), 500
            )
            await response(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)
