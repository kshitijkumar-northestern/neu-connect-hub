"""check admin audit log foreign keys at commit

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-17 19:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# One audit row can point at an admin, a student and a report about that student. Deleting
# several of them in one transaction nulls those ids through separate cascades, and an immediate
# check could fire between two of them, so the keys are checked once, at commit.
CONSTRAINTS = (
    "fk_admin_audit_log_admin_id_users",
    "fk_admin_audit_log_target_user_id_users",
    "fk_admin_audit_log_report_id_reports",
)


def upgrade() -> None:
    for name in CONSTRAINTS:
        op.execute(
            f"ALTER TABLE admin_audit_log ALTER CONSTRAINT {name} DEFERRABLE INITIALLY DEFERRED"
        )


def downgrade() -> None:
    for name in CONSTRAINTS:
        op.execute(f"ALTER TABLE admin_audit_log ALTER CONSTRAINT {name} NOT DEFERRABLE")
