import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ConnectionState = Literal["none", "pending_outgoing", "pending_incoming", "connected"]


class ConnectionCode(BaseModel):
    """The caller's own Scan to Connect code, as shown on the share page. `url` is set only
    while active; `expires_at` is set while active or expired; `shareable` warns when a scan
    would 404."""

    status: Literal["active", "expired", "off"]
    url: str | None
    expires_at: datetime | None
    shareable: bool


class ConnectPreview(BaseModel):
    """What a scanner sees. Never more than these four fields."""

    user_id: uuid.UUID
    display_name: str
    headline: str
    connection_state: ConnectionState


class ResolveRequest(BaseModel):
    """The scanned token, carried in the body so it never appears in a URL that a proxy, load
    balancer or tracing layer in front of the API could log. `token` is deliberately untyped: a
    missing or wrongly-typed value must fail the same generic 404 as a malformed one, not a 422."""

    model_config = ConfigDict(extra="forbid")

    token: Any = None
