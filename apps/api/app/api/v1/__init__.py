"""Version 1 of the HTTP API, mounted at /api/v1."""

from fastapi import APIRouter, Depends

from app.api.v1 import health
from app.core.security import require_trusted_origin
from app.modules.auth.router import router as auth_router

# Every unsafe request under /api/v1 must come from the web origin (CSRF defence).
router = APIRouter(dependencies=[Depends(require_trusted_origin)])
router.include_router(health.router)
router.include_router(auth_router)
