"""Ranking metrics for the offline evaluation, with binary relevance (a candidate is a good match
or it isn't). Pure functions over plain booleans, so each one can be checked by hand."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple


def _check_k(k: int) -> None:
    if k < 1:
        raise ValueError("k must be at least 1")


def precision_at_k(relevance: Sequence[bool], k: int) -> float:
    """The share of the top k slots holding a good match. `relevance` is a ranking, best first.
    A ranking shorter than k still divides by k: a slot the viewer is never shown can't help
    them, so a system that drops candidates is charged for the gap."""
    _check_k(k)
    return sum(1 for relevant in relevance[:k] if relevant) / k


def _discount(rank: int) -> float:
    """The log2 position discount for a 1-based rank: 1 at rank 1, then 1/log2(rank + 1)."""
    return 1 / math.log2(rank + 1)


def ndcg_at_k(relevance: Sequence[bool], k: int, total_relevant: int) -> float:
    """Normalised discounted cumulative gain at k. The ideal ranking is built from every relevant
    candidate in the pool (`total_relevant`), not only from those this ranking surfaced, so
    missing a good match costs as much as burying it. 0 when the pool has no good match."""
    _check_k(k)
    found = sum(1 for relevant in relevance[:k] if relevant)
    if total_relevant < found:
        raise ValueError("total_relevant can't be smaller than the relevant items ranked")
    if total_relevant == 0:
        return 0.0
    dcg = sum(_discount(rank) for rank, relevant in enumerate(relevance[:k], 1) if relevant)
    ideal = sum(_discount(rank) for rank in range(1, min(k, total_relevant) + 1))
    return dcg / ideal


class Shown(NamedTuple):
    """One recommendation a system put in a viewer's top k, by the two constraints of a good
    match that a ranker can actually see."""

    has_needed_skill: bool
    enough_overlap: bool


@dataclass(frozen=True)
class ConstraintRates:
    """Shares of shown recommendations meeting each constraint, and both at once."""

    needed_skill: float
    enough_overlap: float
    both: float


def constraint_rates(shown: Sequence[Shown]) -> ConstraintRates:
    """Pooled over every recommendation shown (to every viewer), so a viewer shown fewer
    recommendations weighs less; all 0 when nothing was shown."""
    if not shown:
        return ConstraintRates(needed_skill=0.0, enough_overlap=0.0, both=0.0)
    total = len(shown)
    return ConstraintRates(
        needed_skill=sum(1 for item in shown if item.has_needed_skill) / total,
        enough_overlap=sum(1 for item in shown if item.enough_overlap) / total,
        both=sum(1 for item in shown if item.has_needed_skill and item.enough_overlap) / total,
    )
