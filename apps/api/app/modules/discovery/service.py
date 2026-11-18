"""The discovery feed and consented match feedback.

Eligibility and scoring live in `Ranker`; this service wires the viewer's request to it, applies
the project override, re-checks visibility on the way out (a candidate the ranker scored may have
gone invisible by the time the card query runs), and records feedback only when the viewer has
consented and the candidate is still visible to them.
"""

import math
import uuid
from collections.abc import Collection
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter
from app.ml.scoring import (
    AvailabilityOverlap,
    Item,
    Reason,
    SharedGoals,
    SharedInterests,
    SkillsCovered,
)
from app.modules.auth.models import User
from app.modules.discovery.models import MatchFeedback
from app.modules.discovery.ranker import Ranker, Recommendation, ViewerNotEligible
from app.modules.discovery.schemas import (
    AvailabilityOverlapOut,
    DiscoveryOut,
    FeedbackRequest,
    MatchOut,
    OverlapWindowOut,
    ReasonOut,
    SharedGoalsOut,
    SharedInterestsOut,
    SkillsCoveredOut,
)
from app.modules.profiles.models import Profile, ProfileSkill, Skill
from app.modules.profiles.schemas import Goal, NamedItem
from app.modules.profiles.visibility import visible_to
from app.modules.teams.models import (
    Application,
    Project,
    ProjectMember,
    ProjectRole,
    ProjectSkill,
)

DISCOVERY_PER_USER = Limit(max_events=30, window_seconds=60)

OPT_IN_REQUIRED = "Turn on discovery in your profile to see suggestions."
STUDENTS_ONLY = "Discovery is for student accounts."
PROJECT_NOT_FOUND = "Project not found."
RATE_LIMITED = "Too many requests. Please wait and try again."


def _named(items: tuple[Item, ...]) -> list[NamedItem]:
    return [NamedItem(slug=item.slug, name=item.name) for item in items]


def _reason_out(reason: Reason) -> ReasonOut:
    if isinstance(reason, SkillsCovered):
        return SkillsCoveredOut(items=_named(reason.items))
    if isinstance(reason, SharedInterests):
        return SharedInterestsOut(items=_named(reason.items))
    if isinstance(reason, AvailabilityOverlap):
        window = reason.window
        return AvailabilityOverlapOut(
            window=OverlapWindowOut(
                weekday=window.weekday, start_min=window.start_min, end_min=window.end_min
            )
        )
    if isinstance(reason, SharedGoals):
        return SharedGoalsOut(goals=cast(list[Goal], list(reason.goals)))
    raise AssertionError(f"unhandled reason kind: {reason!r}")


