"""Synthetic students for the offline evaluation: deterministic for a seed, built from the real
skill and interest taxonomy, and grouped into hidden topic clusters.

Each student belongs to one cluster (say, game development). The cluster shapes their headline
and bio wording, the skills they have and seek, and their interests, each with noise, but it is
never handed to a ranker: rankers see only `features` (what the production scorer reads) and
`text_fields` (what the production encoder reads). The cluster is the ground truth the label
uses to say two students would actually want to work together.
"""

import random
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.ml.profile_text import ProfileTextFields
from app.ml.scoring import Item, MatchFeatures, WeeklySlot
from app.modules.profiles.models import GOALS

DEFAULT_STUDENTS: Final = 240

# Fixed, so student ids (and with them every tie-break) are the same on every machine.
_NAMESPACE: Final = uuid.UUID("6f1c2b9e-3d4a-5e8f-9a7b-1c2d3e4f5a6b")

# The subset of the taxonomy seeded by migration 0003 that the clusters draw on, slug to name,
# copied exactly. A test checks each pair against the migration.
SKILL_NAMES: Final[dict[str, str]] = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "java": "Java",
    "c": "C",
    "cpp": "C++",
    "csharp": "C#",
    "go": "Go",
    "rust": "Rust",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "r": "R",
    "sql": "SQL",
    "matlab": "MATLAB",
    "react": "React",
    "nodejs": "Node.js",
    "html-css": "HTML and CSS",
    "rest-apis": "REST APIs",
    "ios": "iOS development",
    "android": "Android development",
    "machine-learning": "Machine learning",
    "deep-learning": "Deep learning",
    "nlp": "Natural language processing",
    "computer-vision": "Computer vision",
    "data-analysis": "Data analysis",
    "data-visualization": "Data visualization",
    "statistics": "Statistics",
    "databases": "Databases",
    "cloud-computing": "Cloud computing",
    "devops": "DevOps",
    "docker": "Docker",
    "linux": "Linux",
    "cybersecurity": "Cybersecurity",
    "ui-design": "UI design",
    "ux-research": "UX research",
    "figma": "Figma",
    "project-management": "Project management",
    "technical-writing": "Technical writing",
    "public-speaking": "Public speaking",
    "research-methods": "Research methods",
}

INTEREST_NAMES: Final[dict[str, str]] = {
    "accessibility": "Accessibility",
    "ai-ethics": "AI ethics",
    "climate-and-sustainability": "Climate and sustainability",
    "cybersecurity": "Cybersecurity",
    "e-commerce": "E-commerce",
    "education": "Education",
    "fintech": "Fintech",
    "gaming": "Gaming",
    "hackathons": "Hackathons",
    "healthcare": "Healthcare",
    "media-and-journalism": "Media and journalism",
    "mobility": "Mobility and transport",
    "music-and-arts": "Music and arts",
    "open-source": "Open source",
    "robotics": "Robotics",
    "scientific-research": "Scientific research",
    "social-impact": "Social impact",
    "space": "Space",
    "sports-and-fitness": "Sports and fitness",
    "startups": "Startups",
}

# Mostly Boston, with the other campuses and students studying from home. Neighbouring US zones
# can still share an evening; London and Kolkata rarely can with anyone in the Americas.
TIMEZONES: Final = (
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Asia/Kolkata",
)
_TIMEZONE_WEIGHTS: Final = (70, 5, 10, 5, 10)

# How strongly a student's profile follows their cluster. The rest is drawn from everything, so
# no single field gives the cluster away.
_TOPICAL_TEXT_RATE: Final = 0.8
_CLUSTER_SKILL_RATE: Final = 0.8
_CLUSTER_INTEREST_RATE: Final = 0.7
_GOAL_RATE: Final = 0.5

_HAS_SKILLS: Final = (2, 4)
_SEEKING_SKILLS: Final = (1, 3)
_INTERESTS: Final = (1, 4)
_SLOTS: Final = (1, 5)

