"""What counts as a good match, written down before any system is scored against it.

A candidate is a good match for a viewer when all three hold:

1. They share the viewer's hidden topic cluster: they'd want to work on the same kind of thing.
2. They have at least one skill the viewer is seeking: they bring something the viewer lacks.
3. They share at least an hour in the viewer's coming week: they can actually meet.

The README explains why each clause is there. Change the definition only together with
`LABEL_VERSION`, so reports made under different definitions are never compared by mistake.
"""

from typing import Final

from experiments.synthetic import SyntheticStudent

LABEL_VERSION: Final = "good-match-v1"

# One shared hour a week: the least time in which a pair can hold a real working session.
MIN_SHARED_MINUTES: Final = 60

LABEL_SUMMARY: Final = (
    "same hidden topic cluster, the candidate has at least one of the viewer's seeking skills, "
    f"and at least {MIN_SHARED_MINUTES} shared minutes in the viewer's reference week"
)


def has_needed_skill(viewer: SyntheticStudent, candidate: SyntheticStudent) -> bool:
    """Whether the candidate has at least one skill the viewer is seeking, compared by slug as
    production scoring does."""
    seeking = {item.slug for item in viewer.features.seeking_skills}
    return any(item.slug in seeking for item in candidate.features.has_skills)


def enough_overlap(overlap_minutes: int) -> bool:
    return overlap_minutes >= MIN_SHARED_MINUTES


def is_good_match(
    viewer: SyntheticStudent, candidate: SyntheticStudent, *, overlap_minutes: int
) -> bool:
    """The label: all three clauses at once. `overlap_minutes` is the pair's shared time in the
    viewer's week from the reference `now`, computed once by the caller with production code."""
    return (
        viewer.cluster == candidate.cluster
        and has_needed_skill(viewer, candidate)
        and enough_overlap(overlap_minutes)
    )
