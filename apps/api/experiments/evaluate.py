"""One command that reproduces the matcher evaluation report:

    python -m experiments.evaluate [--seed 7] [--encoder sentence-transformers|fake] [--k 5]
                                   [--out experiments/reports] [--n 240]

It generates labelled synthetic students, keeps half of them as test viewers, and ranks every
other student for each test viewer with three systems, all scored by the production code:

- `semantic_only`: cosine similarity of the two profile texts' embeddings, nothing else;
- `rules_only`: `score_pair` with the interest-overlap fallback as its semantic component, which
  is what production does when an embedding is missing;
- `embeddings_plus_rules`: `score_pair` with the embedding similarity, which is what production
  does when both embeddings are fresh.

It writes `<out>/<model>-seed<seed>-n<n>-k<k>.json` and `.md` and prints the Markdown. The same
flags give a byte-identical JSON file: nothing in it depends on the clock or on the order anything
was stored in, and every number is rounded.
"""

import argparse
import dataclasses
import json
import math
import random
import re
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from app.core.config import Settings
from app.ml.availability import overlap_with, viewer_week
from app.ml.encoders import Encoder, FakeEncoder, SentenceTransformerEncoder, validate_batch
from app.ml.profile_text import profile_text
from app.ml.scoring import (
    AVAILABILITY_TARGET_MINUTES,
    WEIGHTS,
    Overlap,
    Weights,
    interest_component,
    score_pair,
    semantic_component,
)
from app.modules.discovery.ranker import MAX_RECOMMENDATIONS
from experiments.labels import (
    LABEL_SUMMARY,
    LABEL_VERSION,
    MIN_SHARED_MINUTES,
    enough_overlap,
    has_needed_skill,
    is_good_match,
)
from experiments.metrics import ConstraintRates, Shown, constraint_rates, ndcg_at_k, precision_at_k
from experiments.synthetic import CLUSTERS, DEFAULT_STUDENTS, SyntheticStudent, generate

# Availability is measured over the viewer's week from this instant (a Monday), never the clock,
# so the report doesn't change with the day it's run.
REFERENCE_NOW: Final = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

SYSTEMS: Final = ("semantic_only", "rules_only", "embeddings_plus_rules")
ENCODERS: Final = ("sentence-transformers", "fake")

DEFAULT_SEED: Final = 7
# Discover shows this many matches, so that's the cut that matters to a student.
DEFAULT_K: Final = MAX_RECOMMENDATIONS
DEFAULT_OUT: Final = Path(__file__).resolve().parent / "reports"

SOURCE: Final = "synthetic: experiments/synthetic.py"
DECIMALS: Final = 4

# The share of students reported on as viewers. The rest are the dev set: kept back for anyone
# tuning weights or the generator, so tuning never happens on the numbers being reported.
TEST_SHARE: Final = 0.5


class EvaluationError(Exception):
    """The run can't go ahead; the message says why and what to do instead."""


@dataclass(frozen=True)
class Candidate:
    """Another student as one viewer's systems see them: their profile, the pair's overlap in
    the viewer's reference week, and the cosine similarity of their two profile embeddings."""

    student: SyntheticStudent
    overlap: Overlap
    cosine: float


@dataclass(frozen=True)
class Source:
    data: str
    n: int
    clusters: tuple[str, ...]


@dataclass(frozen=True)
class LabelDefinition:
    version: str
    summary: str


@dataclass(frozen=True)
class Split:
    test_viewers: int
    dev_viewers: int
    candidates_per_viewer: int


@dataclass(frozen=True)
class LabelStats:
    """How many good matches there were to find, and what a perfect ranker would score with
    them, so the metrics can be read in proportion: a viewer with two good matches caps P@5 at
    0.4, and one with none scores 0 under every system."""

    relevant_per_viewer: float
    test_viewers_with_relevant: int
    best_precision_at_k: float
    best_ndcg_at_k: float


@dataclass(frozen=True)
class SystemResult:
    """Means over test viewers, except the constraint rates, which pool every shown
    recommendation."""

    precision_at_k: float
    ndcg_at_k: float
    mean_shown: float
    constraint_rates: ConstraintRates


