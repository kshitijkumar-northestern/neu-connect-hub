"""Import every model module here so Alembic autogenerate sees the full metadata."""

from app.db.base import Base
from app.modules.auth import models as auth_models
from app.modules.profiles import models as profile_models

__all__ = ["Base", "auth_models", "profile_models"]
