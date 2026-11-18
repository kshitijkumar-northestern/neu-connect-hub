"""Greedy team completion, without a database: who gets suggested, into which role, in what order,
and that no capacity, room or limit is ever exceeded."""

import random
import uuid
from collections import Counter

from app.ml.team import OpenRole, TeamCandidate, TeamPick, greedy_team


def uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def candidate(n: int, *has: str, overlap: int = 60) -> TeamCandidate:
    return TeamCandidate(user_id=uid(n), has=frozenset(has), overlap_minutes=overlap)


def role(n: int, *skills: str, room: int = 1, order: int | None = None) -> OpenRole:
    return OpenRole(
        role_id=uid(1000 + n),
        skills=frozenset(skills),
        room=room,
        order=n if order is None else order,
    )


def pick(candidate_n: int, role_n: int, *covers: str) -> TeamPick:
    return TeamPick(user_id=uid(candidate_n), role_id=uid(1000 + role_n), covers=frozenset(covers))


# --- a known fixture ------------------------------------------------------------------------


def test_a_known_fixture_gives_the_exact_picks_in_order() -> None:
    backend = role(0, "python", "sql")
    design = role(1, "figma", "ui-design")
    data = role(2, "sql", "data-analysis")
    needed = frozenset({"python", "sql", "figma", "ui-design", "data-analysis"})
    candidates = [
        candidate(1, "python", "sql", overlap=60),
        candidate(2, "figma", "ui-design", overlap=90),
        candidate(3, "data-analysis", "figma", overlap=30),
        candidate(4, "python", "sql", "figma", overlap=10),
    ]

    picks, uncovered = greedy_team(candidates, [backend, design, data], needed, 5, 5)

    # 4 brings three needed skills and fits Backend best (two of its skills against one each).
    # Then 1 brings nothing new; 2 and 3 each bring one, and 2 shares more time with the owner.
    assert picks == [
        pick(4, 0, "python", "sql", "figma"),
        pick(2, 1, "ui-design"),
        pick(3, 2, "data-analysis"),
    ]
    assert uncovered == frozenset()


# --- capacity, room and limit -----------------------------------------------------------------


def test_a_capacity_of_one_stops_after_one_pick() -> None:
    roles = [role(0, "python"), role(1, "figma")]
    candidates = [candidate(1, "python"), candidate(2, "figma")]

    picks, uncovered = greedy_team(candidates, roles, frozenset({"python", "figma"}), 1, 5)

    assert len(picks) == 1
    assert uncovered == frozenset({"python", "figma"}) - picks[0].covers


def test_no_capacity_left_gives_no_picks() -> None:
    picks, uncovered = greedy_team(
        [candidate(1, "python")], [role(0, "python")], frozenset({"python"}), 0, 5
    )
    assert picks == []
    assert uncovered == frozenset({"python"})


def test_the_limit_is_respected() -> None:
    roles = [role(0, "python", "figma", "sql", room=5)]
    candidates = [candidate(1, "python"), candidate(2, "figma"), candidate(3, "sql")]

    picks, uncovered = greedy_team(candidates, roles, frozenset({"python", "figma", "sql"}), 5, 2)

    assert [p.user_id for p in picks] == [uid(1), uid(2)]
    assert uncovered == frozenset({"sql"})


def test_a_role_with_room_one_is_never_assigned_twice() -> None:
    backend = role(0, "python", "sql", room=1)
    design = role(1, "figma", room=2)
    candidates = [
        candidate(1, "python", overlap=50),
        candidate(2, "sql", overlap=40),
        candidate(3, "figma", overlap=10),
    ]

    picks, _ = greedy_team(
        candidates, [backend, design], frozenset({"python", "sql", "figma"}), 5, 5
    )

    # 2 would only fit Backend again, which 1 already took.
    assert picks == [pick(1, 0, "python"), pick(3, 1, "figma")]


