"""Administrator MFA: enrolment, recovery codes and step-up verification."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.session import get_session
from app.modules.admin.mfa_schemas import (
    ConfirmRequest,
    ConfirmResponse,
    EnrollResponse,
    MfaStatus,
    VerifyRequest,
)
from app.modules.admin.mfa_service import MfaService
from app.modules.auth.sessions import CurrentSession

router = APIRouter(prefix="/admin/mfa", tags=["admin"])


def mfa_service(request: Request, db: AsyncSession = Depends(get_session)) -> MfaService:
    state = request.app.state
    return MfaService(db, state.settings, state.limiter)


@router.get("")
async def mfa_status(
    current: CurrentSession = Depends(require_admin),
    service: MfaService = Depends(mfa_service),
) -> MfaStatus:
    return await service.status(current.user, current.session)


@router.post("/enroll")
async def enroll(
    current: CurrentSession = Depends(require_admin),
    service: MfaService = Depends(mfa_service),
) -> EnrollResponse:
    return await service.enroll(current.user)


@router.post("/confirm")
async def confirm(
    body: ConfirmRequest,
    current: CurrentSession = Depends(require_admin),
    service: MfaService = Depends(mfa_service),
) -> ConfirmResponse:
    return await service.confirm(current.user, current.session, body.code)


@router.post("/verify")
async def verify(
    body: VerifyRequest,
    current: CurrentSession = Depends(require_admin),
    service: MfaService = Depends(mfa_service),
) -> MfaStatus:
    return await service.verify(current.user, current.session, body.code, body.recovery_code)
