import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.discovery.schemas import DiscoveryOut, FeedbackRequest
from app.modules.discovery.service import DiscoveryService

router = APIRouter(tags=["discovery"])


def discovery_service(
    request: Request, db: AsyncSession = Depends(get_session)
) -> DiscoveryService:
    return DiscoveryService(db, request.app.state.limiter)


@router.get("/discovery")
async def get_discovery(
    project_id: uuid.UUID | None = None,
    current: CurrentSession = Depends(require_verified),
    service: DiscoveryService = Depends(discovery_service),
) -> DiscoveryOut:
    """With `project_id`, the caller must own that (open) project; matches then favour its
    missing role skills and exclude its members and pending applicants."""
    return await service.matches(current.user.id, project_id)


@router.post("/discovery/feedback", status_code=204)
async def post_feedback(
    body: FeedbackRequest,
    current: CurrentSession = Depends(require_verified),
    service: DiscoveryService = Depends(discovery_service),
) -> Response:
    """Always 204, whether or not anything was recorded."""
    await service.feedback(current.user.id, body)
    return Response(status_code=204)
