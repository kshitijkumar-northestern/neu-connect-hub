"""The exact text handed to an encoder, built only from fields a student chose to make public.

Never included: email, display name, timezone, availability, messages, calls, projects,
connections, reports or blocks. Only a profile's headline, bio, goals, skills and interests ever
reach this module, and only for profiles that are `discoverable`.
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.modules.profiles.models import GOALS

# Action-oriented labels, never a naive title-case of the slug. Mirrors
# apps/web/src/features/profiles/constants.ts GOAL_LABELS, so the text a human reads and the text
# the encoder sees agree.
GOAL_LABELS: dict[str, str] = {
    "studying": "Studying together",
    "project_development": "Building a project",
    "research": "Doing research",
    "mentorship": "Mentorship",
}


@dataclass(frozen=True)
class ProfileTextFields:
    """Only what `profile_text` may see. `goals` holds slugs from `GOALS`; the skill and interest
    lists hold display names, not slugs."""

    headline: str
    bio: str
    goals: Sequence[str]
    has_skills: Sequence[str]
    seeking_skills: Sequence[str]
    interests: Sequence[str]


def profile_text(fields: ProfileTextFields) -> str:
    """A deterministic, labelled rendering of the fields above: the same profile always produces
    the same text, regardless of the order its goals, skills or interests were stored in."""
    present_goals = set(fields.goals)
    goal_labels = [GOAL_LABELS[goal] for goal in GOALS if goal in present_goals]
    parts = (
        _part("Headline", fields.headline),
        _part("Bio", fields.bio),
        _joined("Goals", goal_labels),  # GOALS order is already canonical; never re-sorted
        _labelled("Skills", fields.has_skills),
        _labelled("Seeking", fields.seeking_skills),
        _labelled("Interests", fields.interests),
    )
    return "\n".join(part for part in parts if part is not None)


def _part(label: str, value: str) -> str | None:
    return f"{label}: {value}" if value else None


def _joined(label: str, names: Sequence[str]) -> str | None:
    return f"{label}: {', '.join(names)}" if names else None


def _labelled(label: str, names: Iterable[str]) -> str | None:
    """Like `_joined`, but sorts first: skills and interests must be deterministic regardless
    of the order they were stored in, since that order isn't otherwise meaningful."""
    return _joined(label, sorted(names, key=str.casefold))


def source_hash(model_version: str, text: str) -> str:
    """SHA-256 of the model version and the text, so an unchanged profile under the same model
    is never re-encoded."""
    return hashlib.sha256(f"{model_version}\n{text}".encode()).hexdigest()
