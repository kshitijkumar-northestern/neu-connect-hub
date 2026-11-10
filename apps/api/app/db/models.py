"""Import every model module here so Alembic autogenerate sees the full metadata.

No models exist yet; feature modules add them.
"""

from app.db.base import Base

__all__ = ["Base"]
