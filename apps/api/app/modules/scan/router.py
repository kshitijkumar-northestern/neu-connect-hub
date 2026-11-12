from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.scan.schemas import ConnectionCode, ConnectPreview, ResolveRequest
from app.modules.scan.service import ScanService

router = APIRouter(tags=["scan"])


def scan_service(request: Request, db: AsyncSession = Depends(get_session)) -> ScanService:
    state = request.app.state
    return ScanService(db, state.settings, state.limiter)


@router.get("/me/connection-code")
async def my_connection_code(
    current: CurrentSession = Depends(require_verified),
    service: ScanService = Depends(scan_service),
) -> ConnectionCode:
    return await service.my_code(current.user.id)


@router.post("/me/connection-code/rotate", status_code=201)
async def rotate_connection_code(
    current: CurrentSession = Depends(require_verified),
    service: ScanService = Depends(scan_service),
) -> ConnectionCode:
    return await service.rotate(current.user.id)


@router.delete("/me/connection-code", status_code=204)
async def disable_connection_code(
    current: CurrentSession = Depends(require_verified),
    service: ScanService = Depends(scan_service),
) -> Response:
    await service.disable(current.user.id)
    return Response(status_code=204)


@router.post("/connect/resolve")
async def resolve_connect_code(
    body: ResolveRequest,
    current: CurrentSession = Depends(require_verified),
    service: ScanService = Depends(scan_service),
) -> ConnectPreview:
    """The token travels in the body, never in a URL any proxy or tracer in front of the API
    could log."""
    return await service.resolve(current.user.id, body.token)
