"""The offline evaluation: metrics on hand-computed cases, the good-match label, the synthetic
generator, the three ranking systems and the end-to-end report. No database, and only the fake
encoder: CI never loads the real model."""

import dataclasses
import functools
import importlib.util
import itertools
import json
import math
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.ml.encoders import FakeEncoder
from app.ml.profile_text import ProfileTextFields
from app.ml.scoring import (
    WEIGHTS,
    Item,
    MatchFeatures,
    Overlap,
    OverlapWindow,
    WeeklySlot,
    interest_component,
    score_pair,
)
from app.modules.profiles.models import BIO_MAX, GOALS, HEADLINE_MAX
from experiments.evaluate import (
    DEFAULT_OUT,
    REFERENCE_NOW,
    SYSTEMS,
    Candidate,
    evaluate,
    main,
    model_slug,
    parse_args,
    rank,
    report_json,
    report_stem,
    split_viewers,
)
from experiments.labels import LABEL_VERSION, MIN_SHARED_MINUTES, is_good_match
from experiments.metrics import ConstraintRates, Shown, constraint_rates, ndcg_at_k, precision_at_k
from experiments.synthetic import (
    CLUSTERS,
    DEFAULT_STUDENTS,
    TIMEZONES,
    SyntheticStudent,
    generate,
)

# For the tests that only need a population, not the default-size one the CLI evaluates.
SMALL_N = 60

# 1 / log2(rank + 1) for ranks 1 to 3, written out so expectations never come from the code under
# test.
DISCOUNT_1 = 1.0
DISCOUNT_2 = 1 / math.log2(3)  # 0.6309...
DISCOUNT_3 = 1 / math.log2(4)  # 0.5

PYTHON = Item(slug="python", name="Python")
FIGMA = Item(slug="figma", name="Figma")
REACT = Item(slug="react", name="React")
GAMING = Item(slug="gaming", name="Gaming")
OPEN_SOURCE = Item(slug="open-source", name="Open source")


# --- metrics: precision@k ---------------------------------------------------------------------


def test_precision_counts_relevant_items_in_the_top_k() -> None:
    assert precision_at_k([True, False, True, False, False], 5) == pytest.approx(2 / 5)


def test_precision_ignores_items_below_the_cut() -> None:
    assert precision_at_k([False, True, True, True], 3) == pytest.approx(2 / 3)
    assert precision_at_k([False, False, False, True], 3) == 0.0


def test_precision_with_fewer_than_k_items_still_divides_by_k() -> None:
    # Two shown out of five slots: the empty slots count as misses.
    assert precision_at_k([True, True], 5) == pytest.approx(2 / 5)


def test_precision_with_nothing_relevant_or_nothing_shown_is_zero() -> None:
    assert precision_at_k([False] * 5, 5) == 0.0
    assert precision_at_k([], 5) == 0.0


def test_precision_rejects_a_non_positive_k() -> None:
    with pytest.raises(ValueError):
        precision_at_k([True], 0)


# --- metrics: NDCG@k --------------------------------------------------------------------------


def test_ndcg_of_a_perfect_ranking_is_one() -> None:
    assert ndcg_at_k([True, True, False], 5, total_relevant=2) == pytest.approx(1.0)


def test_ndcg_discounts_relevant_items_found_lower_down() -> None:
    # Hits at ranks 2 and 4; the ideal puts both relevant items at ranks 1 and 2.
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    ideal = DISCOUNT_1 + DISCOUNT_2
    expected = dcg / ideal
    assert expected == pytest.approx(0.6509, abs=1e-4)
    assert ndcg_at_k([False, True, False, True, False], 5, total_relevant=2) == pytest.approx(
        expected
    )


def test_ndcg_ideal_counts_relevant_candidates_the_ranking_missed() -> None:
    # One hit at rank 1, but three relevant candidates exist, so the ideal fills ranks 1 to 3.
    expected = DISCOUNT_1 / (DISCOUNT_1 + DISCOUNT_2 + DISCOUNT_3)
    assert expected == pytest.approx(0.4693, abs=1e-4)
    assert ndcg_at_k([True, False, False, False, False], 5, total_relevant=3) == pytest.approx(
        expected
    )


