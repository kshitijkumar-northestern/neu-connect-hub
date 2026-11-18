"""Ranked recommendations: eligibility in SQL first, then scoring on the survivors only.

The semantic component reads stored vectors and never calls a model. When the viewer's or a
candidate's vector is missing, stale or from a different model, that pair falls back to interest
Jaccard and records an internal reason code; eligibility is never bypassed.
"""

import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import and_, case, null, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.availability import overlap_with, viewer_week
from app.ml.models import Embedding
from app.ml.scoring import (
    Components,
    Item,
    MatchFeatures,
    Reason,
    WeeklySlot,
    availability_component,
    combine,
    complementary_component,
    goal_component,
    interest_component,
    reasons_for,
    semantic_component,
)
from app.modules.auth.models import User
from app.modules.discovery.eligibility import candidate_filter, viewer_eligible
from app.modules.profiles.models import (
    AvailabilitySlot,
    Interest,
    Profile,
    ProfileInterest,
    ProfileSkill,
    Skill,
)

logger = logging.getLogger("app.ml")

# Results per request, and how many eligible candidates are scored to find them (the closest by
# embedding first, when the viewer has one).
MAX_RECOMMENDATIONS: Final = 5
CANDIDATE_POOL: Final = 500

FallbackReason = Literal[
    "viewer_embedding_missing", "candidate_embedding_missing", "model_mismatch"
]


class ViewerNotEligible(Exception):
    """The viewer can't receive recommendations (not verified, suspended, an admin, or not opted
    in to discovery)."""


@dataclass(frozen=True)
class Recommendation:
    """One ranked candidate. `score`, `components` and `fallback` are internal: never shown to
    students, who see only the reasons."""

    user_id: uuid.UUID
    score: float
    components: Components
    reasons: tuple[Reason, ...]
    fallback: FallbackReason | None


@dataclass(frozen=True)
class _PoolRow:
    user_id: uuid.UUID
    similarity: float | None
    candidate_stale: bool | None
    candidate_model_version: str | None


