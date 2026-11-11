"""Version 1 of the HTTP API, mounted at /api/v1."""

from fastapi import APIRouter, Depends

from app.api.v1 import health
from app.core.security import require_trusted_origin
from app.modules.auth.router import router as auth_router
from app.modules.chat.router import router as chat_router
from app.modules.connections.router import router as connections_router
from app.modules.profiles.router import router as profiles_router
from app.modules.profiles.trust_router import router as trust_router
from app.modules.teams.router import router as teams_router

# Every unsafe request under /api/v1 must come from the web origin (CSRF defence).
router = APIRouter(dependencies=[Depends(require_trusted_origin)])
router.include_router(health.router)
router.include_router(auth_router)
router.include_router(profiles_router)
router.include_router(trust_router)
router.include_router(connections_router)
router.include_router(chat_router)
router.include_router(teams_router)