def test_ndcg_ideal_is_capped_at_k() -> None:
    assert ndcg_at_k([True, True], 2, total_relevant=10) == pytest.approx(1.0)


def test_ndcg_with_fewer_than_k_items() -> None:
    assert ndcg_at_k([False, True], 5, total_relevant=1) == pytest.approx(DISCOUNT_2)


def test_ndcg_ignores_items_below_the_cut() -> None:
    assert ndcg_at_k([False] * 5 + [True], 5, total_relevant=1) == 0.0


def test_ndcg_with_no_relevant_candidates_is_zero() -> None:
    assert ndcg_at_k([False, False], 5, total_relevant=0) == 0.0
    assert ndcg_at_k([], 5, total_relevant=0) == 0.0


def test_ndcg_rejects_inconsistent_inputs() -> None:
    with pytest.raises(ValueError):
        ndcg_at_k([True, True], 5, total_relevant=1)
    with pytest.raises(ValueError):
        ndcg_at_k([True], 0, total_relevant=1)


# --- metrics: constraint rates ----------------------------------------------------------------


def test_constraint_rates_are_shares_of_the_shown_items() -> None:
    shown = [Shown(True, True), Shown(True, False), Shown(False, True), Shown(False, False)]
    assert constraint_rates(shown) == ConstraintRates(
        needed_skill=0.5, enough_overlap=0.5, both=0.25
    )


def test_constraint_rates_both_needs_each_on_the_same_item() -> None:
    rates = constraint_rates([Shown(True, False), Shown(False, True), Shown(True, True)])
    assert rates.needed_skill == pytest.approx(2 / 3)
    assert rates.enough_overlap == pytest.approx(2 / 3)
    assert rates.both == pytest.approx(1 / 3)


def test_constraint_rates_of_nothing_shown_are_zero() -> None:
    assert constraint_rates([]) == ConstraintRates(needed_skill=0.0, enough_overlap=0.0, both=0.0)


# --- the good-match label ---------------------------------------------------------------------


def student(
    *,
    cluster: str = "web-apps",
    has: frozenset[Item] = frozenset(),
    seeking: frozenset[Item] = frozenset(),
    interests: frozenset[Item] = frozenset(),
    goals: frozenset[str] = frozenset(),
    availability: tuple[WeeklySlot, ...] = (),
    user_id: uuid.UUID | None = None,
) -> SyntheticStudent:
    return SyntheticStudent(
        cluster=cluster,
        features=MatchFeatures(
            user_id=user_id or uuid.uuid4(),
            timezone="America/New_York",
            goals=goals,
            has_skills=has,
            seeking_skills=seeking,
            interests=interests,
            availability=availability,
        ),
        text_fields=ProfileTextFields(
            headline="", bio="", goals=(), has_skills=(), seeking_skills=(), interests=()
        ),
    )


def test_label_version_is_recorded() -> None:
    assert LABEL_VERSION == "good-match-v1"
    assert MIN_SHARED_MINUTES == 60


def test_a_good_match_meets_all_three_clauses() -> None:
    viewer = student(seeking=frozenset({PYTHON}))
    candidate = student(has=frozenset({PYTHON, FIGMA}))
    assert is_good_match(viewer, candidate, overlap_minutes=60)


def test_a_different_cluster_is_never_a_good_match() -> None:
    viewer = student(cluster="web-apps", seeking=frozenset({PYTHON}))
    candidate = student(cluster="security", has=frozenset({PYTHON}))
    assert not is_good_match(viewer, candidate, overlap_minutes=600)