class Ranker:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recommend(
        self,
        viewer_id: uuid.UUID,
        *,
        limit: int = MAX_RECOMMENDATIONS,
        needed_skills: Collection[str] | None = None,
        exclude: Collection[uuid.UUID] = (),
        now: datetime | None = None,
    ) -> list[Recommendation]:
        """Up to `limit` eligible candidates with at least one reason, best first (ties broken by
        user id). `needed_skills` (skill slugs) overrides the viewer's seeking skills, e.g. with a
        project's missing role skills; `exclude` drops extra user ids (e.g. a project's
        members). Raises `ViewerNotEligible`."""
        if not 1 <= limit <= MAX_RECOMMENDATIONS:
            raise ValueError(f"limit must be between 1 and {MAX_RECOMMENDATIONS}")
        if not await viewer_eligible(self._session, viewer_id):
            raise ViewerNotEligible()
        now = now or datetime.now(UTC)

        viewer_vector, viewer_model_version = await self._viewer_vector(viewer_id)
        pool = await self._pool(viewer_id, viewer_vector, viewer_model_version, exclude)

        all_ids = [viewer_id, *(row.user_id for row in pool)]
        features = await self._load_features(all_ids)
        viewer_features = features.get(viewer_id)
        if viewer_features is None:
            # The viewer passed `viewer_eligible` a moment ago but their profile vanished (or
            # never had any rows loaded); nothing to score against.
            return []

        needed = await self._needed_skills(needed_skills, viewer_features)
        # The viewer's week is laid out once; each candidate is then only intersected with it.
        week = viewer_week(viewer_features.availability, viewer_features.timezone, now=now)

        fallback_counts: Counter[str] = Counter()
        scored: list[Recommendation] = []
        for row in pool:
            candidate = features.get(row.user_id)
            if candidate is None:
                continue
            overlap = overlap_with(week, candidate.availability, candidate.timezone)
            semantic, fallback = self._semantic(
                row, viewer_vector, viewer_model_version, viewer_features, candidate
            )
            if fallback is not None:
                fallback_counts[fallback] += 1
            components = Components(
                semantic=semantic,
                complementary=complementary_component(needed, candidate.has_skills),
                availability=availability_component(overlap),
                goal=goal_component(viewer_features.goals, candidate.goals),
            )
            reasons = reasons_for(viewer_features, candidate, needed, overlap)
            if not reasons:
                continue
            scored.append(
                Recommendation(
                    user_id=row.user_id,
                    score=combine(components),
                    components=components,
                    reasons=reasons,
                    fallback=fallback,
                )
            )

        scored.sort(key=lambda rec: (-rec.score, rec.user_id))
        results = scored[:limit]
        logger.info(
            "Computed discovery recommendations",
            extra={
                "pool_size": len(pool),
                "returned": len(results),
                "fallbacks": dict(fallback_counts),
            },
        )
        return results

    # --- per-candidate scoring ---------------------------------------------------------------

    def _semantic(
        self,
        row: _PoolRow,
        viewer_vector: list[float] | None,
        viewer_model_version: str | None,
        viewer_features: MatchFeatures,
        candidate: MatchFeatures,
    ) -> tuple[float, FallbackReason | None]:
        if viewer_vector is None:
            return interest_component(viewer_features.interests, candidate.interests), (
                "viewer_embedding_missing"
            )
        if row.similarity is not None:
            return semantic_component(row.similarity), None
        # Not comparable despite the viewer having a vector: a fresh candidate row under a
        # different model version is a mismatch; anything else (no row, or stale) is missing.
        fallback: FallbackReason = (
            "model_mismatch"
            if row.candidate_stale is False and row.candidate_model_version != viewer_model_version
            else "candidate_embedding_missing"
        )
        return interest_component(viewer_features.interests, candidate.interests), fallback

    # --- loading ------------------------------------------------------------------------------

    async def _viewer_vector(self, viewer_id: uuid.UUID) -> tuple[list[float] | None, str | None]:
        row = (
            await self._session.execute(
                select(Embedding.embedding, Embedding.model_version).where(
                    Embedding.user_id == viewer_id, ~Embedding.stale
                )
            )
        ).first()
        if row is None or row.embedding is None:
            return None, None
        return list(row.embedding), row.model_version

    async def _pool(
        self,
        viewer_id: uuid.UUID,
        viewer_vector: list[float] | None,
        viewer_model_version: str | None,
        exclude: Collection[uuid.UUID],
    ) -> list[_PoolRow]:
        """Eligible candidates, closest by embedding first when the viewer has a vector
        (ordering candidates with no comparable vector last), else by user id."""
        base = (
            select(
                Profile.user_id,
                Embedding.stale.label("candidate_stale"),
                Embedding.model_version.label("candidate_model_version"),
            )
            .select_from(Profile)
            .join(User, User.id == Profile.user_id)
            .outerjoin(Embedding, Embedding.user_id == Profile.user_id)
            .where(candidate_filter(viewer_id))
        )
        if exclude:
            base = base.where(Profile.user_id.not_in(exclude))

        if viewer_vector is not None:
            distance = Embedding.embedding.cosine_distance(viewer_vector)
            comparable = and_(~Embedding.stale, Embedding.model_version == viewer_model_version)
            query = (
                base.add_columns(case((comparable, 1 - distance), else_=null()).label("similarity"))
                .order_by(
                    case((comparable, distance), else_=null()).asc().nulls_last(),
                    Profile.user_id.asc(),
                )
                .limit(CANDIDATE_POOL)
            )
        else:
            query = (
                base.add_columns(null().label("similarity"))
                .order_by(Profile.user_id.asc())
                .limit(CANDIDATE_POOL)
            )

        rows = await self._session.execute(query)
        return [
            _PoolRow(
                user_id=r.user_id,
                similarity=r.similarity,
                candidate_stale=r.candidate_stale,
                candidate_model_version=r.candidate_model_version,
            )
            for r in rows
        ]

    async def _load_features(
        self, user_ids: Collection[uuid.UUID]
    ) -> dict[uuid.UUID, MatchFeatures]:
        """Batch-loads everything scoring may read, one query per relation regardless of how
        many user ids are asked for."""
        ids = list(dict.fromkeys(user_ids))  # de-duplicate (viewer may also be a pool row's id)
        if not ids:
            return {}

        base = await self._session.execute(
            select(Profile.user_id, Profile.timezone, Profile.goals).where(Profile.user_id.in_(ids))
        )
        base_by_id = {user_id: (timezone, goals) for user_id, timezone, goals in base}

        has: dict[uuid.UUID, set[Item]] = defaultdict(set)
        seeking: dict[uuid.UUID, set[Item]] = defaultdict(set)
        skill_rows = await self._session.execute(
            select(ProfileSkill.user_id, ProfileSkill.kind, Skill.slug, Skill.name)
            .join(Skill, Skill.id == ProfileSkill.skill_id)
            .where(ProfileSkill.user_id.in_(ids))
        )
        for user_id, kind, slug, name in skill_rows:
            (has if kind == "has" else seeking)[user_id].add(Item(slug=slug, name=name))

        interests: dict[uuid.UUID, set[Item]] = defaultdict(set)
        interest_rows = await self._session.execute(
            select(ProfileInterest.user_id, Interest.slug, Interest.name)
            .join(Interest, Interest.id == ProfileInterest.interest_id)
            .where(ProfileInterest.user_id.in_(ids))
        )
        for user_id, slug, name in interest_rows:
            interests[user_id].add(Item(slug=slug, name=name))

        slots: dict[uuid.UUID, list[WeeklySlot]] = defaultdict(list)
        slot_rows = await self._session.execute(
            select(AvailabilitySlot).where(AvailabilitySlot.user_id.in_(ids))
        )
        for slot in slot_rows.scalars():
            slots[slot.user_id].append(
                WeeklySlot(weekday=slot.weekday, start_min=slot.start_min, end_min=slot.end_min)
            )

        return {
            user_id: MatchFeatures(
                user_id=user_id,
                timezone=timezone,
                goals=frozenset(goals),
                has_skills=frozenset(has.get(user_id, ())),
                seeking_skills=frozenset(seeking.get(user_id, ())),
                interests=frozenset(interests.get(user_id, ())),
                availability=tuple(slots.get(user_id, ())),
            )
            for user_id, (timezone, goals) in base_by_id.items()
        }

    async def _needed_skills(
        self, needed_skills: Collection[str] | None, viewer: MatchFeatures
    ) -> frozenset[Item]:
        if needed_skills is None:
            return viewer.seeking_skills
        if not needed_skills:
            return frozenset()
        rows = await self._session.execute(
            select(Skill.slug, Skill.name).where(Skill.slug.in_(needed_skills))
        )
        return frozenset(Item(slug=slug, name=name) for slug, name in rows)
