import asyncio
import logging

from fastapi import APIRouter, Request
from sqlalchemy import text

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import Database

logger = logging.getLogger("app.health")

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up and serving requests."""
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    """Readiness: the database answers within the configured timeout."""
    settings: Settings = request.app.state.settings
    database: Database = request.app.state.db
    try:
        async with asyncio.timeout(settings.readiness_timeout_seconds):
            async with database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Readiness check failed", extra={"reason": type(exc).__name__})
        raise AppError(503, "not_ready", "The service is not ready.") from exc
    return {"status": "ok", "database": "ok"}