class DiscoveryService:
    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db = db
        self.limiter = limiter

    async def matches(self, viewer_id: uuid.UUID, project_id: uuid.UUID | None) -> DiscoveryOut:
        self._limit(viewer_id)
        await self._require_student(viewer_id)
        needed_skills: Collection[str] | None = None
        exclude: Collection[uuid.UUID] = ()
        if project_id is not None:
            needed_skills, exclude = await self._project_override(viewer_id, project_id)
        try:
            recommendations = await Ranker(self.db).recommend(
                viewer_id, needed_skills=needed_skills, exclude=exclude
            )
        except ViewerNotEligible as exc:
            raise AppError(403, "discovery_opt_in_required", OPT_IN_REQUIRED) from exc
        return DiscoveryOut(matches=await self._cards(viewer_id, recommendations))

    async def feedback(self, viewer_id: uuid.UUID, body: FeedbackRequest) -> None:
        """Always succeeds from the caller's view; the row is written only when consented,
        discoverable, and the candidate is still visible and isn't the viewer."""
        self._limit(viewer_id)
        if not await self._may_record(viewer_id, body.candidate_id):
            return
        try:
            await self.db.execute(
                pg_insert(MatchFeedback)
                .values(viewer_id=viewer_id, candidate_id=body.candidate_id, action=body.action)
                .on_conflict_do_nothing(
                    index_elements=[
                        MatchFeedback.viewer_id,
                        MatchFeedback.candidate_id,
                        MatchFeedback.action,
                    ]
                )
            )
            await self.db.commit()
        except IntegrityError:
            # Either account could have been deleted between the visibility check above and
            # this insert (a ForeignKeyViolation): answer the same 204 as a consent-less call
            # rather than 500, and store nothing.
            await self.db.rollback()

    # --- helpers ----------------------------------------------------------------------------

    async def _require_student(self, viewer_id: uuid.UUID) -> None:
        """An admin can have a discoverable profile (seeded data, a dual role) but still can't
        act on the opt-in prompt, so this gets its own code rather than
        discovery_opt_in_required."""
        role = await self.db.scalar(select(User.role).where(User.id == viewer_id))
        if role != "student":
            raise AppError(403, "discovery_students_only", STUDENTS_ONLY)

    async def _may_record(self, viewer_id: uuid.UUID, candidate_id: uuid.UUID) -> bool:
        if candidate_id == viewer_id:
            return False
        # FOR SHARE: ProfileService.save() upserts the profile row first and holds that row's
        # lock until it commits. Without this lock, a plain read here could see consent that a
        # concurrent save is in the middle of withdrawing (and whose purge already ran, before
        # its own commit), so we'd insert a row its DELETE can never see. Taking the lock makes
        # us wait for that save to finish and then re-read the consent it actually left behind;
        # conversely, a feedback write already in flight makes a save's upsert wait for us, so
        # its purge runs after our commit and still catches the row we just wrote.
        consents = await self.db.scalar(
            select(Profile.user_id)
            .where(
                Profile.user_id == viewer_id,
                Profile.feedback_consent,
                Profile.discoverable,
            )
            .with_for_update(read=True)
        )
        if consents is None:
            return False
        candidate_visible = await self.db.scalar(
            select(Profile.user_id)
            .join(User, User.id == Profile.user_id)
            .where(Profile.user_id == candidate_id, visible_to(viewer_id))
        )
        return candidate_visible is not None

    async def _project_override(
        self, viewer_id: uuid.UUID, project_id: uuid.UUID
    ) -> tuple[frozenset[str], set[uuid.UUID]]:
        """`needed_skills` is a project's missing role skills; `exclude` is everyone already on
        the team or waiting on a decision. The owner-or-404 check never reveals why a project
        isn't theirs to use: an unknown id, someone else's project and anything but an open one
        (full or closed) all 404."""
        project = await self.db.scalar(select(Project).where(Project.id == project_id))
        if project is None or project.owner_id != viewer_id or project.status != "open":
            raise AppError(404, "not_found", PROJECT_NOT_FOUND)

        member_ids = set(
            await self.db.scalars(
                select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)
            )
        )
        # A role only counts toward "missing" while it still has room: the same remaining-slots
        # shape teams/service.py uses (holders per role_id, grouped), compared against open_slots.
        holder_counts = dict(
            (
                await self.db.execute(
                    select(ProjectMember.role_id, func.count())
                    .where(
                        ProjectMember.project_id == project_id,
                        ProjectMember.role_id.is_not(None),
                    )
                    .group_by(ProjectMember.role_id)
                )
            ).all()
        )
        roles = (
            await self.db.execute(
                select(ProjectRole.id, ProjectRole.open_slots).where(
                    ProjectRole.project_id == project_id
                )
            )
        ).all()
        unfilled_role_ids = {
            role_id for role_id, open_slots in roles if open_slots > holder_counts.get(role_id, 0)
        }

        role_skill_slugs = (
            set(
                await self.db.scalars(
                    select(Skill.slug)
                    .join(ProjectSkill, ProjectSkill.skill_id == Skill.id)
                    .where(ProjectSkill.role_id.in_(unfilled_role_ids))
                )
            )
            if unfilled_role_ids
            else set()
        )
        has_slugs = (
            set(
                await self.db.scalars(
                    select(Skill.slug)
                    .join(ProfileSkill, ProfileSkill.skill_id == Skill.id)
                    .where(ProfileSkill.user_id.in_(member_ids), ProfileSkill.kind == "has")
                )
            )
            if member_ids
            else set()
        )
        needed_skills = frozenset(role_skill_slugs - has_slugs)

        applicant_ids = set(
            await self.db.scalars(
                select(Application.applicant_id).where(
                    Application.project_id == project_id, Application.status == "pending"
                )
            )
        )
        return needed_skills, member_ids | applicant_ids

    async def _cards(
        self, viewer_id: uuid.UUID, recommendations: list[Recommendation]
    ) -> list[MatchOut]:
        if not recommendations:
            return []
        candidate_ids = [rec.user_id for rec in recommendations]
        # Re-applies visible_to: a candidate the ranker scored a moment ago but who has since
        # been blocked, hidden or suspended is dropped here, never shown.
        rows = (
            await self.db.execute(
                select(Profile.user_id, Profile.display_name, Profile.headline)
                .join(User, User.id == Profile.user_id)
                .where(Profile.user_id.in_(candidate_ids), visible_to(viewer_id))
            )
        ).all()
        visible_by_id = {row.user_id: row for row in rows}

        cards = []
        for rec in recommendations:
            row = visible_by_id.get(rec.user_id)
            if row is None:
                continue
            cards.append(
                MatchOut(
                    user_id=row.user_id,
                    display_name=row.display_name,
                    headline=row.headline,
                    reasons=[_reason_out(reason) for reason in rec.reasons],
                )
            )
        return cards

    def _limit(self, viewer_id: uuid.UUID) -> None:
        retry_after = self.limiter.hit("discovery", str(viewer_id), DISCOVERY_PER_USER)
        if retry_after is not None:
            raise AppError(
                429,
                "rate_limited",
                RATE_LIMITED,
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            )
