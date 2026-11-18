"""Pure match scoring: every component, `combine` and `reasons_for`. No database, no clock."""

import math
import random
import uuid

from app.ml.scoring import (
    AVAILABILITY_TARGET_MINUTES,
    REASON_FIELDS,
    WEIGHTS,
    AvailabilityOverlap,
    Components,
    Item,
    MatchFeatures,
    Overlap,
    OverlapWindow,
    SharedGoals,
    SharedInterests,
    SkillsCovered,
    WeeklySlot,
    Weights,
    availability_component,
    combine,
    complementary_component,
    goal_component,
    interest_component,
    jaccard,
    reasons_for,
    semantic_component,
)

UI = Item(slug="ui_design", name="UI design")
FIGMA = Item(slug="figma", name="Figma")
PYTHON = Item(slug="python", name="Python")
MUSIC = Item(slug="music", name="Music and arts")
EDUCATION = Item(slug="education", name="Education")


def features(**overrides: object) -> MatchFeatures:
    base: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "timezone": "America/New_York",
        "goals": frozenset(),
        "has_skills": frozenset(),
        "seeking_skills": frozenset(),
        "interests": frozenset(),
        "availability": (),
    }
    base.update(overrides)
    return MatchFeatures(**base)  # type: ignore[arg-type]


# -- semantic_component -------------------------------------------------------------------------


def test_semantic_component_clamps_and_rejects_non_finite() -> None:
    assert semantic_component(-1.0) == 0.0
    assert semantic_component(0.0) == 0.0
    assert semantic_component(0.5) == 0.5
    assert semantic_component(1.0) == 1.0
    assert semantic_component(1.0000001) == 1.0
    assert semantic_component(float("nan")) == 0.0
    assert semantic_component(float("inf")) == 0.0
    assert semantic_component(float("-inf")) == 0.0


# -- jaccard --------------------------------------------------------------------------------


def test_jaccard_empty_both_sides_is_zero() -> None:
    assert jaccard(frozenset(), frozenset()) == 0.0


def test_jaccard_empty_one_side_is_zero() -> None:
    assert jaccard(frozenset({"a"}), frozenset()) == 0.0
    assert jaccard(frozenset(), frozenset({"a"})) == 0.0


def test_jaccard_full_overlap_is_one() -> None:
    assert jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0


def test_jaccard_partial_overlap() -> None:
    assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == 1 / 3


# -- complementary_component ------------------------------------------------------------------


def test_complementary_component_empty_needed_is_zero() -> None:
    assert complementary_component(frozenset(), frozenset({UI})) == 0.0


def test_complementary_component_empty_has_is_zero() -> None:
    assert complementary_component(frozenset({UI, FIGMA}), frozenset()) == 0.0


def test_complementary_component_full_coverage_is_one() -> None:
    assert complementary_component(frozenset({UI}), frozenset({UI, PYTHON})) == 1.0


def test_complementary_component_partial_coverage() -> None:
    assert complementary_component(frozenset({UI, FIGMA}), frozenset({UI})) == 0.5


def test_complementary_component_matches_by_slug_not_name() -> None:
    renamed = Item(slug="ui_design", name="UX design")
    assert complementary_component(frozenset({UI}), frozenset({renamed})) == 1.0


# -- goal_component / interest_component (Jaccard wrappers) ------------------------------------


def test_goal_component_empty_both_is_zero() -> None:
    assert goal_component(frozenset(), frozenset()) == 0.0


def test_goal_component_full_overlap_is_one() -> None:
    assert goal_component(frozenset({"research"}), frozenset({"research"})) == 1.0


def test_goal_component_partial_overlap() -> None:
    assert goal_component(frozenset({"research", "studying"}), frozenset({"studying"})) == 0.5


def test_interest_component_empty_both_is_zero() -> None:
    assert interest_component(frozenset(), frozenset()) == 0.0


def test_interest_component_full_overlap_is_one() -> None:
    assert interest_component(frozenset({MUSIC}), frozenset({MUSIC})) == 1.0


def test_interest_component_partial_overlap_by_slug() -> None:
    assert interest_component(frozenset({MUSIC, EDUCATION}), frozenset({MUSIC})) == 0.5


