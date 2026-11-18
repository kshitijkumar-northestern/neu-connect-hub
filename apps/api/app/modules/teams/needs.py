"""What a project's team still lacks, worked out in one place for every feature that asks: the
project page's missing skills, Discover's project override and suggested teammates.

A role only counts while it has room (its open slots minus the members holding it). The skills
the team still needs are those required by a role with room that no member, the owner included,
has: whichever role a member holds, every skill they have counts for the team.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.profiles.models import ProfileSkill, Skill
from app.modules.profiles.schemas import NamedItem
from app.modules.teams.models import ProjectMember, ProjectRole, ProjectSkill


def by_name(items: Iterable[NamedItem]) -> list[NamedItem]:
    """Skills the way every project view lists them: by name, case-insensitively."""
    return sorted(items, key=lambda item: (item.name.casefold(), item.slug))


@dataclass(frozen=True)
class RoleNeed:
    id: uuid.UUID
    title: str
    open_slots: int
    # Places left: open slots minus the members holding the role, never below 0.
    room: int
    # The role's position in the project, the order the project page lists roles in.
    order: int
    # Sorted by name.
    skills: tuple[NamedItem, ...]

    @property
    def slugs(self) -> frozenset[str]:
        return frozenset(item.slug for item in self.skills)


@dataclass(frozen=True)
class TeamNeeds:
    # Every role, full or not, in the project's order.
    roles: tuple[RoleNeed, ...]
    # Everyone on the team, the owner included.
    member_ids: frozenset[uuid.UUID]
    # Skill slugs any member has.
    member_slugs: frozenset[str]
    # Required by a role with room and held by no member, sorted by name.
    missing: tuple[NamedItem, ...]

    @property
    def open_roles(self) -> tuple[RoleNeed, ...]:
        return tuple(role for role in self.roles if role.room > 0)

    @property
    def missing_slugs(self) -> frozenset[str]:
        return frozenset(item.slug for item in self.missing)


async def team_needs(db: AsyncSession, project_id: uuid.UUID) -> TeamNeeds:
    """Three queries, however large the team or the role list."""
    # One row per member and skill they have (or one row with no skill): who is on the team,
    # which role each holds, and every skill the team already has.
    member_rows = (
        await db.execute(
            select(ProjectMember.user_id, ProjectMember.role_id, Skill.slug)
            .select_from(ProjectMember)
            .outerjoin(
                ProfileSkill,
                and_(ProfileSkill.user_id == ProjectMember.user_id, ProfileSkill.kind == "has"),
            )
            .outerjoin(Skill, Skill.id == ProfileSkill.skill_id)
            .where(ProjectMember.project_id == project_id)
        )
    ).all()
    role_by_member = {user_id: role_id for user_id, role_id, _ in member_rows}
    holders = Counter(role_id for role_id in role_by_member.values() if role_id is not None)
    member_slugs = frozenset(slug for _, _, slug in member_rows if slug is not None)

    roles = (
        await db.execute(
            select(ProjectRole.id, ProjectRole.title, ProjectRole.open_slots)
            .where(ProjectRole.project_id == project_id)
            .order_by(ProjectRole.created_at, ProjectRole.id)
        )
    ).all()
    role_ids = [role.id for role in roles]
    skill_rows = (
        (
            await db.execute(
                select(ProjectSkill.role_id, Skill.slug, Skill.name)
                .join(Skill, Skill.id == ProjectSkill.skill_id)
                .where(ProjectSkill.role_id.in_(role_ids))
            )
        ).all()
        if role_ids
        else []
    )
    skills_by_role: dict[uuid.UUID, list[NamedItem]] = defaultdict(list)
    for role_id, slug, name in skill_rows:
        skills_by_role[role_id].append(NamedItem(slug=slug, name=name))

    role_needs = tuple(
        RoleNeed(
            id=role.id,
            title=role.title,
            open_slots=role.open_slots,
            room=max(0, role.open_slots - holders.get(role.id, 0)),
            order=order,
            skills=tuple(by_name(skills_by_role.get(role.id, []))),
        )
        for order, role in enumerate(roles)
    )
    missing = {
        item.slug: item
        for role in role_needs
        if role.room > 0
        for item in role.skills
        if item.slug not in member_slugs
    }
    return TeamNeeds(
        roles=role_needs,
        member_ids=frozenset(role_by_member),
        member_slugs=member_slugs,
        missing=tuple(by_name(missing.values())),
    )
