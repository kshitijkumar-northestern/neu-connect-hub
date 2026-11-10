import itertools
import unicodedata
import uuid
import zoneinfo
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from app.modules.profiles.models import (
    BIO_MAX,
    DISPLAY_NAME_MAX,
    HEADLINE_MAX,
    MINUTES_PER_DAY,
    REPORT_DETAILS_MAX,
)

Goal = Literal["studying", "project_development", "research", "mentorship"]
Level = Literal["beginner", "intermediate", "advanced"]
ReportReason = Literal["spam", "harassment", "inappropriate_content", "impersonation", "other"]

MAX_SKILLS = 20
MAX_INTERESTS = 15
MAX_SLOTS = 42  # six ranges a day

# Every IANA zone this machine knows (system database or the tzdata package), minus the
# placeholder entries that aren't real places.
_TIMEZONES = frozenset(zoneinfo.available_timezones()) - {"Factory", "localtime", "posixrules"}


def _strip(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n").strip()
    return value


def _no_control_characters(allowed: str) -> Any:
    def check(value: str) -> str:
        if any(unicodedata.category(c).startswith("C") and c not in allowed for c in value):
            raise ValueError("Remove line breaks and other control characters.")
        return value

    return AfterValidator(check)


def _known_timezone(value: str) -> str:
    if value not in _TIMEZONES:
        raise ValueError("Use an IANA time zone such as America/New_York.")
    return value


def _unique(values: Sequence[Any], what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"Each {what} can appear only once.")


# Zero-width (non-)joiners are format characters that Persian, Indic and emoji spellings need;
# every other control or format character (including bidi overrides that disguise text) is refused.
JOINERS = "\u200c\u200d"
SingleLine = _no_control_characters(allowed=JOINERS)
MultiLine = _no_control_characters(allowed=JOINERS + "\n\t")
DisplayName = Annotated[
    str, BeforeValidator(_strip), Field(min_length=1, max_length=DISPLAY_NAME_MAX), SingleLine
]
Headline = Annotated[str, BeforeValidator(_strip), Field(max_length=HEADLINE_MAX), SingleLine]
Bio = Annotated[str, BeforeValidator(_strip), Field(max_length=BIO_MAX), MultiLine]
Timezone = Annotated[str, Field(max_length=64), AfterValidator(_known_timezone)]
Slug = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillLevel(_Request):
    slug: Slug
    level: Level


class Slot(_Request):
    """A weekly range in the profile's time zone: weekday 0 is Monday, minutes from midnight."""

    weekday: int = Field(ge=0, le=6)
    start_min: int = Field(ge=0, lt=MINUTES_PER_DAY)
    end_min: int = Field(gt=0, le=MINUTES_PER_DAY)

    @model_validator(mode="after")
    def _ordered(self) -> "Slot":
        if self.start_min >= self.end_min:
            raise ValueError("A time range must end after it starts.")
        return self


class ProfileInput(_Request):
    """The whole profile; saving replaces every field. Discovery stays off unless set."""

    display_name: DisplayName
    headline: Headline = ""
    bio: Bio = ""
    goals: list[Goal] = Field(default_factory=list, max_length=4)
    timezone: Timezone
    skills: list[SkillLevel] = Field(default_factory=list, max_length=MAX_SKILLS)
    seeking_skills: list[Slug] = Field(default_factory=list, max_length=MAX_SKILLS)
    interests: list[Slug] = Field(default_factory=list, max_length=MAX_INTERESTS)
    availability: list[Slot] = Field(default_factory=list, max_length=MAX_SLOTS)
    discoverable: StrictBool = False
    feedback_consent: StrictBool = False

    @field_validator("goals")
    @classmethod
    def _unique_goals(cls, value: list[str]) -> list[str]:
        _unique(value, "goal")
        return value

    @field_validator("skills")
    @classmethod
    def _unique_skills(cls, value: list[SkillLevel]) -> list[SkillLevel]:
        _unique([skill.slug for skill in value], "skill")
        return value

    @field_validator("seeking_skills", "interests")
    @classmethod
    def _unique_slugs(cls, value: list[str]) -> list[str]:
        _unique(value, "item")
        return value

    @field_validator("availability")
    @classmethod
    def _no_overlaps(cls, value: list[Slot]) -> list[Slot]:
        ordered = sorted(value, key=lambda slot: (slot.weekday, slot.start_min))
        for earlier, later in itertools.pairwise(ordered):
            if earlier.weekday == later.weekday and earlier.end_min > later.start_min:
                raise ValueError("Time ranges on the same day can't overlap.")
        return value


# --- responses ------------------------------------------------------------------------------


class SkillItem(BaseModel):
    slug: str
    name: str
    category: str


class InterestItem(BaseModel):
    slug: str
    name: str


class NamedItem(BaseModel):
    slug: str
    name: str


class LeveledSkill(NamedItem):
    level: Level


class SlotOut(BaseModel):
    weekday: int
    start_min: int
    end_min: int


class PublicProfile(BaseModel):
    """What other verified students may see. Never the email address or the consent flags."""

    user_id: uuid.UUID
    display_name: str
    headline: str
    bio: str
    goals: list[Goal]
    timezone: str
    skills: list[LeveledSkill]
    seeking_skills: list[NamedItem]
    interests: list[NamedItem]
    availability: list[SlotOut]


class OwnProfile(PublicProfile):
    discoverable: bool
    feedback_consent: bool
    updated_at: datetime


# --- trust ----------------------------------------------------------------------------------


class BlockRequest(_Request):
    user_id: uuid.UUID


class BlockedUser(BaseModel):
    user_id: uuid.UUID
    display_name: str | None  # None when the blocked student has no profile
    blocked_at: datetime


ReportDetails = Annotated[
    str, BeforeValidator(_strip), Field(max_length=REPORT_DETAILS_MAX), MultiLine
]


class ReportRequest(_Request):
    user_id: uuid.UUID
    reason: ReportReason
    details: ReportDetails = ""