@dataclass(frozen=True)
class Report:
    model_version: str
    encoder: str
    seed: int
    source: Source
    label_definition: LabelDefinition
    split: Split
    k: int
    reference_now: str
    weights: Weights
    availability_target_minutes: int
    labels: LabelStats
    systems: dict[str, SystemResult]


# --- ranking ----------------------------------------------------------------------------------


def rank(viewer: SyntheticStudent, candidates: Sequence[Candidate]) -> dict[str, list[uuid.UUID]]:
    """Each system's ordering of the candidates, best first, ties broken by user id as the
    ranker breaks them. The two `score_pair` systems drop a candidate with no reasons, exactly
    like the ranker: a match that can't be explained is never shown."""
    needed = viewer.features.seeking_skills
    semantic_only: list[tuple[float, uuid.UUID]] = []
    rules_only: list[tuple[float, uuid.UUID]] = []
    embeddings_plus_rules: list[tuple[float, uuid.UUID]] = []

    for candidate in candidates:
        features = candidate.student.features
        semantic_only.append((candidate.cosine, features.user_id))

        fallback = interest_component(viewer.features.interests, features.interests)
        rules = score_pair(viewer.features, features, needed, candidate.overlap, fallback)
        if rules.reasons:
            rules_only.append((rules.score, features.user_id))

        embedded = semantic_component(candidate.cosine)
        both = score_pair(viewer.features, features, needed, candidate.overlap, embedded)
        if both.reasons:
            embeddings_plus_rules.append((both.score, features.user_id))

    return {
        "semantic_only": _best_first(semantic_only),
        "rules_only": _best_first(rules_only),
        "embeddings_plus_rules": _best_first(embeddings_plus_rules),
    }


def _best_first(scored: list[tuple[float, uuid.UUID]]) -> list[uuid.UUID]:
    return [user_id for _score, user_id in sorted(scored, key=lambda pair: (-pair[0], pair[1]))]


def _cosine(a: Sequence[float], b: Sequence[float], norm_a: float, norm_b: float) -> float:
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return math.sumprod(a, b) / (norm_a * norm_b)


# --- the run ----------------------------------------------------------------------------------


def split_viewers(
    students: Sequence[SyntheticStudent], seed: int
) -> tuple[list[SyntheticStudent], list[SyntheticStudent]]:
    """A seeded shuffle, then the first half are test viewers and the rest dev. Its own random
    stream (derived from the seed), so changing how the data is generated never silently
    reshuffles which viewers are tested."""
    order = list(students)
    random.Random(f"split/{seed}").shuffle(order)  # noqa: S311 - a reproducible split
    cut = int(len(order) * TEST_SHARE)
    return order[:cut], order[cut:]


