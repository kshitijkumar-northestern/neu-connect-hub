import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator

from app.modules.profiles.schemas import MultiLine, NamedItem, SingleLine, Slug
from app.modules.teams.models import (
    CAPACITY_MAX,
    MAX_ROLE_SKILLS,
    MAX_ROLES,
    OPEN_SLOTS_MAX,
    ROLE_TITLE_MAX,
    SUMMARY_MAX,
    TITLE_MAX,
)

ProjectStatus = Literal["open", "full", "closed"]
ApplicationStatus = Literal["pending", "accepted", "rejected", "withdrawn"]

DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def _strip(value: Any) -> Any:
    return value.replace("\r\n", "\n").strip() if isinstance(value, str) else value


def _unique_slugs(values: Sequence[str]) -> None:
    if len(set(values)) != len(values):
        raise ValueError("Each skill can appear only once.")


Title = Annotated[
    str, BeforeValidator(_strip), Field(min_length=1, max_length=TITLE_MAX), SingleLine
]
Summary = Annotated[str, BeforeValidator(_strip), Field(max_length=SUMMARY_MAX), MultiLine]
RoleTitle = Annotated[
    str, BeforeValidator(_strip), Field(min_length=1, max_length=ROLE_TITLE_MAX), SingleLine
]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests -------------------------------------------------------------------------------


class RoleInput(_Request):
    title: RoleTitle
    open_slots: int = Field(ge=1, le=OPEN_SLOTS_MAX)
    skills: list[Slug] = Field(default_factory=list, max_length=MAX_ROLE_SKILLS)

    @field_validator("skills")
    @classmethod
    def _skills_unique(cls, value: list[str]) -> list[str]:
        _unique_slugs(value)
        return value


class RoleUpdate(RoleInput):
    id: uuid.UUID | None = None


class ProjectCreate(_Request):
    title: Title
    summary: Summary = ""
    capacity: int = Field(ge=1, le=CAPACITY_MAX)
    roles: list[RoleInput] = Field(default_factory=list, max_length=MAX_ROLES)


class ProjectUpdate(_Request):
    title: Title | None = None
    summary: Summary | None = None
    capacity: int | None = Field(default=None, ge=1, le=CAPACITY_MAX)
    status: ProjectStatus | None = None
    roles: list[RoleUpdate] | None = Field(default=None, max_length=MAX_ROLES)


class ApplyRequest(_Request):
    role_id: uuid.UUID | None = None


# --- responses --------------------------------------------------------------------------------


class Owner(BaseModel):
    user_id: uuid.UUID
    display_name: str | None


class RoleRef(BaseModel):
    id: uuid.UUID
    title: str


class RoleSummary(BaseModel):
    id: uuid.UUID
    title: str
    open_slots: int
    remaining_slots: int


class RoleDetail(RoleSummary):
    skills: list[NamedItem]


class Member(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    member_role: Literal["owner", "member"]
    role: RoleRef | None
    joined_at: datetime


class ViewerApplication(BaseModel):
    id: uuid.UUID
    status: ApplicationStatus
    role_id: uuid.UUID | None


class ProjectSummary(BaseModel):
    id: uuid.UUID
    title: str
    summary: str
    capacity: int
    status: ProjectStatus
    member_count: int
    owner: Owner
    roles: list[RoleSummary]
    created_at: datetime


class ProjectDetail(BaseModel):
    id: uuid.UUID
    title: str
    summary: str
    capacity: int
    status: ProjectStatus
    member_count: int
    owner: Owner
    roles: list[RoleDetail]
    members: list[Member]
    missing_skills: list[NamedItem]
    conversation_id: uuid.UUID | None
    viewer_application: ViewerApplication | None
    created_at: datetime
    updated_at: datetime


class Applicant(BaseModel):
    user_id: uuid.UUID
    display_name: str | None


class ApplicationOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    applicant: Applicant
    role: RoleRef | None
    status: ApplicationStatus
    created_at: datetime
    decided_at: datetime | None


class ProjectRef(BaseModel):
    id: uuid.UUID
    title: str


class MyApplicationOut(ApplicationOut):
    project: ProjectRef


class ProjectPage(BaseModel):
    items: list[ProjectSummary]
    next_offset: int | None


class ApplicationPage(BaseModel):
    items: list[ApplicationOut]
    next_offset: int | None


class MyApplicationPage(BaseModel):
    items: list[MyApplicationOut]
    next_offset: int | None
