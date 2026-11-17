"""Shapes for the report queue: what the list, detail and action endpoints return."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.modules.profiles.schemas import ReportReason

ReportStatus = Literal["open", "resolved", "dismissed"]

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


class ReporterSummary(BaseModel):
    user_id: uuid.UUID
    display_name: str | None


class TargetSummary(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    suspended: bool


class AdminReport(BaseModel):
    """Never the reported conversation's message bodies: only the report's own text, which the
    reporter wrote for this purpose."""

    id: uuid.UUID
    reason: ReportReason
    details: str
    status: ReportStatus
    created_at: datetime
    resolved_at: datetime | None
    reporter: ReporterSummary | None  # None once the reporter has deleted their account
    target: TargetSummary


class TargetContext(BaseModel):
    headline: str
    member_since: datetime
    verified: bool
    open_reports: int
    total_reports: int


class AdminReportDetail(AdminReport):
    target_context: TargetContext


class ReportPage(BaseModel):
    items: list[AdminReport]
    next_offset: int | None
