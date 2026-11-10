"""Version 1 of the HTTP API, mounted at /api/v1."""

from fastapi import APIRouter

from app.api.v1 import health

router = APIRouter()
router.include_router(health.router)