def test_a_candidate_without_a_seeking_skill_is_never_a_good_match() -> None:
    viewer = student(has=frozenset({PYTHON}), seeking=frozenset({FIGMA}))
    # Shares a skill the viewer already has, not one they seek.
    candidate = student(has=frozenset({PYTHON, REACT}))
    assert not is_good_match(viewer, candidate, overlap_minutes=600)
    # A viewer who seeks nothing has no good match.
    assert not is_good_match(student(), candidate, overlap_minutes=600)


def test_fewer_than_sixty_shared_minutes_is_never_a_good_match() -> None:
    viewer = student(seeking=frozenset({PYTHON}))
    candidate = student(has=frozenset({PYTHON}))
    assert not is_good_match(viewer, candidate, overlap_minutes=59)
    assert not is_good_match(viewer, candidate, overlap_minutes=0)


def test_label_compares_skills_by_slug() -> None:
    viewer = student(seeking=frozenset({Item(slug="python", name="Python 3")}))
    candidate = student(has=frozenset({PYTHON}))
    assert is_good_match(viewer, candidate, overlap_minutes=60)


# --- the synthetic generator ------------------------------------------------------------------


def _migration_taxonomy() -> tuple[dict[str, str], dict[str, str]]:
    """Slug to name for every skill and interest the migration seeds, read from the migration
    itself so the generator's hard-coded subset can't drift from it."""
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    (path,) = versions.glob("0003_*.py")
    spec = importlib.util.spec_from_file_location("taxonomy_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    skills = {slug: name for slug, name, _category in module.SKILLS}
    interests = {slug: name for slug, name in module.INTERESTS}
    return skills, interests


@pytest.fixture(scope="module")
def population() -> list[SyntheticStudent]:
    return generate(7)


def test_generator_is_deterministic(population: list[SyntheticStudent]) -> None:
    assert generate(7) == population
    assert generate(8) != population


def test_generator_makes_n_students_with_unique_stable_ids(
    population: list[SyntheticStudent],
) -> None:
    assert len(population) == DEFAULT_STUDENTS == 240
    ids = [s.features.user_id for s in population]
    assert len(set(ids)) == len(ids)
    assert [s.features.user_id for s in generate(7, n=SMALL_N)] == ids[:SMALL_N]
    assert len(generate(3, n=SMALL_N)) == SMALL_N


def test_generator_spreads_students_over_the_hidden_clusters(
    population: list[SyntheticStudent],
) -> None:
    assert len(CLUSTERS) == 6
    counts = Counter(s.cluster for s in population)
    assert set(counts) == set(CLUSTERS)
    assert set(counts.values()) == {len(population) // len(CLUSTERS)}


def test_generator_slots_never_overlap(population: list[SyntheticStudent]) -> None:
    for s in population:
        slots = s.features.availability
        assert 1 <= len(slots) <= 5
        for slot in slots:
            assert 0 <= slot.weekday <= 6
            assert 0 <= slot.start_min < slot.end_min <= 1440
        ordered = sorted(slots, key=lambda slot: (slot.weekday, slot.start_min))
        for earlier, later in itertools.pairwise(ordered):
            if earlier.weekday == later.weekday:
                assert earlier.end_min <= later.start_min


def test_generator_uses_the_real_taxonomy(population: list[SyntheticStudent]) -> None:
    skills, interests = _migration_taxonomy()
    for s in population:
        for item in s.features.has_skills | s.features.seeking_skills:
            assert skills[item.slug] == item.name
        for item in s.features.interests:
            assert interests[item.slug] == item.name


def test_generator_profiles_are_complete_and_consistent(
    population: list[SyntheticStudent],
) -> None:
    for s in population:
        features, text = s.features, s.text_fields
        assert features.timezone in TIMEZONES
        assert features.goals and features.goals <= set(GOALS)
        assert 1 <= len(features.has_skills) and 1 <= len(features.seeking_skills)
        assert features.interests
        assert not {i.slug for i in features.has_skills} & {i.slug for i in features.seeking_skills}
        assert 0 < len(text.headline) <= HEADLINE_MAX
        assert 0 < len(text.bio) <= BIO_MAX
        # The text an encoder sees describes the same profile the rules score.
        assert list(text.goals) == [goal for goal in GOALS if goal in features.goals]
        assert sorted(text.has_skills) == sorted(i.name for i in features.has_skills)
        assert sorted(text.seeking_skills) == sorted(i.name for i in features.seeking_skills)
        assert sorted(text.interests) == sorted(i.name for i in features.interests)


def test_good_matches_exist_but_are_a_small_share_of_candidates() -> None:
    # Labels that are never true (or always true) would make every system look the same.
    report = generate_report()
    labels, split = report["labels"], report["split"]
    assert labels["test_viewers_with_relevant"] >= split["test_viewers"] // 2
    assert 1 <= labels["relevant_per_viewer"] <= split["candidates_per_viewer"] / 10


# --- the three systems ------------------------------------------------------------------------


def _uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


WINDOW = OverlapWindow(weekday=0, start_min=600, end_min=720)
NO_OVERLAP = Overlap(minutes=0, longest=None)
TWO_HOURS = Overlap(minutes=120, longest=WINDOW)


def test_rank_drops_unjustifiable_candidates_only_from_the_scored_systems() -> None:
    viewer = student(seeking=frozenset({PYTHON}), interests=frozenset({GAMING}))
    # Nothing in common with the viewer, so no reasons, but the closest by embedding.
    stranger = student(user_id=_uid(1))
    helper = student(user_id=_uid(2), has=frozenset({PYTHON}))
    ranked = rank(
        viewer,
        [
            Candidate(student=stranger, overlap=NO_OVERLAP, cosine=0.9),
            Candidate(student=helper, overlap=TWO_HOURS, cosine=0.1),
        ],
    )
    assert set(ranked) == set(SYSTEMS) == {"semantic_only", "rules_only", "embeddings_plus_rules"}
    assert ranked["semantic_only"] == [_uid(1), _uid(2)]
    assert ranked["rules_only"] == [_uid(2)]
    assert ranked["embeddings_plus_rules"] == [_uid(2)]


def test_rank_uses_interests_for_rules_and_cosine_for_embeddings() -> None:
    viewer = student(seeking=frozenset({PYTHON}), interests=frozenset({GAMING, OPEN_SOURCE}))
    shares_interests = student(
        user_id=_uid(1), has=frozenset({PYTHON}), interests=frozenset({GAMING, OPEN_SOURCE})
    )
    close_text = student(user_id=_uid(2), has=frozenset({PYTHON}))
    candidates = [
        Candidate(student=shares_interests, overlap=TWO_HOURS, cosine=0.1),
        Candidate(student=close_text, overlap=TWO_HOURS, cosine=0.9),
    ]

    # The production score, with the production fallback as the semantic component.
    needed = viewer.features.seeking_skills
    rules_scores = [
        score_pair(
            viewer.features,
            c.student.features,
            needed,
            c.overlap,
            interest_component(viewer.features.interests, c.student.features.interests),
        ).score
        for c in candidates
    ]
    assert rules_scores[0] > rules_scores[1]

    ranked = rank(viewer, candidates)
    assert ranked["rules_only"] == [_uid(1), _uid(2)]
    assert ranked["embeddings_plus_rules"] == [_uid(2), _uid(1)]
    assert ranked["semantic_only"] == [_uid(2), _uid(1)]


def test_rank_breaks_ties_by_user_id() -> None:
    viewer = student(seeking=frozenset({PYTHON}))
    twins = [
        Candidate(
            student=student(user_id=_uid(n), has=frozenset({PYTHON})), overlap=TWO_HOURS, cosine=0.5
        )
        for n in (3, 1, 2)
    ]
    for order in rank(viewer, twins).values():
        assert order == [_uid(1), _uid(2), _uid(3)]


def test_split_is_seeded_half_and_half() -> None:
    students = generate(7, n=SMALL_N)
    test, dev = split_viewers(students, seed=7)
    assert len(test) == SMALL_N // 2
    assert len(dev) == SMALL_N - SMALL_N // 2
    assert {s.features.user_id for s in test}.isdisjoint(s.features.user_id for s in dev)
    assert {s.features.user_id for s in test + dev} == {s.features.user_id for s in students}
    assert split_viewers(students, seed=7) == (test, dev)
    assert split_viewers(students, seed=8)[0] != test


# --- the CLI and the end-to-end report --------------------------------------------------------


def test_cli_defaults() -> None:
    args = parse_args([])
    assert args.seed == 7
    assert args.encoder == "sentence-transformers"
    assert args.k == 5
    assert args.out == DEFAULT_OUT
    assert args.n == DEFAULT_STUDENTS


def test_cli_parses_every_flag() -> None:
    args = parse_args(
        ["--seed", "3", "--encoder", "fake", "--k", "10", "--out", "elsewhere", "--n", "30"]
    )
    assert args.seed == 3
    assert args.encoder == "fake"
    assert args.k == 10
    assert args.out == Path("elsewhere")
    assert args.n == 30


@pytest.mark.parametrize(
    "argv",
    [["--encoder", "openai"], ["--k", "0"], ["--k", "many"], ["--n", "1"], ["--seed", "x"]],
)
def test_cli_rejects_bad_flags(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(argv)
    assert excinfo.value.code == 2


def test_model_slug() -> None:
    assert model_slug("sentence-transformers/all-MiniLM-L6-v2") == "all-minilm-l6-v2"
    assert model_slug(FakeEncoder.model_version) == "fake-v1"


def test_report_stem_names_everything_that_changes_the_numbers() -> None:
    # The committed reference report's name.
    assert (
        report_stem("sentence-transformers/all-MiniLM-L6-v2", seed=7, n=240, k=5)
        == "all-minilm-l6-v2-seed7-n240-k5"
    )
    assert report_stem("fake-v1", seed=3, n=60, k=10) == "fake-v1-seed3-n60-k10"


def test_reference_now_is_fixed_and_timezone_aware() -> None:
    assert REFERENCE_NOW == datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
    assert REFERENCE_NOW.utcoffset() is not None


@functools.cache
def generate_report() -> dict[str, Any]:
    """The default-size fake-encoder report, as its JSON reads back, shared by the tests that
    only inspect it."""
    report = evaluate(seed=7, encoder=FakeEncoder(), encoder_name="fake", k=5)
    loaded: dict[str, Any] = json.loads(report_json(report))
    return loaded


def test_end_to_end_with_the_fake_encoder_is_byte_identical(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The CLI defaults, apart from the encoder: what CI can afford to run.
    argv = ["--seed", "7", "--encoder", "fake"]
    assert main([*argv, "--out", str(tmp_path / "first")]) == 0
    printed = capsys.readouterr().out
    assert main([*argv, "--out", str(tmp_path / "second")]) == 0

    stem = "fake-v1-seed7-n240-k5"
    assert sorted(path.name for path in (tmp_path / "first").iterdir()) == [
        f"{stem}.json",
        f"{stem}.md",
    ]
    for suffix in (".json", ".md"):
        first = (tmp_path / "first" / f"{stem}{suffix}").read_bytes()
        second = (tmp_path / "second" / f"{stem}{suffix}").read_bytes()
        assert first == second
    markdown = (tmp_path / "first" / f"{stem}.md").read_text()
    assert printed.strip() == markdown.strip()

    text = (tmp_path / "first" / f"{stem}.json").read_text()
    report = json.loads(text)
    # Sorted keys and a stable layout, so the file diffs cleanly between runs.
    assert text == json.dumps(report, sort_keys=True, indent=2) + "\n"

    assert report["model_version"] == "fake-v1"
    assert report["encoder"] == "fake"
    assert report["seed"] == 7
    assert report["k"] == 5
    assert report["reference_now"] == "2026-09-07T12:00:00+00:00"
    assert report["source"] == {
        "data": "synthetic: experiments/synthetic.py",
        "n": DEFAULT_STUDENTS,
        "clusters": list(CLUSTERS),
    }
    assert report["label_definition"]["version"] == LABEL_VERSION
    assert report["label_definition"]["summary"]
    assert report["split"] == {
        "test_viewers": DEFAULT_STUDENTS // 2,
        "dev_viewers": DEFAULT_STUDENTS - DEFAULT_STUDENTS // 2,
        "candidates_per_viewer": DEFAULT_STUDENTS - 1,
    }
    assert report["weights"] == dataclasses.asdict(WEIGHTS)
    assert report["availability_target_minutes"] == 240


def test_a_non_default_k_or_n_never_overwrites_the_default_report(tmp_path: Path) -> None:
    # Numbers made with another k or n can't be compared with the defaults, so they must land
    # in a file of their own rather than replace the default one.
    assert main(["--encoder", "fake", "--out", str(tmp_path)]) == 0
    default_json = (tmp_path / "fake-v1-seed7-n240-k5.json").read_bytes()
    default_md = (tmp_path / "fake-v1-seed7-n240-k5.md").read_bytes()

    assert main(["--encoder", "fake", "--k", "3", "--n", "60", "--out", str(tmp_path)]) == 0

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "fake-v1-seed7-n240-k5.json",
        "fake-v1-seed7-n240-k5.md",
        "fake-v1-seed7-n60-k3.json",
        "fake-v1-seed7-n60-k3.md",
    ]
    assert (tmp_path / "fake-v1-seed7-n240-k5.json").read_bytes() == default_json
    assert (tmp_path / "fake-v1-seed7-n240-k5.md").read_bytes() == default_md
    other = json.loads((tmp_path / "fake-v1-seed7-n60-k3.json").read_text())
    assert (other["k"], other["source"]["n"]) == (3, 60)


def test_end_to_end_reports_all_three_systems() -> None:
    report = generate_report()
    assert set(report["systems"]) == set(SYSTEMS)
    for result in report["systems"].values():
        assert 0.0 <= result["precision_at_k"] <= 1.0
        assert 0.0 <= result["ndcg_at_k"] <= 1.0
        assert 0.0 <= result["mean_shown"] <= 5.0
        assert set(result["constraint_rates"]) == {"needed_skill", "enough_overlap", "both"}
        for rate in result["constraint_rates"].values():
            assert 0.0 <= rate <= 1.0


def test_end_to_end_reports_the_best_possible_scores() -> None:
    report = generate_report()
    labels = report["labels"]
    # A perfect ranker scores 1 on every viewer with a good match to find, and 0 on the rest.
    assert labels["best_ndcg_at_k"] == pytest.approx(
        labels["test_viewers_with_relevant"] / report["split"]["test_viewers"], abs=1e-4
    )
    assert 0.0 < labels["best_precision_at_k"] <= 1.0
    for result in report["systems"].values():
        assert result["precision_at_k"] <= labels["best_precision_at_k"]
        assert result["ndcg_at_k"] <= labels["best_ndcg_at_k"]


def test_end_to_end_rounds_every_number_to_four_places() -> None:
    def floats(value: object) -> list[float]:
        if isinstance(value, float):
            return [value]
        if isinstance(value, dict):
            return [f for v in value.values() for f in floats(v)]
        if isinstance(value, list):
            return [f for v in value for f in floats(v)]
        return []

    found = floats(generate_report())
    assert found
    assert all(f == round(f, 4) for f in found)


def test_real_encoder_without_the_ml_extra_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A None entry makes `import sentence_transformers` fail, as it does without the extra.
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    out = tmp_path / "reports"
    code = main(["--encoder", "sentence-transformers", "--out", str(out), "--n", str(SMALL_N)])
    assert code != 0
    err = capsys.readouterr().err
    assert ".[ml]" in err
    assert "--encoder fake" in err
    assert "Traceback" not in err
    assert not out.exists()
