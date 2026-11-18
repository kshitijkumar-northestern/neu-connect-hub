"""Pure match scoring: four components, each in [0, 1], combined with fixed weights, and the
reasons a viewer is shown for a match.

Reads only public profile fields (`MatchFeatures`): no database, no model and no clock of its own
(callers pass `now`). Reasons are built only from what the candidate shows on their public
profile: interests, skills, availability and goals. Never a score, percentage or probability.
"""

import math
import uuid
from collections.abc import Set
from dataclasses import dataclass
from typing import Final, Literal

from app.modules.profiles.models import GOALS


@dataclass(frozen=True)
class Weights:
    semantic: float
    complementary: float
    availability: float
    goal: float


# Hypotheses, not measurements: the evaluation entrypoint is where they get tested. They sum to 1,
# so with every component in [0, 1] every score is too.
WEIGHTS: Final = Weights(semantic=0.35, complementary=0.30, availability=0.25, goal=0.10)

# Shared minutes per week at which the availability component reaches 1: enough for two two-hour
# sessions. Also a hypothesis.
AVAILABILITY_TARGET_MINUTES: Final = 240


@dataclass(frozen=True, order=True)
class Item:
    """A skill or an interest as a profile shows it. Compared by slug; the name is for display."""

    slug: str
    name: str


@dataclass(frozen=True)
class WeeklySlot:
    """A weekly range in its owner's timezone: weekday 0 is Monday, minutes from local midnight,
    end_min up to 1440."""

    weekday: int
    start_min: int
    end_min: int


@dataclass(frozen=True)
class MatchFeatures:
    """Everything scoring may read about one student: public profile fields only. Never the
    display name, email, bio text, messages or anything private."""

    user_id: uuid.UUID
    timezone: str
    goals: frozenset[str]
    has_skills: frozenset[Item]
    seeking_skills: frozenset[Item]
    interests: frozenset[Item]
    availability: tuple[WeeklySlot, ...]


@dataclass(frozen=True)
class OverlapWindow:
    """The longest shared stretch in the coming week, in the viewer's local time: weekday 0 is
    Monday. Clipped at the viewer's local midnight, so end_min is at most 1440."""

    weekday: int
    start_min: int
    end_min: int


@dataclass(frozen=True)
class Overlap:
    """How much of the coming week two students are both available."""

    minutes: int
    longest: OverlapWindow | None


@dataclass(frozen=True)
class SkillsCovered:
    """Needed skills the candidate has, sorted by name."""

    items: tuple[Item, ...]
    kind: Literal["skills_covered"] = "skills_covered"


@dataclass(frozen=True)
class SharedInterests:
    """Interests both students listed, sorted by name."""

    items: tuple[Item, ...]
    kind: Literal["shared_interests"] = "shared_interests"


@dataclass(frozen=True)
class AvailabilityOverlap:
    window: OverlapWindow
    kind: Literal["availability_overlap"] = "availability_overlap"


@dataclass(frozen=True)
class SharedGoals:
    """Goal slugs both students chose, in `GOALS` order."""

    goals: tuple[str, ...]
    kind: Literal["shared_goals"] = "shared_goals"


Reason = SkillsCovered | SharedInterests | AvailabilityOverlap | SharedGoals

# Which public profile field each reason is drawn from: a reason never draws on anything else.
REASON_FIELDS: Final[dict[str, str]] = {
    "skills_covered": "skills",
    "shared_interests": "interests",
    "availability_overlap": "availability",
    "shared_goals": "goals",
}


@dataclass(frozen=True)
class Components:
    semantic: float
    complementary: float
    availability: float
    goal: float


def semantic_component(cosine_similarity: float) -> float:
    """Cosine similarity clamped to [0, 1]: an unrelated or opposed profile earns nothing, and a
    non-finite input counts as 0."""
    if not math.isfinite(cosine_similarity):
        return 0.0
    return min(1.0, max(0.0, cosine_similarity))


