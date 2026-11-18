"""Suggested teammates for a project's owner: students who could fill the skills the team still
lacks, chosen by the greedy coverage pass in `app.ml.team`.

Who may be suggested is one predicate, `suggestable`, applied twice: by the candidate query, and
again when the chosen students' cards are read, so anyone who hid, was blocked or otherwise
stopped qualifying in between is never shown. Shared time with the owner and the greedy pass run
on the candidates only. It suggests and never enrols: nothing is written, and nobody is told they
were suggested.

Known limitation: the candidate pool (CANDIDATE_POOL students, those holding the most needed
skills first) is cut before shared time with the owner is worked out. In a large pool spread
across time zones, everyone in it could share no time with the owner, and nobody would be
suggested even though someone further down the list could have met.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import ColumnElement, and_, any_, exists, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter, enforce
from app.ml.availability import overlap_with, viewer_week
from app.ml.team import OpenRole, TeamCandidate, greedy_team
from app.modules.auth.models import User
from app.modules.discovery.ranker import CANDIDATE_POOL
from app.modules.discovery.service import OPT_IN_REQUIRED, RATE_LIMITED, STUDENTS_ONLY
from app.modules.profiles.models import AvailabilitySlot, Profile, ProfileSkill, Skill
from app.modules.profiles.schemas import Goal, NamedItem
from app.modules.profiles.slots import load_slots
from app.modules.profiles.visibility import blocked_between, visible_to
from app.modules.teams.models import Application, Project, ProjectMember
from app.modules.teams.needs import TeamNeeds, by_name, team_needs
from app.modules.teams.schemas import RoleRef, TeamSuggestion, TeamSuggestionsOut
from app.modules.teams.service import NOT_FOUND

logger = logging.getLogger("app.teams")

TEAM_SUGGESTIONS_PER_USER: Final = Limit(max_events=30, window_seconds=60)
MAX_TEAM_SUGGESTIONS: Final = 5
# How many times the greedy pass may run when picks vanish before their cards are read.
MAX_PASSES: Final = 3
# Only students who said they want to build projects are suggested for one.
WILLING_GOAL: Final[Goal] = "project_development"

AVAILABILITY_REQUIRED = "Add your weekly availability to see suggested teammates."


def suggestable(owner_id: uuid.UUID, project_id: uuid.UUID) -> ColumnElement[bool]:
    """Students who may be suggested for this project, for a query joining Profile to User:
    visible to the owner and opted in to discovery, a student, not the owner, not on the team or
    waiting on a decision, no block either way with anyone on the team, willing to build projects,
    and with some availability. A connection with the owner excludes nobody: a friend is a fine
    teammate."""
    on_team = exists().where(
        ProjectMember.project_id == project_id, ProjectMember.user_id == Profile.user_id
    )
    pending = exists().where(
        Application.project_id == project_id,
        Application.applicant_id == Profile.user_id,
        Application.status == "pending",
    )
    # Two levels deep, auto-correlation only reaches the immediately enclosing subquery: left to
    # it, the block check would select from its own copy of profiles and one block between any
    # member and anyone would hide every candidate. Correlate both sides explicitly.
    blocked_with_team = exists().where(
        ProjectMember.project_id == project_id,
        blocked_between(ProjectMember.user_id, Profile.user_id).correlate(ProjectMember, Profile),
    )
    return and_(
        visible_to(owner_id),
        Profile.discoverable,
        User.role == "student",
        Profile.user_id != owner_id,
        ~on_team,
        ~pending,
        ~blocked_with_team,
        literal(WILLING_GOAL) == any_(Profile.goals),
        exists().where(AvailabilitySlot.user_id == Profile.user_id),
    )


@dataclass(frozen=True)
class _Owner:
    timezone: str
    capacity: int


@dataclass(frozen=True)
class _Candidate:
    user_id: uuid.UUID
    timezone: str
    # The needed skills they have (never their other skills).
    has: frozenset[str]


class TeamSuggestionService:
    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db = db
        self.limiter = limiter

    async def suggestions(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, *, now: datetime | None = None
    ) -> TeamSuggestionsOut:
        """Up to MAX_TEAM_SUGGESTIONS students for an open project, owner only. Reciprocal, as in
        Discover: an owner who hasn't opted in to discovery isn't shown anyone either."""
        enforce(
            self.limiter, "team_suggestions", str(owner_id), TEAM_SUGGESTIONS_PER_USER, RATE_LIMITED
        )
        owner = await self._owner_project(owner_id, project_id)

        needs = await team_needs(self.db, project_id)
        needed = needs.missing_slugs
        if not needed:
            return TeamSuggestionsOut(suggestions=[], uncovered=[])
        candidates = await self._candidates(owner_id, project_id, needed)
        if not candidates:
            return TeamSuggestionsOut(suggestions=[], uncovered=list(needs.missing))

        slots = await load_slots(self.db, [owner_id, *(c.user_id for c in candidates)])
        # The owner's week is laid out once; each candidate is then only intersected with it.
        week = viewer_week(slots.get(owner_id, []), owner.timezone, now=now or datetime.now(UTC))
        pool = []
        for candidate in candidates:
            overlap = overlap_with(week, slots.get(candidate.user_id, []), candidate.timezone)
            # A team that can never meet isn't a team: no shared time, no suggestion.
            if overlap.minutes > 0:
                pool.append(
                    TeamCandidate(
                        user_id=candidate.user_id,
                        has=candidate.has,
                        overlap_minutes=overlap.minutes,
                    )
                )

        suggestions, passes = await self._choose(
            owner_id, project_id, pool, needs, max(0, owner.capacity - len(needs.member_ids))
        )
        # What the suggestions actually returned bring, against everything the team needs: the
        # two always add up to the project page's missing skills.
        covered = {item.slug for suggestion in suggestions for item in suggestion.covers}
        uncovered = [item for item in needs.missing if item.slug not in covered]

        logger.info(
            "Computed team suggestions",
            extra={
                "candidates": len(candidates),
                "with_shared_time": len(pool),
                "passes": passes,
                "suggested": len(suggestions),
                "uncovered": len(uncovered),
            },
        )
        return TeamSuggestionsOut(suggestions=suggestions, uncovered=uncovered)

    # --- the owner --------------------------------------------------------------------------

    async def _owner_project(self, owner_id: uuid.UUID, project_id: uuid.UUID) -> _Owner:
        """The owner's timezone and the project's capacity, once the caller is shown to be allowed
        to ask, all from one query. Someone else's project, an unknown id and anything but an open
        one all look the same: 404."""
        row = (
            await self.db.execute(
                select(
                    Project.owner_id,
                    Project.status,
                    Project.capacity,
                    User.role,
                    User.verified_at,
                    User.suspended_at,
                    Profile.discoverable,
                    Profile.timezone,
                    exists()
                    .where(AvailabilitySlot.user_id == Project.owner_id)
                    .label("has_availability"),
                )
                .join(User, User.id == Project.owner_id)
                .outerjoin(Profile, Profile.user_id == Project.owner_id)
                .where(Project.id == project_id)
            )
        ).first()
        if row is None or row.owner_id != owner_id or row.status != "open":
            raise AppError(404, "not_found", NOT_FOUND)
        # The caller is the owner from here on, so the owner's account is the caller's.
        if row.role != "student":
            raise AppError(403, "discovery_students_only", STUDENTS_ONLY)
        # The same test as discovery's viewer_eligible (the role is checked just above): a profile
        # that opted in, on a verified account that isn't suspended.
        if not (row.discoverable and row.verified_at is not None and row.suspended_at is None):
            raise AppError(403, "discovery_opt_in_required", OPT_IN_REQUIRED)
        # Nobody can share time with an owner who has none: say so rather than "no one matches".
        if not row.has_availability:
            raise AppError(409, "availability_required", AVAILABILITY_REQUIRED)
        return _Owner(timezone=row.timezone, capacity=row.capacity)

    # --- candidates and cards -----------------------------------------------------------------

    async def _candidates(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, needed: frozenset[str]
    ) -> list[_Candidate]:
        """Everyone suggestable who holds at least one needed skill, those holding the most
        first, at most CANDIDATE_POOL of them."""
        rows = (
            await self.db.execute(
                select(Profile.user_id, Profile.timezone, func.array_agg(Skill.slug).label("has"))
                .select_from(Profile)
                .join(User, User.id == Profile.user_id)
                .join(
                    ProfileSkill,
                    and_(ProfileSkill.user_id == Profile.user_id, ProfileSkill.kind == "has"),
                )
                .join(Skill, Skill.id == ProfileSkill.skill_id)
                .where(Skill.slug.in_(needed), suggestable(owner_id, project_id))
                .group_by(Profile.user_id)
                .order_by(func.count().desc(), Profile.user_id)
                .limit(CANDIDATE_POOL)
            )
        ).all()
        return [
            _Candidate(user_id=row.user_id, timezone=row.timezone, has=frozenset(row.has))
            for row in rows
        ]

    async def _choose(
        self,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        pool: Sequence[TeamCandidate],
        needs: TeamNeeds,
        capacity_left: int,
    ) -> tuple[list[TeamSuggestion], int]:
        """Runs the greedy pass and reads the picks' cards. Anyone who stopped being suggestable
        since the candidate query is left out and the pass re-run without them, so their place
        goes to the next best candidate; after MAX_PASSES the last answer stands, minus anyone
        who vanished from it. Returns the suggestions in pick order and the passes it took."""
        roles = [
            OpenRole(role_id=role.id, skills=role.slugs, room=role.room, order=role.order)
            for role in needs.open_roles
        ]
        titles = {role.id: role.title for role in needs.roles}
        names = {item.slug: item.name for role in needs.roles for item in role.skills}

        vanished: set[uuid.UUID] = set()
        suggestions: list[TeamSuggestion] = []
        passes = 0
        while passes < MAX_PASSES:
            passes += 1
            picks, _ = greedy_team(
                [c for c in pool if c.user_id not in vanished],
                roles,
                needs.missing_slugs,
                capacity_left,
                MAX_TEAM_SUGGESTIONS,
            )
            cards = await self._cards(owner_id, project_id, [pick.user_id for pick in picks])
            suggestions = [
                TeamSuggestion(
                    user_id=pick.user_id,
                    display_name=cards[pick.user_id][0],
                    headline=cards[pick.user_id][1],
                    role=RoleRef(id=pick.role_id, title=titles[pick.role_id]),
                    covers=by_name(NamedItem(slug=slug, name=names[slug]) for slug in pick.covers),
                )
                for pick in picks
                if pick.user_id in cards
            ]
            gone = {pick.user_id for pick in picks} - cards.keys()
            if not gone:
                break
            vanished |= gone
        return suggestions, passes

    async def _cards(
        self, owner_id: uuid.UUID, project_id: uuid.UUID, user_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[str, str]]:
        """Display name and headline for whichever of `user_ids` are still suggestable."""
        if not user_ids:
            return {}
        rows = await self.db.execute(
            select(Profile.user_id, Profile.display_name, Profile.headline)
            .join(User, User.id == Profile.user_id)
            .where(Profile.user_id.in_(user_ids), suggestable(owner_id, project_id))
        )
        return {user_id: (name, headline) for user_id, name, headline in rows}
