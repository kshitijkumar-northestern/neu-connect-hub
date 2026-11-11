"""Import every model module here so Alembic autogenerate sees the full metadata."""

from app.db.base import Base
from app.modules.auth import models as auth_models
from app.modules.chat import models as chat_models
from app.modules.connections import models as connection_models
from app.modules.profiles import models as profile_models
from app.modules.rooms import models as room_models
from app.modules.teams import models as team_models

__all__ = [
    "Base",
    "auth_models",
    "chat_models",
    "connection_models",
    "profile_models",
    "room_models",
    "team_models",
]
