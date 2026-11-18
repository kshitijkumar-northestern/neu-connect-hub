"""order project roles by insert time

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-18 10:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# now() is the transaction's start time, so every role saved in one request got the same
# created_at and the page fell back to the roles' random ids. clock_timestamp() advances between
# the inserts, keeping roles in the order they were entered.
def upgrade() -> None:
    op.alter_column("project_roles", "created_at", server_default=sa.text("clock_timestamp()"))


def downgrade() -> None:
    op.alter_column("project_roles", "created_at", server_default=sa.text("now()"))