def jaccard(a: Set[object], b: Set[object]) -> float:
    """Size of the intersection over size of the union, and 0 when both are empty (no shared
    signal is no evidence)."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def complementary_component(needed: frozenset[Item], candidate_has: frozenset[Item]) -> float:
    """The share of the viewer's needed skills the candidate has (by slug); 0 when nothing is
    needed."""
    if not needed:
        return 0.0
    needed_slugs = {item.slug for item in needed}
    has_slugs = {item.slug for item in candidate_has}
    return len(needed_slugs & has_slugs) / len(needed_slugs)


def goal_component(viewer_goals: frozenset[str], candidate_goals: frozenset[str]) -> float:
    """Jaccard similarity of the two goal sets."""
    return jaccard(viewer_goals, candidate_goals)


def interest_component(viewer: frozenset[Item], candidate: frozenset[Item]) -> float:
    """Jaccard similarity of the two interest sets, by slug. The deterministic stand-in for the
    semantic component when an embedding is missing."""
    return jaccard({item.slug for item in viewer}, {item.slug for item in candidate})


def availability_component(overlap: Overlap) -> float:
    """Shared minutes over `AVAILABILITY_TARGET_MINUTES`, capped at 1."""
    return min(1.0, overlap.minutes / AVAILABILITY_TARGET_MINUTES)


def combine(components: Components, weights: Weights = WEIGHTS) -> float:
    """The weighted sum, in [0, 1]."""
    total = (
        components.semantic * weights.semantic
        + components.complementary * weights.complementary
        + components.availability * weights.availability
        + components.goal * weights.goal
    )
    return min(1.0, max(0.0, total))


def reasons_for(
    viewer: MatchFeatures, candidate: MatchFeatures, needed: frozenset[Item], overlap: Overlap
) -> tuple[Reason, ...]:
    """Why this candidate, from fields the candidate shows publicly, in a fixed order: skills
    covered, shared interests, availability overlap, shared goals. Kinds with nothing to say are
    left out, so an empty tuple means the match can't be justified."""
    reasons: list[Reason] = []

    needed_slugs = {item.slug for item in needed}
    covered = tuple(
        sorted(
            (item for item in candidate.has_skills if item.slug in needed_slugs),
            key=lambda item: item.name.casefold(),
        )
    )
    if covered:
        reasons.append(SkillsCovered(items=covered))

    shared_interest_slugs = {item.slug for item in viewer.interests} & {
        item.slug for item in candidate.interests
    }
    shared_interests = tuple(
        sorted(
            (item for item in candidate.interests if item.slug in shared_interest_slugs),
            key=lambda item: item.name.casefold(),
        )
    )
    if shared_interests:
        reasons.append(SharedInterests(items=shared_interests))

    if overlap.longest is not None and overlap.minutes > 0:
        reasons.append(AvailabilityOverlap(window=overlap.longest))

    shared_goals = tuple(goal for goal in GOALS if goal in viewer.goals and goal in candidate.goals)
    if shared_goals:
        reasons.append(SharedGoals(goals=shared_goals))

    return tuple(reasons)


@dataclass(frozen=True)
class PairScore:
    score: float
    components: Components
    reasons: tuple[Reason, ...]


def score_pair(
    viewer: MatchFeatures,
    candidate: MatchFeatures,
    needed: frozenset[Item],
    overlap: Overlap,
    semantic: float,
) -> PairScore:
    """Scores one pair from an already-decided semantic component (embedding similarity, or the
    interest fallback). The ranker and the offline evaluation both score through here, so what
    is evaluated is exactly what students are shown."""
    components = Components(
        semantic=semantic,
        complementary=complementary_component(needed, candidate.has_skills),
        availability=availability_component(overlap),
        goal=goal_component(viewer.goals, candidate.goals),
    )
    return PairScore(
        score=combine(components),
        components=components,
        reasons=reasons_for(viewer, candidate, needed, overlap),
    )