# -- availability_component ---------------------------------------------------------------------


def test_availability_component_empty_overlap_is_zero() -> None:
    assert availability_component(Overlap(minutes=0, longest=None)) == 0.0


def test_availability_component_scales_linearly_below_target() -> None:
    half_target = AVAILABILITY_TARGET_MINUTES // 2
    window = OverlapWindow(weekday=0, start_min=0, end_min=half_target)
    assert availability_component(Overlap(minutes=half_target, longest=window)) == 0.5


def test_availability_component_caps_at_one() -> None:
    window = OverlapWindow(weekday=0, start_min=0, end_min=1440)
    minutes = AVAILABILITY_TARGET_MINUTES * 10
    assert availability_component(Overlap(minutes=minutes, longest=window)) == 1.0


def test_availability_component_hits_exactly_one_at_the_target() -> None:
    window = OverlapWindow(weekday=0, start_min=0, end_min=AVAILABILITY_TARGET_MINUTES)
    overlap = Overlap(minutes=AVAILABILITY_TARGET_MINUTES, longest=window)
    assert availability_component(overlap) == 1.0


# -- WEIGHTS / combine ----------------------------------------------------------------------


def test_weights_sum_to_one() -> None:
    total = WEIGHTS.semantic + WEIGHTS.complementary + WEIGHTS.availability + WEIGHTS.goal
    assert math.isclose(total, 1.0)


def test_combine_all_zero_is_zero() -> None:
    assert combine(Components(semantic=0, complementary=0, availability=0, goal=0)) == 0.0


def test_combine_all_one_is_one() -> None:
    components = Components(semantic=1, complementary=1, availability=1, goal=1)
    assert math.isclose(combine(components), 1.0)


def test_combine_is_the_weighted_sum() -> None:
    components = Components(semantic=0.5, complementary=0.2, availability=0.8, goal=0.0)
    expected = 0.5 * WEIGHTS.semantic + 0.2 * WEIGHTS.complementary + 0.8 * WEIGHTS.availability
    assert math.isclose(combine(components), expected)


def test_combine_clamps_against_float_error() -> None:
    # Weights (each in [0, 1], summing to 1) and components in [0, 1] can never truly exceed 1 or
    # go below 0, but `combine` clamps anyway as a defence against float accumulation error.
    nudged = Weights(
        semantic=WEIGHTS.semantic + 1e-12,
        complementary=WEIGHTS.complementary,
        availability=WEIGHTS.availability,
        goal=WEIGHTS.goal,
    )
    components = Components(semantic=1.0, complementary=1.0, availability=1.0, goal=1.0)
    assert combine(components, nudged) <= 1.0


def test_combine_is_always_in_unit_interval_property() -> None:
    rng = random.Random(20261117)  # noqa: S311 - deterministic test seed, not cryptographic use
    for _ in range(500):
        components = Components(
            semantic=rng.uniform(0, 1),
            complementary=rng.uniform(0, 1),
            availability=rng.uniform(0, 1),
            goal=rng.uniform(0, 1),
        )
        result = combine(components)
        assert 0.0 <= result <= 1.0


# -- reasons_for ------------------------------------------------------------------------------


def test_reasons_for_fixed_order_and_omits_empty_kinds() -> None:
    viewer = features(
        goals=frozenset({"research", "studying"}),
        interests=frozenset({MUSIC, EDUCATION}),
    )
    candidate = features(
        goals=frozenset({"studying", "mentorship"}),
        has_skills=frozenset({UI, PYTHON}),
        interests=frozenset({MUSIC}),
    )
    window = OverlapWindow(weekday=1, start_min=1080, end_min=1200)
    overlap = Overlap(minutes=120, longest=window)
    needed = frozenset({UI, FIGMA})

    reasons = reasons_for(viewer, candidate, needed, overlap)

    assert [reason.kind for reason in reasons] == [
        "skills_covered",
        "shared_interests",
        "availability_overlap",
        "shared_goals",
    ]
    skills_covered, shared_interests, availability_overlap, shared_goals = reasons
    assert skills_covered.items == (UI,)
    assert shared_interests.items == (MUSIC,)
    assert availability_overlap.window == window
    assert shared_goals.goals == ("studying",)


