"""Greedy team completion: who could fill the skills a project still lacks.

Pure and deterministic, with no database and no clock. The caller decides who is eligible and
willing and how much of the week they share with the owner; this only chooses among them. It
suggests and never enrols.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class TeamCandidate:
    user_id: uuid.UUID
    # Skill slugs the student has.
    has: frozenset[str]
    # Shared minutes with the project owner in the coming week; the caller has already dropped 0.
    overlap_minutes: int


@dataclass(frozen=True)
class OpenRole:
    role_id: uuid.UUID
    # Skill slugs the role requires.
    skills: frozenset[str]
    # Places left: open slots minus the members already holding the role.
    room: int
    # The role's position in the project (creation order), for ties.
    order: int


@dataclass(frozen=True)
class TeamPick:
    user_id: uuid.UUID
    role_id: uuid.UUID
    # Needed skills this student brings that no earlier pick or member had.
    covers: frozenset[str]


def greedy_team(
    candidates: Sequence[TeamCandidate],
    roles: Sequence[OpenRole],
    needed: frozenset[str],
    capacity_left: int,
    limit: int,
) -> tuple[list[TeamPick], frozenset[str]]:
    """Repeatedly picks whoever covers the most still-needed skills (ties: more shared time with
    the owner, then the smaller user id) and places them in the role with room they fit best (ties:
    the earlier role). Never exceeds `capacity_left`, any role's room or `limit`. Returns the picks
    in order and the needed skills nobody picked covers."""
    room = {role.role_id: role.room for role in roles}
    needed_left = set(needed) & _open_skills(roles, room)
    remaining = list(candidates)
    picks: list[TeamPick] = []
    max_picks = min(capacity_left, limit)

    while len(picks) < max_picks and needed_left and remaining:
        # `needed_left` only ever holds skills some role with room still requires (see the
        # pruning below), so the gain is already limited to those roles' skills.
        best = min(
            remaining,
            key=lambda c: (-len(c.has & needed_left), -c.overlap_minutes, c.user_id),
        )
        gain = frozenset(best.has & needed_left)
        if not gain:
            break
        remaining.remove(best)

        fits = [role for role in roles if room[role.role_id] > 0 and role.skills & gain]
        if not fits:
            # Unreachable: every skill in the gain is one some role with room still requires.
            raise AssertionError("no role with room requires any of a non-empty gain")
        chosen = min(fits, key=lambda role: (-len(role.skills & gain), role.order))

        picks.append(TeamPick(user_id=best.user_id, role_id=chosen.role_id, covers=gain))
        room[chosen.role_id] -= 1
        # A member holds every skill they have, whichever role they fill: the same rule as the
        # project's missing skills.
        needed_left -= best.has
        if room[chosen.role_id] == 0:
            needed_left &= _open_skills(roles, room)

    return picks, frozenset(needed_left)


def _open_skills(roles: Sequence[OpenRole], room: dict[uuid.UUID, int]) -> set[str]:
    """Skills required by at least one role that still has room. A skill only a filled role
    wants is no longer needed: nobody new could take that role to bring it."""
    return {skill for role in roles if room[role.role_id] > 0 for skill in role.skills}
