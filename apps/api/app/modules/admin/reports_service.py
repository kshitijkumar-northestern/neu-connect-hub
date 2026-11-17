"""The report queue: list, view, dismiss and suspend. Dismiss and suspend both change a
report's status with a conditional UPDATE or row lock, so two admins racing the same report
never both win."""

import uuid
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import AppError
from app.ml.models import Embedding
from app.modules.admin import audit
from app.modules.admin.reports_schemas import (
    AdminReport,
    AdminReportDetail,
    ReporterSummary,
    ReportPage,
    ReportStatus,
    TargetContext,
    TargetSummary,
)
from app.modules.auth.models import User, UserSession
from app.modules.profiles.models import Profile, Report
from app.modules.profiles.schemas import ReportReason

NOT_FOUND = "Report not found."
CLOSED = "This report is no longer open."
CANNOT_SUSPEND_ADMIN = "An administrator's account can't be suspended this way."


class ReportsService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, status: ReportStatus, limit: int, offset: int) -> ReportPage:
        # Open reports read like a queue (oldest first); closed ones read newest first, since
        # that's what an admin checking recent decisions wants.
        ascending = status == "open"
        order = Report.created_at.asc() if ascending else Report.created_at.desc()
        tiebreak = Report.id.asc() if ascending else Report.id.desc()
        rows = (
            await self.db.execute(
                self._select()
                .where(Report.status == status)
                .order_by(order, tiebreak)
                .offset(offset)
                .limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        items = [
            self._out(report, reporter_name, target_name, suspended_at)
            for report, reporter_name, target_name, suspended_at in rows[:limit]
        ]
        return ReportPage(items=items, next_offset=offset + limit if has_more else None)

    async def detail(self, admin_id: uuid.UUID, report_id: uuid.UUID) -> AdminReportDetail:
        row = (await self.db.execute(self._select().where(Report.id == report_id))).first()
        if row is None:
            raise AppError(404, "not_found", NOT_FOUND)
        report, reporter_name, target_name, suspended_at = row
        context = await self._target_context(report.target_user_id)
        audit.record(
            self.db,
            "report_viewed",
            admin_id=admin_id,
            target_user_id=report.target_user_id,
            report_id=report.id,
        )
        await self.db.commit()
        base = self._out(report, reporter_name, target_name, suspended_at)
        return AdminReportDetail(**base.model_dump(), target_context=context)

    async def dismiss(self, admin_id: uuid.UUID, report_id: uuid.UUID) -> AdminReport:
        target_id = await self.db.scalar(
            update(Report)
            .where(Report.id == report_id, Report.status == "open")
            .values(status="dismissed", resolved_by=admin_id, resolved_at=func.now())
            .returning(Report.target_user_id)
        )
        if target_id is None:
            await self.db.rollback()
            raise await self._not_open_error(report_id)
        audit.record(
            self.db,
            "report_dismissed",
            admin_id=admin_id,
            target_user_id=target_id,
            report_id=report_id,
        )
        await self.db.commit()
        return await self._view(report_id)

    async def suspend(self, admin_id: uuid.UUID, report_id: uuid.UUID) -> AdminReport:
        """Lock the report, then its target, in that order, so a concurrent dismiss and a
        concurrent suspend on the same report always leave exactly one of them to win."""
        report = await self.db.scalar(
            select(Report).where(Report.id == report_id).with_for_update()
        )
        if report is None:
            await self.db.rollback()
            raise AppError(404, "not_found", NOT_FOUND)
        if report.status != "open":
            await self.db.rollback()
            raise AppError(409, "report_closed", CLOSED)

        target = await self.db.scalar(
            select(User).where(User.id == report.target_user_id).with_for_update()
        )
        if target is None:  # the account vanished under the report (shouldn't happen: CASCADE)
            await self.db.rollback()
            raise AppError(404, "not_found", NOT_FOUND)
        if target.role == "admin":
            await self.db.rollback()
            raise AppError(409, "cannot_suspend_admin", CANNOT_SUSPEND_ADMIN)

        now = datetime.now(UTC)
        target.suspended_at = now
        await self.db.execute(delete(UserSession).where(UserSession.user_id == target.id))
        # A suspended account must never keep a vector matching could still surface, and it
        # shouldn't outlive the suspension either.
        await self.db.execute(delete(Embedding).where(Embedding.user_id == target.id))
        # Every open report against this target closes with the suspension, including the one
        # that triggered it.
        await self.db.execute(
            update(Report)
            .where(Report.target_user_id == target.id, Report.status == "open")
            .values(status="resolved", resolved_by=admin_id, resolved_at=now)
        )
        audit.record(
            self.db,
            "account_suspended",
            admin_id=admin_id,
            target_user_id=target.id,
            report_id=report_id,
        )
        await self.db.commit()
        return await self._view(report_id)

    # --- helpers ----------------------------------------------------------------------------

    def _select(self) -> Select[Report, str, str, datetime | None]:
        reporter_profile = aliased(Profile)
        target_profile = aliased(Profile)
        return (
            select(
                Report,
                reporter_profile.display_name,
                target_profile.display_name,
                User.suspended_at,
            )
            .join(User, User.id == Report.target_user_id)
            .outerjoin(reporter_profile, reporter_profile.user_id == Report.reporter_id)
            .outerjoin(target_profile, target_profile.user_id == Report.target_user_id)
        )

    def _out(
        self,
        report: Report,
        reporter_name: str | None,
        target_name: str | None,
        suspended_at: datetime | None,
    ) -> AdminReport:
        reporter = (
            None
            if report.reporter_id is None
            else ReporterSummary(user_id=report.reporter_id, display_name=reporter_name)
        )
        return AdminReport(
            id=report.id,
            reason=cast(ReportReason, report.reason),
            details=report.details,
            status=cast(ReportStatus, report.status),
            created_at=report.created_at,
            resolved_at=report.resolved_at,
            reporter=reporter,
            target=TargetSummary(
                user_id=report.target_user_id,
                display_name=target_name,
                suspended=suspended_at is not None,
            ),
        )

    async def _view(self, report_id: uuid.UUID) -> AdminReport:
        row = (await self.db.execute(self._select().where(Report.id == report_id))).first()
        if row is None:
            raise AppError(404, "not_found", NOT_FOUND)
        report, reporter_name, target_name, suspended_at = row
        return self._out(report, reporter_name, target_name, suspended_at)

    async def _target_context(self, target_id: uuid.UUID) -> TargetContext:
        row = (
            await self.db.execute(
                select(func.coalesce(Profile.headline, ""), User.created_at, User.verified_at)
                .select_from(User)
                .outerjoin(Profile, Profile.user_id == User.id)
                .where(User.id == target_id)
            )
        ).one()
        headline, member_since, verified_at = row
        total_reports, open_reports = (
            await self.db.execute(
                select(func.count(), func.count().filter(Report.status == "open")).where(
                    Report.target_user_id == target_id
                )
            )
        ).one()
        return TargetContext(
            headline=headline,
            member_since=member_since,
            verified=verified_at is not None,
            open_reports=open_reports,
            total_reports=total_reports,
        )

    async def _not_open_error(self, report_id: uuid.UUID) -> AppError:
        status = await self.db.scalar(select(Report.status).where(Report.id == report_id))
        if status is None:
            return AppError(404, "not_found", NOT_FOUND)
        return AppError(409, "report_closed", CLOSED)
