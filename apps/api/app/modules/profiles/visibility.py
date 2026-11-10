"""Who may see whom. Every feature that shows one student to another filters through here, so
blocks, connections and opt-in discovery apply everywhere in the same way."""

import uuid

from sqlalchemy import ColumnElement, and_, exists, or_
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.auth.models import User
from app.modules.connections.models import Connection
from app.modules.profiles.models import Block, Profile

AccountId = uuid.UUID | ColumnElement[uuid.UUID] | InstrumentedAttribute[uuid.UUID]


def blocked_between(a: AccountId, b: AccountId) -> ColumnElement[bool]:
    """True when either student has blocked the other."""
    return exists().where(
        or_(
            and_(Block.blocker_id == a, Block.blocked_id == b),
            and_(Block.blocker_id == b, Block.blocked_id == a),
        )
    )


def connected(a: AccountId, b: AccountId) -> ColumnElement[bool]:
    """True when the two students have an accepted connection (a pending request isn't enough)."""
    return exists().where(
        Connection.state == "accepted",
        or_(
            and_(Connection.sender_id == a, Connection.recipient_id == b),
            and_(Connection.sender_id == b, Connection.recipient_id == a),
        ),
    )


def visible_to(viewer_id: uuid.UUID) -> ColumnElement[bool]:
    """Profiles the viewer may see: a verified, active account that opted in to discovery, is
    connected to the viewer, or is the viewer's own, with no block either way. Use on a query
    joining Profile to User."""
    return and_(
        User.verified_at.is_not(None),
        User.suspended_at.is_(None),
        or_(
            Profile.discoverable,
            Profile.user_id == viewer_id,
            connected(Profile.user_id, viewer_id),
        ),
        ~blocked_between(Profile.user_id, viewer_id),
    )