def evaluate(
    *, seed: int, encoder: Encoder, encoder_name: str, k: int, n: int = DEFAULT_STUDENTS
) -> Report:
    """Generates the data, encodes every profile once, and scores every test viewer against
    every other student with each system."""
    if k < 1:
        raise ValueError("k must be at least 1")
    if n < 2:
        raise ValueError("n must be at least 2")

    students = generate(seed, n)
    test, dev = split_viewers(students, seed)

    texts = [profile_text(student.text_fields) for student in students]
    vectors = encoder.encode(texts)
    if not validate_batch(vectors, len(texts)):
        raise EvaluationError(f"the {encoder.model_version} encoder returned an unusable batch")
    vector_by_id = {
        student.features.user_id: (vector, math.sqrt(math.sumprod(vector, vector)))
        for student, vector in zip(students, vectors, strict=True)
    }

    precision: dict[str, list[float]] = {system: [] for system in SYSTEMS}
    ndcg: dict[str, list[float]] = {system: [] for system in SYSTEMS}
    shown_counts: dict[str, list[int]] = {system: [] for system in SYSTEMS}
    shown: dict[str, list[Shown]] = {system: [] for system in SYSTEMS}
    relevant_counts: list[int] = []
    best_precision: list[float] = []
    best_ndcg: list[float] = []

    for viewer in test:
        viewer_id = viewer.features.user_id
        viewer_vector, viewer_norm = vector_by_id[viewer_id]
        # The viewer's week is laid out once; each candidate is then only intersected with it.
        week = viewer_week(
            viewer.features.availability, viewer.features.timezone, now=REFERENCE_NOW
        )

        candidates: list[Candidate] = []
        relevant: dict[uuid.UUID, bool] = {}
        constraints: dict[uuid.UUID, Shown] = {}
        for other in students:
            other_id = other.features.user_id
            if other_id == viewer_id:
                continue
            overlap = overlap_with(week, other.features.availability, other.features.timezone)
            other_vector, other_norm = vector_by_id[other_id]
            cosine = _cosine(viewer_vector, other_vector, viewer_norm, other_norm)
            candidates.append(Candidate(student=other, overlap=overlap, cosine=cosine))
            relevant[other_id] = is_good_match(viewer, other, overlap_minutes=overlap.minutes)
            constraints[other_id] = Shown(
                has_needed_skill=has_needed_skill(viewer, other),
                enough_overlap=enough_overlap(overlap.minutes),
            )

        total_relevant = sum(relevant.values())
        relevant_counts.append(total_relevant)
        ideal = [True] * total_relevant
        best_precision.append(precision_at_k(ideal, k))
        best_ndcg.append(ndcg_at_k(ideal, k, total_relevant))
        for system, order in rank(viewer, candidates).items():
            hits = [relevant[user_id] for user_id in order]
            precision[system].append(precision_at_k(hits, k))
            ndcg[system].append(ndcg_at_k(hits, k, total_relevant))
            top = order[:k]
            shown_counts[system].append(len(top))
            shown[system].extend(constraints[user_id] for user_id in top)

    return Report(
        model_version=encoder.model_version,
        encoder=encoder_name,
        seed=seed,
        source=Source(data=SOURCE, n=n, clusters=CLUSTERS),
        label_definition=LabelDefinition(version=LABEL_VERSION, summary=LABEL_SUMMARY),
        split=Split(test_viewers=len(test), dev_viewers=len(dev), candidates_per_viewer=n - 1),
        k=k,
        reference_now=REFERENCE_NOW.isoformat(),
        weights=WEIGHTS,
        availability_target_minutes=AVAILABILITY_TARGET_MINUTES,
        labels=LabelStats(
            relevant_per_viewer=_mean(relevant_counts),
            test_viewers_with_relevant=sum(1 for count in relevant_counts if count > 0),
            best_precision_at_k=_mean(best_precision),
            best_ndcg_at_k=_mean(best_ndcg),
        ),
        systems={
            system: SystemResult(
                precision_at_k=_mean(precision[system]),
                ndcg_at_k=_mean(ndcg[system]),
                mean_shown=_mean(shown_counts[system]),
                constraint_rates=constraint_rates(shown[system]),
            )
            for system in SYSTEMS
        },
    )


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# --- output -----------------------------------------------------------------------------------


