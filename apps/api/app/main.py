"""ASGI composition: FastAPI for HTTP, wrapped by Python Socket.IO."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI

from app.api.v1 import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.errors import RequestContextMiddleware, install_error_handlers
from app.core.limits import BodySizeLimitMiddleware
from app.core.logging import configure_logging
from app.core.mail import Mailer, create_mailer
from app.core.rate_limit import RateLimiter
from app.db.session import create_database
from app.ml.encoders import create_encoder
from app.ml.worker import EmbeddingWorker
from app.modules.auth.passwords import PasswordService
from app.realtime.calls import CallRegistry
from app.realtime.presence import Presence
from app.realtime.server import create_socketio_server


def create_http_app(settings: Settings, *, mailer: Mailer | None = None) -> FastAPI:
    configure_logging(settings)
    database = create_database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The encoder (and the real model behind it) is built here, not at app-construction
        # time, and only when the worker is enabled: importing or building the app must never
        # load the model, so a web-only replica that never runs the worker never pays for it.
        task: asyncio.Task[None] | None = None
        if settings.embedding_worker_enabled:
            encoder = create_encoder(settings)
            if encoder is not None:
                worker = EmbeddingWorker(database, encoder, settings)
                app.state.embedding_worker = worker
                task = asyncio.create_task(worker.run_forever())
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await database.engine.dispose()

    docs_enabled = not settings.is_production
    app = FastAPI(
        title="NU Connect Hub API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if docs_enabled else None,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.db = database
    # Set for real by the lifespan, only when enabled and a model actually loads; readiness and
    # tests read this directly.
    app.state.embedding_worker = None
    app.state.mailer = mailer or create_mailer(settings)
    app.state.passwords = PasswordService(settings)
    app.state.limiter = RateLimiter()
    app.state.presence = Presence()
    app.state.calls = CallRegistry()
    install_error_handlers(app)
    # Added first, so it runs inside RequestContextMiddleware and its 413s carry a request ID.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_v1_router, prefix="/api/v1")
    return app


def create_app(
    settings: Settings | None = None, *, mailer: Mailer | None = None
) -> socketio.ASGIApp:
    settings = settings or get_settings()
    http_app = create_http_app(settings, mailer=mailer)
    state = http_app.state
    sio, rooms_hooks = create_socketio_server(
        settings, state.db, state.limiter, state.presence, state.calls
    )
    http_app.state.sio = sio  # lets logout and password reset disconnect sockets
    http_app.state.room_access = rooms_hooks.recheck_room_access
    return socketio.ASGIApp(sio, other_asgi_app=http_app)


app = create_app()