def test_reasons_for_omits_kinds_with_nothing_to_say() -> None:
    viewer = features()
    candidate = features()
    reasons = reasons_for(viewer, candidate, frozenset(), Overlap(minutes=0, longest=None))
    assert reasons == ()


def test_reasons_for_skills_covered_uses_candidates_item_names_and_sorts_by_name() -> None:
    needed_ui = Item(slug="ui_design", name="whatever the viewer called it")
    candidate_ui = Item(slug="ui_design", name="UI design")
    candidate_figma = Item(slug="figma", name="Figma")
    viewer = features()
    candidate = features(has_skills=frozenset({candidate_ui, candidate_figma}))
    needed = frozenset({needed_ui, candidate_figma})

    reasons = reasons_for(viewer, candidate, needed, Overlap(minutes=0, longest=None))

    assert len(reasons) == 1
    skills_covered = reasons[0]
    assert isinstance(skills_covered, SkillsCovered)
    # Sorted by name (casefold): "Figma" before "UI design".
    assert skills_covered.items == (candidate_figma, candidate_ui)
    assert skills_covered.items[0].name == "Figma"


def test_reasons_for_shared_interests_sorted_by_name() -> None:
    interest_a = Item(slug="a", name="Zoology")
    interest_b = Item(slug="b", name="Astronomy")
    viewer = features(interests=frozenset({interest_a, interest_b}))
    candidate = features(interests=frozenset({interest_a, interest_b}))

    reasons = reasons_for(viewer, candidate, frozenset(), Overlap(minutes=0, longest=None))

    assert len(reasons) == 1
    shared = reasons[0]
    assert isinstance(shared, SharedInterests)
    assert shared.items == (interest_b, interest_a)


def test_reasons_for_availability_omitted_when_minutes_zero_even_with_a_window() -> None:
    # Guards the `overlap.minutes > 0` half of the boundary, not just `longest is not None`.
    window = OverlapWindow(weekday=0, start_min=0, end_min=1)
    reasons = reasons_for(features(), features(), frozenset(), Overlap(minutes=0, longest=window))
    assert reasons == ()


def test_reasons_for_availability_omitted_when_no_longest() -> None:
    reasons = reasons_for(features(), features(), frozenset(), Overlap(minutes=10, longest=None))
    assert reasons == ()


def test_reasons_for_shared_goals_in_goals_order_not_input_order() -> None:
    viewer = features(goals=frozenset({"mentorship", "studying", "research"}))
    candidate = features(goals=frozenset({"research", "mentorship"}))

    reasons = reasons_for(viewer, candidate, frozenset(), Overlap(minutes=0, longest=None))

    assert len(reasons) == 1
    shared_goals = reasons[0]
    assert isinstance(shared_goals, SharedGoals)
    # GOALS order is ("studying", "project_development", "research", "mentorship").
    assert shared_goals.goals == ("research", "mentorship")


def test_reasons_for_every_reason_kind_is_registered_and_values_come_from_candidate() -> None:
    viewer = features(
        goals=frozenset({"research"}),
        interests=frozenset({MUSIC}),
    )
    candidate_slot = WeeklySlot(weekday=2, start_min=600, end_min=660)
    candidate = features(
        goals=frozenset({"research"}),
        has_skills=frozenset({UI}),
        interests=frozenset({MUSIC}),
        availability=(candidate_slot,),
    )
    window = OverlapWindow(weekday=2, start_min=610, end_min=650)
    overlap = Overlap(minutes=40, longest=window)

    reasons = reasons_for(viewer, candidate, frozenset({UI}), overlap)

    for reason in reasons:
        assert reason.kind in REASON_FIELDS
        if isinstance(reason, SkillsCovered):
            assert set(reason.items) <= candidate.has_skills
        elif isinstance(reason, SharedInterests):
            assert set(reason.items) <= candidate.interests
        elif isinstance(reason, SharedGoals):
            assert set(reason.goals) <= candidate.goals
        elif isinstance(reason, AvailabilityOverlap):
            assert reason.window.weekday == candidate_slot.weekday
            assert candidate_slot.start_min <= reason.window.start_min
            assert reason.window.end_min <= candidate_slot.end_min