# Weekly availability, in local time: the hours students tend to offer, and how long for.
_SLOT_STARTS: Final = (540, 600, 720, 840, 960, 1080, 1140, 1200)  # 09:00 to 20:00
_SLOT_LENGTHS: Final = (60, 90, 120, 180)


@dataclass(frozen=True)
class _Cluster:
    name: str
    focuses: tuple[str, ...]
    headlines: tuple[str, ...]
    bios: tuple[str, ...]
    skills: tuple[str, ...]
    interests: tuple[str, ...]


_CLUSTER_DEFINITIONS: Final = (
    _Cluster(
        name="ml-research",
        focuses=(
            "language models",
            "image recognition",
            "reinforcement learning",
            "model interpretability",
        ),
        headlines=(
            "Training neural networks and reading too many papers",
            "Machine learning researcher in the making",
            "Curious about how models learn from data",
            "Into ML research, especially {focus}",
        ),
        bios=(
            "I spend most evenings fine-tuning models and reproducing results from recent papers. "
            "Looking for people to run experiments with.",
            "Currently exploring {focus} for a research project. I like careful baselines and "
            "honest ablations.",
            "I want to co-author a workshop paper on {focus} and need collaborators who enjoy "
            "reading papers.",
            "Mostly notebooks, GPU queues and loss curves. Happy to pair on a research idea.",
        ),
        skills=(
            "python",
            "machine-learning",
            "deep-learning",
            "nlp",
            "computer-vision",
            "statistics",
            "research-methods",
            "matlab",
        ),
        interests=("scientific-research", "ai-ethics", "healthcare", "robotics"),
    ),
    _Cluster(
        name="web-apps",
        focuses=("full-stack web apps", "single-page apps", "API design", "web performance"),
        headlines=(
            "Full-stack developer who loves shipping web apps",
            "Building things for the browser",
            "Frontend and backend, mostly {focus}",
            "Web developer turning ideas into websites",
        ),
        bios=(
            "I build web apps end to end, from the database schema to the last button. I'd like "
            "a team to ship a real product with.",
            "Lately I've been focused on {focus}. Looking for collaborators for a side project "
            "that real users will visit.",
            "I enjoy turning rough ideas into working websites quickly, then making them fast "
            "and accessible.",
            "Comfortable with servers, routes and components. Want to build a web platform for "
            "a student club.",
        ),
        skills=(
            "javascript",
            "typescript",
            "react",
            "nodejs",
            "html-css",
            "rest-apis",
            "databases",
            "sql",
            "docker",
        ),
        interests=("startups", "e-commerce", "open-source", "accessibility"),
    ),
    _Cluster(
        name="mobile",
        focuses=("iPhone apps", "Android apps", "cross-platform apps", "offline-first apps"),
        headlines=(
            "Mobile developer building apps people carry around",
            "Making {focus} that feel native",
            "Phone apps, push notifications and app store releases",
            "Mobile-first product builder",
        ),
        bios=(
            "I build apps for phones and care about smooth gestures and battery life. Looking "
            "for a team to publish an app on the store.",
            "Working on {focus} in my spare time. I'd love to find people to design and test a "
            "mobile app with.",
            "I like the whole mobile cycle: prototyping screens, wiring up APIs and shipping to "
            "real devices.",
            "Interested in apps that work well on a train with bad signal. Want collaborators "
            "for a campus app.",
        ),
        skills=("swift", "kotlin", "ios", "android", "java", "rest-apis", "ui-design", "figma"),
        interests=("startups", "mobility", "sports-and-fitness", "social-impact"),
    ),
    _Cluster(
        name="games",
        focuses=("indie games", "multiplayer games", "game physics", "procedural worlds"),
        headlines=(
            "Game developer making small worlds to play in",
            "Designing and programming {focus}",
            "Gameplay programmer and game jam regular",
            "Building games, one prototype at a time",
        ),
        bios=(
            "I make games in my free time and enter every game jam I can. Looking for artists "
            "and programmers to finish a game with.",
            "Currently prototyping {focus} with a custom engine. I want a team to polish it into "
            "something playable.",
            "Level design, game feel and frame rates are what I think about. Looking for people "
            "who love playtesting.",
            "I want to release a small game this year and need collaborators for gameplay and art.",
        ),
        skills=("cpp", "csharp", "c", "rust", "ui-design", "ux-research", "project-management"),
        interests=("gaming", "music-and-arts", "hackathons", "space"),
    ),
    _Cluster(
        name="data-viz",
        focuses=(
            "interactive dashboards",
            "public data stories",
            "climate data",
            "sports analytics",
        ),
        headlines=(
            "Turning messy data into clear charts",
            "Data storyteller working on {focus}",
            "Analyst who loves a good visualization",
            "Making numbers readable for everyone",
        ),
        bios=(
            "I clean datasets, find the story in them and design charts that people actually "
            "read. Looking for a data journalism partner.",
            "Working on {focus} and always looking for better ways to show uncertainty in a chart.",
            "I like dashboards that answer a real question. Want collaborators who enjoy "
            "statistics and design.",
            "Spreadsheets, notebooks and plotting libraries are my home. Interested in "
            "visualizing open public data.",
        ),
        skills=(
            "python",
            "r",
            "sql",
            "data-analysis",
            "data-visualization",
            "statistics",
            "javascript",
            "figma",
        ),
        interests=(
            "climate-and-sustainability",
            "media-and-journalism",
            "social-impact",
            "education",
        ),
    ),
    _Cluster(
        name="security",
        focuses=(
            "capture-the-flag challenges",
            "web application security",
            "network defense",
            "reverse engineering",
        ),
        headlines=(
            "Security enthusiast breaking things to fix them",
            "Practicing {focus} and secure coding",
            "Aspiring penetration tester",
            "Defending systems, one exploit at a time",
        ),
        bios=(
            "I play capture-the-flag competitions and study how real attacks work. Looking for "
            "teammates for the next competition.",
            "Currently learning {focus}. I'd like to build tools that help find vulnerabilities "
            "before attackers do.",
            "I enjoy reading exploit write-ups and hardening Linux servers. Want people to run a "
            "security lab with.",
            "Interested in threat modelling and incident response. Looking for collaborators on "
            "a security audit project.",
        ),
        skills=(
            "linux",
            "cybersecurity",
            "c",
            "go",
            "rust",
            "python",
            "docker",
            "devops",
            "cloud-computing",
        ),
        interests=("cybersecurity", "fintech", "hackathons", "open-source"),
    ),
)

