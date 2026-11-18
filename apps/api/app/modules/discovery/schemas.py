"""Request and response shapes for discovery and match feedback.

`ReasonOut` mirrors `apps/web/src/features/discovery/api.ts` field for field, discriminated on
`kind` the same way the web client narrows on it. Never a score, a component or a fallback code:
students see only what a candidate shows publicly.
"""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.profiles.schemas import Goal, NamedItem

# Mirrors FEEDBACK_ACTIONS in app/modules/discovery/models.py (a request-schema Literal can't be
# built from that tuple at the type level, so the two are kept in step by hand, as
# teams/schemas.py already does for ProjectStatus against PROJECT_STATUSES).
FeedbackAction = Literal["connected", "dismissed"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OverlapWindowOut(BaseModel):
    """The longest shared stretch in the coming week, already in the viewer's timezone."""

    weekday: int
    start_min: int
    end_min: int


class SkillsCoveredOut(BaseModel):
    kind: Literal["skills_covered"] = "skills_covered"
    items: list[NamedItem]


class SharedInterestsOut(BaseModel):
    kind: Literal["shared_interests"] = "shared_interests"
    items: list[NamedItem]


class AvailabilityOverlapOut(BaseModel):
    kind: Literal["availability_overlap"] = "availability_overlap"
    window: OverlapWindowOut


class SharedGoalsOut(BaseModel):
    kind: Literal["shared_goals"] = "shared_goals"
    goals: list[Goal]


ReasonOut = Annotated[
    SkillsCoveredOut | SharedInterestsOut | AvailabilityOverlapOut | SharedGoalsOut,
    Field(discriminator="kind"),
]


class MatchOut(BaseModel):
    user_id: uuid.UUID
    display_name: str
    headline: str
    # At least one, in the fixed order reasons_for produces: skills, interests, availability,
    # goals.
    reasons: list[ReasonOut]


class DiscoveryOut(BaseModel):
    """At most MAX_RECOMMENDATIONS matches, best first. Empty when nobody suitable is around."""

    matches: list[MatchOut]


class FeedbackRequest(_Request):
    candidate_id: uuid.UUID
    action: FeedbackAction