def _rounded(value: Any) -> Any:
    """Every float rounded to `DECIMALS` places, all the way down, so tiny floating-point noise
    can't make two runs' files differ."""
    if isinstance(value, float):
        return round(value, DECIMALS)
    if isinstance(value, dict):
        return {key: _rounded(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_rounded(item) for item in value]
    return value


def report_json(report: Report) -> str:
    """Sorted keys and a fixed indent, so a re-run with the same seed diffs as empty."""
    return json.dumps(_rounded(dataclasses.asdict(report)), sort_keys=True, indent=2) + "\n"


def _number(value: float) -> str:
    return f"{round(value, DECIMALS):.{DECIMALS}f}"


def render_markdown(report: Report) -> str:
    """The same numbers as the JSON, as a table someone can read in a pull request."""
    k = report.k
    lines = [
        f"# Matcher evaluation: {report.model_version}, seed {report.seed}",
        "",
        f"| System | P@{k} | NDCG@{k} | Needed skill | {MIN_SHARED_MINUTES}+ shared minutes"
        " | Both | Shown per viewer |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for system in SYSTEMS:
        result = report.systems[system]
        rates = result.constraint_rates
        cells = (
            result.precision_at_k,
            result.ndcg_at_k,
            rates.needed_skill,
            rates.enough_overlap,
            rates.both,
            result.mean_shown,
        )
        lines.append(f"| `{system}` | " + " | ".join(_number(cell) for cell in cells) + " |")

    weights = report.weights
    split = report.split
    labels = report.labels
    lines += [
        "",
        f"- **Model:** `{report.model_version}` (encoder `{report.encoder}`)",
        f"- **Data:** {report.source.data}, {report.source.n} students in"
        f" {len(report.source.clusters)} hidden clusters ({', '.join(report.source.clusters)})",
        f"- **Labels:** `{report.label_definition.version}`: {report.label_definition.summary}",
        f"- **Split:** {split.test_viewers} test viewers (reported), {split.dev_viewers} dev"
        f" viewers (reserved, not reported), {split.candidates_per_viewer} candidates per viewer",
        f"- **Good matches:** {_number(labels.relevant_per_viewer)} per test viewer on"
        f" average; {labels.test_viewers_with_relevant} of {split.test_viewers} test viewers"
        f" have at least one, so a perfect ranker would score P@{k}"
        f" {_number(labels.best_precision_at_k)} and NDCG@{k} {_number(labels.best_ndcg_at_k)}",
        f"- **Reference now:** {report.reference_now}",
        f"- **Weights:** semantic {weights.semantic}, complementary {weights.complementary},"
        f" availability {weights.availability}, goal {weights.goal}; availability saturates at"
        f" {report.availability_target_minutes} shared minutes",
        "",
        "The data is synthetic and the labels are our own definition, so the absolute numbers"
        " mean little: compare the systems with each other.",
    ]
    return "\n".join(lines) + "\n"


def model_slug(model_version: str) -> str:
    """The file-name form of a model version: its last path segment, lower-cased, with every
    run of other characters turned into one hyphen."""
    name = model_version.rsplit("/", 1)[-1].lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def report_stem(model_version: str, *, seed: int, n: int, k: int) -> str:
    """The report's file name without its extension. It names every input that changes the
    numbers, so a run with another seed, population size or cut lands beside the reference
    report instead of silently replacing it with numbers that can't be compared."""
    return f"{model_slug(model_version)}-seed{seed}-n{n}-k{k}"


# --- the command line -------------------------------------------------------------------------


def _shipped_model_id() -> str:
    """The model production uses by default. Read from the settings' declared default rather
    than the environment, so a local override can't change what the reference report means."""
    return str(Settings.model_fields["embedding_model_id"].default)


def load_encoder(name: str) -> Encoder:
    """The encoder for a `--encoder` value. The real one needs the optional `ml` extra; without
    it this raises `EvaluationError` with the install command instead of a traceback."""
    if name == "fake":
        return FakeEncoder()
    model_id = _shipped_model_id()
    try:
        return SentenceTransformerEncoder(model_id)
    except ImportError as exc:
        raise EvaluationError(
            "the sentence-transformers encoder needs the optional ml extra. From apps/api, run\n"
            '    pip install -c requirements.txt -e ".[ml]"\n'
            "or use --encoder fake, a deterministic stand-in whose vectors carry no meaning."
        ) from exc
    except Exception as exc:
        # Offline with no cached model, a bad model id, ...: say so plainly, as above.
        raise EvaluationError(f"the {model_id} model failed to load: {exc}") from exc


def _at_least(minimum: int) -> Callable[[str], int]:
    def parse(raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"{raw!r} is not a whole number") from None
        if value < minimum:
            raise argparse.ArgumentTypeError(f"must be at least {minimum}, got {value}")
        return value

    return parse


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m experiments.evaluate",
        description="Compare rules-only and embedding matching on labelled synthetic students.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="data and split seed")
    parser.add_argument(
        "--encoder",
        choices=ENCODERS,
        default="sentence-transformers",
        help="the real model (needs the ml extra) or the deterministic fake",
    )
    parser.add_argument("--k", type=_at_least(1), default=DEFAULT_K, help="ranks to score")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="report directory")
    parser.add_argument(
        "--n", type=_at_least(2), default=DEFAULT_STUDENTS, help="synthetic students"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        encoder = load_encoder(args.encoder)
        report = evaluate(
            seed=args.seed, encoder=encoder, encoder_name=args.encoder, k=args.k, n=args.n
        )
    except EvaluationError as exc:
        print(f"experiments.evaluate: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    stem = report_stem(report.model_version, seed=report.seed, n=report.source.n, k=report.k)
    (out / f"{stem}.json").write_text(report_json(report), encoding="utf-8", newline="\n")
    (out / f"{stem}.md").write_text(markdown, encoding="utf-8", newline="\n")
    sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