CLUSTERS: Final = tuple(cluster.name for cluster in _CLUSTER_DEFINITIONS)

# Off-topic wording, so some profiles say nothing about their cluster in prose.
_GENERIC_HEADLINES: Final = (
    "Always curious, always learning",
    "Computer science student who likes building things",
    "Looking for a team to learn with",
)
_GENERIC_BIOS: Final = (
    "Happy to help out on most things and learn the rest along the way.",
    "I like working with people who finish what they start. Open to most kinds of projects.",
    "Still figuring out what I like most, so I try a bit of everything.",
)


@dataclass(frozen=True)
class SyntheticStudent:
    """One synthetic profile. `cluster` is the hidden ground truth and is never given to a
    ranker; `features` is what scoring reads and `text_fields` is what the encoder reads."""

    cluster: str
    features: MatchFeatures
    text_fields: ProfileTextFields


def generate(seed: int, n: int = DEFAULT_STUDENTS) -> list[SyntheticStudent]:
    """`n` students, the same for the same seed on every run and machine: one seeded
    `random.Random` drives every choice, and ids are uuid5s of the seed and position. Clusters
    are assigned in turn, so they're balanced, and the first students don't depend on `n`."""
    rng = random.Random(seed)  # noqa: S311 - reproducible synthetic data, not a security use
    return [
        _student(rng, seed, index, _CLUSTER_DEFINITIONS[index % len(_CLUSTER_DEFINITIONS)])
        for index in range(n)
    ]


