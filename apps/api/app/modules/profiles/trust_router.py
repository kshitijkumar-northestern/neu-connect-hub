import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.profiles.schemas import BlockedUser, BlockRequest, ReportRequest
from app.modules.profiles.trust import TrustService

router = APIRouter(tags=["trust"])


def trust_service(request: Request, db: AsyncSession = Depends(get_session)) -> TrustService:
    return TrustService(db, request.app.state.limiter)


@router.post("/blocks", status_code=204)
async def create_block(
    body: BlockRequest,
    current: CurrentSession = Depends(require_verified),
    service: TrustService = Depends(trust_service),
) -> Response:
    await service.block(current.user.id, body.user_id)
    return Response(status_code=204)


@router.delete("/blocks/{user_id}", status_code=204)
async def delete_block(
    user_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: TrustService = Depends(trust_service),
) -> Response:
    await service.unblock(current.user.id, user_id)
    return Response(status_code=204)


@router.get("/blocks")
async def list_blocks(
    current: CurrentSession = Depends(require_verified),
    service: TrustService = Depends(trust_service),
) -> list[BlockedUser]:
    return await service.list_blocks(current.user.id)


@router.post("/reports", status_code=202)
async def create_report(
    body: ReportRequest,
    current: CurrentSession = Depends(require_verified),
    service: TrustService = Depends(trust_service),
) -> dict[str, str]:
    await service.report(current.user.id, body.user_id, body.reason, body.details)
    return {"status": "report_received"}
