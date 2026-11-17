import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.admin.models import AdminAuditLog


def record(
    db: AsyncSession,
    action: str,
    *,
    admin_id: uuid.UUID | None,
    target_user_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
) -> None:
    """Add an audit row to the caller's transaction, so the action and its record commit (or
    roll back) together. `admin_id` is None for the command line."""
    db.add(
        AdminAuditLog(
            admin_id=admin_id, action=action, target_user_id=target_user_id, report_id=report_id
        )
    )
