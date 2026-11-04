"""ASGI composition: FastAPI for HTTP, wrapped by Python Socket.IO."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import socketio
from fastapi import FastAPI

from app.api.v1 import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.errors import RequestContextMiddleware, install_error_handlers
from app.core.logging import configure_logging
from app.db.session import create_database
from app.realtime.server import create_socketio_server


def create_http_app(settings: Settings) -> FastAPI:
    configure_logging(settings)
    database = create_database(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
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
    install_error_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_v1_router, prefix="/api/v1")
    return app


def create_app(settings: Settings | None = None) -> socketio.ASGIApp:
    settings = settings or get_settings()
    sio = create_socketio_server(settings)
    return socketio.ASGIApp(sio, other_asgi_app=create_http_app(settings))


app = create_app()
