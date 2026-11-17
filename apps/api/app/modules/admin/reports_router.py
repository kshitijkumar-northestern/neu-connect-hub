"""The report queue: list, view, dismiss and suspend."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin_mfa
from app.db.session import get_session
from app.modules.admin.reports_schemas import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    AdminReport,
    AdminReportDetail,
    ReportPage,
    ReportStatus,
)
from app.modules.admin.reports_service import ReportsService
from app.modules.auth.sessions import CurrentSession
from app.realtime.server import disconnect_room, user_room

router = APIRouter(prefix="/admin/reports", tags=["admin"])


def reports_service(db: AsyncSession = Depends(get_session)) -> ReportsService:
    return ReportsService(db)


async def _disconnect(request: Request, user: uuid.UUID) -> None:
    sio = getattr(request.app.state, "sio", None)
    if sio is not None:
        await disconnect_room(sio, user_room(user))


@router.get("")
async def list_reports(
    status: ReportStatus = "open",
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    current: CurrentSession = Depends(require_admin_mfa),
    service: ReportsService = Depends(reports_service),
) -> ReportPage:
    return await service.list(status, limit, offset)


@router.get("/{report_id}")
async def get_report(
    report_id: uuid.UUID,
    current: CurrentSession = Depends(require_admin_mfa),
    service: ReportsService = Depends(reports_service),
) -> AdminReportDetail:
    return await service.detail(current.user.id, report_id)


@router.post("/{report_id}/dismiss")
async def dismiss_report(
    report_id: uuid.UUID,
    current: CurrentSession = Depends(require_admin_mfa),
    service: ReportsService = Depends(reports_service),
) -> AdminReport:
    return await service.dismiss(current.user.id, report_id)


@router.post("/{report_id}/suspend")
async def suspend_account(
    report_id: uuid.UUID,
    request: Request,
    current: CurrentSession = Depends(require_admin_mfa),
    service: ReportsService = Depends(reports_service),
) -> AdminReport:
    result = await service.suspend(current.user.id, report_id)
    # Sessions are already gone; this clears any socket still connected under the old cookie,
    # the same way logout and password reset do.
    await _disconnect(request, result.target.user_id)
    return result