def _student(rng: random.Random, seed: int, index: int, cluster: _Cluster) -> SyntheticStudent:
    focus = rng.choice(cluster.focuses)
    headline = _text(rng, cluster.headlines, _GENERIC_HEADLINES, focus)
    bio = _text(rng, cluster.bios, _GENERIC_BIOS, focus)

    all_skills = tuple(SKILL_NAMES)
    has = _pick(rng, rng.randint(*_HAS_SKILLS), cluster.skills, all_skills, _CLUSTER_SKILL_RATE)
    seeking = _pick(
        rng,
        rng.randint(*_SEEKING_SKILLS),
        cluster.skills,
        all_skills,
        _CLUSTER_SKILL_RATE,
        exclude=frozenset(has),
    )
    interests = _pick(
        rng,
        rng.randint(*_INTERESTS),
        cluster.interests,
        tuple(INTEREST_NAMES),
        _CLUSTER_INTEREST_RATE,
    )
    goals = _goals(rng)
    timezone = rng.choices(TIMEZONES, weights=_TIMEZONE_WEIGHTS)[0]
    availability = _availability(rng)

    features = MatchFeatures(
        user_id=uuid.uuid5(_NAMESPACE, f"synthetic-student/{seed}/{index}"),
        timezone=timezone,
        goals=frozenset(goals),
        has_skills=frozenset(Item(slug=slug, name=SKILL_NAMES[slug]) for slug in has),
        seeking_skills=frozenset(Item(slug=slug, name=SKILL_NAMES[slug]) for slug in seeking),
        interests=frozenset(Item(slug=slug, name=INTEREST_NAMES[slug]) for slug in interests),
        availability=availability,
    )
    text_fields = ProfileTextFields(
        headline=headline,
        bio=bio,
        goals=goals,
        has_skills=tuple(SKILL_NAMES[slug] for slug in has),
        seeking_skills=tuple(SKILL_NAMES[slug] for slug in seeking),
        interests=tuple(INTEREST_NAMES[slug] for slug in interests),
    )
    return SyntheticStudent(cluster=cluster.name, features=features, text_fields=text_fields)


def _text(rng: random.Random, topical: Sequence[str], generic: Sequence[str], focus: str) -> str:
    templates = topical if rng.random() < _TOPICAL_TEXT_RATE else generic
    return rng.choice(templates).format(focus=focus)


def _pick(
    rng: random.Random,
    count: int,
    preferred: Sequence[str],
    everything: Sequence[str],
    preferred_rate: float,
    *,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """`count` distinct slugs, each drawn from `preferred` with probability `preferred_rate`
    and from `everything` otherwise, never one in `exclude`."""
    chosen: list[str] = []
    while len(chosen) < count:
        pool = preferred if rng.random() < preferred_rate else everything
        options = [slug for slug in pool if slug not in exclude and slug not in chosen]
        if not options:
            options = [slug for slug in everything if slug not in exclude and slug not in chosen]
        chosen.append(rng.choice(options))
    return tuple(chosen)


def _goals(rng: random.Random) -> tuple[str, ...]:
    """A non-empty subset of `GOALS`, in `GOALS` order. Independent of the cluster: what someone
    wants to do together says little about their topic."""
    goals = tuple(goal for goal in GOALS if rng.random() < _GOAL_RATE)
    return goals or (rng.choice(GOALS),)


def _availability(rng: random.Random) -> tuple[WeeklySlot, ...]:
    """1 to 5 weekly slots that never overlap on the same day (touching is allowed), the same
    rule the profile API enforces. A clashing draw is redrawn; with at most five slots across
    seven days there is always room, so this ends."""
    slots: list[WeeklySlot] = []
    target = rng.randint(*_SLOTS)
    while len(slots) < target:
        start = rng.choice(_SLOT_STARTS)
        slot = WeeklySlot(
            weekday=rng.randrange(7),
            start_min=start,
            end_min=min(start + rng.choice(_SLOT_LENGTHS), 1440),
        )
        clashes = any(
            other.weekday == slot.weekday
            and other.start_min < slot.end_min
            and slot.start_min < other.end_min
            for other in slots
        )
        if not clashes:
            slots.append(slot)
    return tuple(sorted(slots, key=lambda slot: (slot.weekday, slot.start_min)))
