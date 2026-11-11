"""Short-lived TURN credentials, coturn's "use REST API" scheme: a username carrying an
expiry and the caller's id, and a credential that's the base64 of an HMAC-SHA1 of that
username keyed by the shared secret. A pure function of its inputs, so it's unit-testable
without a server or a clock."""

import base64
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha1


@dataclass(frozen=True)
class TurnCredential:
    username: str
    credential: str
    expires_at: datetime


def generate_turn_credential(
    secret: str, user_id: uuid.UUID, ttl_seconds: int, now: datetime
) -> TurnCredential:
    expires_at = now + timedelta(seconds=ttl_seconds)
    username = f"{int(expires_at.timestamp())}:{user_id}"
    # HMAC-SHA1 is coturn's mandated REST-API scheme, not a choice made here.
    digest = hmac.new(secret.encode(), username.encode(), sha1).digest()
    credential = base64.b64encode(digest).decode()
    return TurnCredential(username=username, credential=credential, expires_at=expires_at)