def test_a_role_passed_with_no_room_never_takes_a_pick() -> None:
    full = role(0, "python", room=0)
    # Two places, so it doesn't fill (and prune the needed skills) after one pick.
    open_ = role(1, "figma", room=2)

    picks, uncovered = greedy_team(
        [candidate(1, "python", overlap=500), candidate(2, "figma")],
        [full, open_],
        frozenset({"python", "figma"}),
        5,
        5,
    )

    assert picks == [pick(2, 1, "figma")]
    assert uncovered == frozenset()


# --- tie-breaks ---------------------------------------------------------------------------------


def test_the_most_needed_skills_wins_before_overlap() -> None:
    roles = [role(0, "python", "sql", "figma", room=3)]
    candidates = [candidate(1, "python", overlap=900), candidate(2, "sql", "figma", overlap=10)]

    picks, _ = greedy_team(candidates, roles, frozenset({"python", "sql", "figma"}), 1, 5)

    assert picks == [pick(2, 0, "sql", "figma")]


def test_equal_gain_goes_to_more_shared_time_with_the_owner() -> None:
    roles = [role(0, "python", "figma", room=2)]
    candidates = [candidate(1, "python", overlap=30), candidate(2, "figma", overlap=120)]

    picks, _ = greedy_team(candidates, roles, frozenset({"python", "figma"}), 5, 5)

    assert [p.user_id for p in picks] == [uid(2), uid(1)]


def test_equal_gain_and_overlap_go_to_the_smaller_user_id() -> None:
    roles = [role(0, "python", "figma", room=2)]
    # Listed largest id first, so input order can't be what decides.
    candidates = [candidate(9, "figma", overlap=60), candidate(3, "python", overlap=60)]

    picks, _ = greedy_team(candidates, roles, frozenset({"python", "figma"}), 5, 5)

    assert [p.user_id for p in picks] == [uid(3), uid(9)]


def test_a_pick_goes_to_the_role_sharing_the_most_of_their_gain() -> None:
    narrow = role(0, "python")
    wide = role(1, "python", "sql")

    picks, _ = greedy_team(
        [candidate(1, "python", "sql")], [narrow, wide], frozenset({"python", "sql"}), 5, 5
    )

    assert picks == [pick(1, 1, "python", "sql")]


def test_an_equally_good_role_fit_goes_to_the_earlier_role() -> None:
    # Listed later role first, so input order can't be what decides.
    later = role(1, "python", order=7)
    earlier = role(2, "python", order=3)

    picks, _ = greedy_team([candidate(1, "python")], [later, earlier], frozenset({"python"}), 5, 5)

    assert picks == [pick(1, 2, "python")]


# --- what counts as needed ----------------------------------------------------------------------


def test_a_candidate_covering_only_a_filled_roles_skills_is_never_picked() -> None:
    backend = role(0, "python", "sql", room=1)
    design = role(1, "figma", room=3)
    candidates = [
        candidate(1, "python", overlap=100),
        candidate(2, "sql", overlap=900),
        candidate(3, "figma", overlap=10),
    ]

    picks, uncovered = greedy_team(
        candidates, [backend, design], frozenset({"python", "sql", "figma"}), 5, 5
    )

    # 1 and 2 tie on gain and 2 shares more time, but once 2 fills Backend, 1's python is only
    # wanted by that filled role.
    assert picks == [pick(2, 0, "sql"), pick(3, 1, "figma")]
    assert uid(1) not in {p.user_id for p in picks}
    # Python is no longer reported missing: the only role that wanted it has nobody to take.
    assert uncovered == frozenset()


def test_skills_only_a_filled_role_requires_are_not_reported_uncovered() -> None:
    backend = role(0, "python", "sql", room=1)
    design = role(1, "figma", "ui-design", room=2)

    picks, uncovered = greedy_team(
        [candidate(1, "python")],
        [backend, design],
        frozenset({"python", "sql", "figma", "ui-design"}),
        5,
        5,
    )

    assert picks == [pick(1, 0, "python")]
    assert uncovered == frozenset({"figma", "ui-design"})


