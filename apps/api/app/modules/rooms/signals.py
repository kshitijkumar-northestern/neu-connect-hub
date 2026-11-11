"""Payloads for the room and WebRTC signaling socket events. SDP and ICE candidates pass through
untouched: nothing here strips, normalises or logs them."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SDP_MAX = 20_000
CANDIDATE_MAX = 2_000
SDP_MID_MAX = 64
USERNAME_FRAGMENT_MAX = 256
SDP_M_LINE_INDEX_MAX = 1_000


class RoomPayload(BaseModel):
    """`room.join` and `room.leave` share this shape."""

    model_config = ConfigDict(extra="forbid")

    room_id: uuid.UUID


class SdpSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["offer", "answer"]
    sdp: str = Field(min_length=1, max_length=SDP_MAX)


class IceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: str = Field(min_length=0, max_length=CANDIDATE_MAX)
    sdpMid: str | None = Field(max_length=SDP_MID_MAX)
    sdpMLineIndex: int | None = Field(ge=0, le=SDP_M_LINE_INDEX_MAX)
    usernameFragment: str | None = Field(default=None, max_length=USERNAME_FRAGMENT_MAX)


class CandidateSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["candidate"]
    candidate: IceCandidate


RtcSignal = Annotated[SdpSignal | CandidateSignal, Field(discriminator="type")]


class RtcSignalPayload(BaseModel):
    """The payload of an `rtc.signal` socket event."""

    model_config = ConfigDict(extra="forbid")

    room_id: uuid.UUID
    to: uuid.UUID
    signal: RtcSignal
