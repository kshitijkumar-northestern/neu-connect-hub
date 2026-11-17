"""`profile_text` and `source_hash`: pure, and never touch anything but the fields a student
chose to make public."""

import hashlib

import pytest

from app.ml.profile_text import GOAL_LABELS, ProfileTextFields, profile_text, source_hash
from app.modules.profiles.models import GOALS


def fields(**overrides: object) -> ProfileTextFields:
    base: dict[str, object] = {
        "headline": "Analytical engines and poetry",
        "bio": "Looking for a team to build a music generator.",
        "goals": ["research", "project_development"],
        "has_skills": ["Python", "Machine learning"],
        "seeking_skills": ["UI design"],
        "interests": ["Music and arts", "Education"],
    }
    base.update(overrides)
    return ProfileTextFields(**base)  # type: ignore[arg-type]


def test_exact_output_for_a_fixture() -> None:
    assert profile_text(fields()) == (
        "Headline: Analytical engines and poetry\n"
        "Bio: Looking for a team to build a music generator.\n"
        "Goals: Building a project, Doing research\n"  # GOALS canonical order, not input order
        "Skills: Machine learning, Python\n"
        "Seeking: UI design\n"
        "Interests: Education, Music and arts"
    )


def test_every_goal_has_a_human_label_not_its_slug() -> None:
    assert set(GOAL_LABELS) == set(GOALS)
    for slug, label in GOAL_LABELS.items():
        assert label != slug
        assert "_" not in label


def test_goals_are_rendered_in_canonical_order_regardless_of_input_order() -> None:
    forward = profile_text(fields(goals=["studying", "mentorship"]))
    reversed_input = profile_text(fields(goals=["mentorship", "studying"]))
    assert forward == reversed_input
    assert "Goals: Studying together, Mentorship" in forward


@pytest.mark.parametrize(
    ("key", "unsorted", "sorted_line"),
    [
        (
            "has_skills",
            ["Python", "APIs", "machine learning"],
            "Skills: APIs, machine learning, Python",
        ),
        ("interests", ["Space", "education"], "Interests: education, Space"),
        ("seeking_skills", ["UI design", "Backend"], "Seeking: Backend, UI design"),
    ],
)
def test_skills_and_interests_are_sorted_by_name_casefolded(
    key: str, unsorted: list[str], sorted_line: str
) -> None:
    text = profile_text(fields(**{key: unsorted}))
    assert sorted_line in text.splitlines()


def test_empty_fields_are_simply_absent_not_blank_labels() -> None:
    text = profile_text(
        ProfileTextFields(
            headline="", bio="", goals=[], has_skills=[], seeking_skills=[], interests=[]
        )
    )
    assert text == ""


def test_only_the_present_parts_appear_in_order() -> None:
    text = profile_text(fields(bio="", seeking_skills=[]))
    assert text.splitlines() == [
        "Headline: Analytical engines and poetry",
        "Goals: Building a project, Doing research",
        "Skills: Machine learning, Python",
        "Interests: Education, Music and arts",
    ]


# --- source_hash ------------------------------------------------------------------------------


def test_source_hash_matches_the_documented_formula() -> None:
    assert (
        source_hash("fake-v1", "Headline: Hi")
        == hashlib.sha256(b"fake-v1\nHeadline: Hi").hexdigest()
    )


def test_source_hash_changes_with_the_text_or_the_model_version() -> None:
    base = source_hash("fake-v1", "Headline: Hi")
    assert source_hash("fake-v1", "Headline: Bye") != base
    assert source_hash("fake-v2", "Headline: Hi") != base


def test_source_hash_is_deterministic() -> None:
    assert source_hash("fake-v1", "Headline: Hi") == source_hash("fake-v1", "Headline: Hi")