def test_a_member_holds_their_skills_whatever_their_role() -> None:
    data = role(0, "sql", "statistics", room=2)
    backend = role(1, "python", room=1)
    candidates = [
        candidate(1, "python", "sql", overlap=10),
        candidate(2, "python", overlap=5),
        candidate(3, "statistics", overlap=1),
    ]

    picks, uncovered = greedy_team(
        candidates, [data, backend], frozenset({"python", "sql", "statistics"}), 5, 5
    )

    # 1 fits Data and Backend equally and goes to the earlier, Data; their python still counts
    # for the team, so 2 brings nothing new and Backend is left to nobody.
    assert picks == [pick(1, 0, "python", "sql"), pick(3, 0, "statistics")]
    assert uncovered == frozenset()


def test_empty_needed_gives_no_picks() -> None:
    picks, uncovered = greedy_team([candidate(1, "python")], [role(0, "python")], frozenset(), 5, 5)
    assert picks == []
    assert uncovered == frozenset()


def test_nobody_covering_anything_gives_no_picks_and_everything_uncovered() -> None:
    needed = frozenset({"python", "figma"})
    picks, uncovered = greedy_team(
        [candidate(1, "go"), candidate(2, "rust")],
        [role(0, "python"), role(1, "figma")],
        needed,
        5,
        5,
    )
    assert picks == []
    assert uncovered == needed


def test_no_candidates_gives_no_picks_and_everything_uncovered() -> None:
    needed = frozenset({"python"})
    assert greedy_team([], [role(0, "python")], needed, 5, 5) == ([], needed)


# --- invariants over many random inputs ------------------------------------------------------

SKILLS = ("python", "sql", "figma", "ui-design", "go", "rust", "statistics", "docker")


def _random_case(
    rng: random.Random,
) -> tuple[list[TeamCandidate], list[OpenRole], frozenset[str], int, int]:
    roles = [
        OpenRole(
            role_id=uuid.UUID(int=rng.getrandbits(128)),
            skills=frozenset(rng.sample(SKILLS, rng.randint(0, 4))),
            room=rng.randint(0, 3),
            order=i,
        )
        for i in range(rng.randint(0, 4))
    ]
    open_skills = {s for r in roles if r.room > 0 for s in r.skills}
    members_have = set(rng.sample(SKILLS, rng.randint(0, 3)))
    needed = frozenset(open_skills - members_have)
    candidates = [
        TeamCandidate(
            user_id=uuid.UUID(int=rng.getrandbits(128)),
            has=frozenset(rng.sample(SKILLS, rng.randint(0, 5))),
            # A narrow range so overlap ties, and the user id tie-break, actually happen.
            overlap_minutes=rng.choice((30, 60, 90)),
        )
        for _ in range(rng.randint(0, 12))
    ]
    return candidates, roles, needed, rng.randint(-1, 6), rng.randint(-1, 6)


def test_random_inputs_never_exceed_capacity_room_or_limit() -> None:
    rng = random.Random(20261117)  # noqa: S311 - test data, not security
    for _ in range(400):
        candidates, roles, needed, capacity_left, limit = _random_case(rng)

        picks, uncovered = greedy_team(candidates, roles, needed, capacity_left, limit)

        assert len(picks) <= max(0, min(capacity_left, limit))
        room = {r.role_id: r.room for r in roles}
        for role_id, taken in Counter(p.role_id for p in picks).items():
            assert taken <= room[role_id]
        by_id = {c.user_id: c for c in candidates}
        assert len({p.user_id for p in picks}) == len(picks)
        covered: set[str] = set()
        for p in picks:
            assert p.covers
            assert p.covers <= needed
            assert p.covers <= by_id[p.user_id].has
            assert not p.covers & covered
            covered |= p.covers
        assert uncovered <= needed
        assert not uncovered & covered

        # The same input in another order gives the same answer.
        shuffled_candidates = rng.sample(candidates, len(candidates))
        shuffled_roles = rng.sample(roles, len(roles))
        assert greedy_team(shuffled_candidates, shuffled_roles, needed, capacity_left, limit) == (
            picks,
            uncovered,
        )
