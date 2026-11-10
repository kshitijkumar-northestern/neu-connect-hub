"""Fill the development database with synthetic demo students.

Usage: cd apps/api && DEMO_PASSWORD='<passphrase>' python -m scripts.seed_demo --count 40
"""

import argparse
import asyncio
import os
import random
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select

from app.core.config import Settings, get_settings
from app.db.session import create_database
from app.modules.auth.models import User
from app.modules.auth.passwords import PasswordService
from app.modules.profiles.models import (
    GOALS,
    AvailabilitySlot,
    Interest,
    Profile,
    ProfileInterest,
    ProfileSkill,
    Skill,
)

# Reserved by RFC 2606, so no real mailbox can exist there.
DEMO_DOMAIN = "demo.example.com"

# Fixed namespace for deterministic UUID generation.
FIXED_NAMESPACE = uuid.UUID("12345678-1234-5678-1234-567812345678")

# Fixed first and last names for synthetic display names.
FIRST_NAMES = [
    "Ada",
    "Alice",
    "Alex",
    "Ava",
    "Bailey",
    "Blake",
    "Casey",
    "Chris",
    "Dana",
    "Drew",
    "Elliot",
    "Emma",
    "Evan",
    "Eve",
    "Fiona",
    "Frank",
    "Grace",
    "Gary",
    "Harper",
    "Henry",
]
LAST_NAMES = [
    "Anderson",
    "Bennett",
    "Carter",
    "Davis",
    "Evans",
    "Foster",
    "Garcia",
    "Harris",
    "Iverson",
    "Jackson",
    "Klein",
    "Lewis",
    "Morgan",
    "Nelson",
    "O'Brien",
    "Parker",
    "Quinn",
    "Robinson",
    "Stewart",
    "Thompson",
]

# Fixed timezone list for deterministic assignment.
TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Asia/Kolkata",
]


async def seed(settings: Settings, *, count: int = 40, password: str) -> int:
    """Create synthetic demo students in the database.

    If the database already contains demo students, deletes them first and recreates them
    identically. Real accounts (not from DEMO_DOMAIN) are left alone.

    Args:
        settings: Runtime configuration (used to check production mode).
        count: Number of demo students to create.
        password: Password for all demo accounts (hashed before storage).

    Returns:
        The number of students created.

    Raises:
        SystemExit: If attempting to seed production.
    """
    if settings.is_production:
        raise SystemExit("Refusing to seed demo data in production.")

    # One Random instance per call, seeded deterministically for reproducibility.
    rng = random.Random(42)  # noqa: S311 - deterministic synthetic data, not a security use

    database = create_database(settings)
    try:
        # Hash the password once and reuse for all demo accounts.
        password_service = PasswordService(settings)
        password_hash = await password_service.hash(password)

        async with database.sessionmaker() as session:
            # Delete previous demo users (ON DELETE CASCADE handles their profiles).
            await session.execute(
                delete(User).where(User.email.endswith(f"@{DEMO_DOMAIN}", autoescape=True))
            )

            # Read skills and interests from the database, ordered by slug.
            skills_result = await session.execute(select(Skill).order_by(Skill.slug))
            all_skills = skills_result.scalars().all()

            interests_result = await session.execute(select(Interest).order_by(Interest.slug))
            all_interests = interests_result.scalars().all()

            # Create users and profiles.
            users = []
            profiles = []
            profile_skills = []
            profile_interests = []
            availability_slots = []

            verified_time = datetime(2026, 9, 1, tzinfo=UTC)

            for i in range(1, count + 1):
                # Generate user.
                user_id = uuid.uuid5(FIXED_NAMESPACE, f"demo-student-{i}")
                email = f"student{i:02d}@{DEMO_DOMAIN}"
                user = User(
                    id=user_id,
                    email=email,
                    password_hash=password_hash,
                    role="student",
                    verified_at=verified_time,
                )
                users.append(user)

                # Generate display name from lists.
                first = rng.choice(FIRST_NAMES)
                last = rng.choice(LAST_NAMES)
                display_name = f"{first} {last}"

                # Generate headline and bio.
                headlines = [
                    "Passionate about coding and learning",
                    "Building cool things with friends",
                    "Exploring AI and machine learning",
                    "Designer and developer",
                    "Always curious, always learning",
                    "Tech enthusiast and problem solver",
                ]
                headline = rng.choice(headlines)

                bios = [
                    "Looking to collaborate on interesting projects.",
                    "Interested in learning new technologies.",
                    "Love working on team projects.",
                    "Want to expand my skills.",
                    "Exploring different domains of computer science.",
                    "Passionate about open source.",
                ]
                bio = rng.choice(bios)

                # Generate goals (non-empty subset in GOALS order).
                num_goals = rng.randint(1, len(GOALS))
                chosen = set(rng.sample(GOALS, num_goals))
                goals = [goal for goal in GOALS if goal in chosen]

                # Assign timezone deterministically (but randomized per student).
                timezone_str = rng.choice(TIMEZONES)

                # Discoverable and feedback_consent vary deterministically.
                # With count=12, both values must occur.
                discoverable = (i % 3) != 0  # 2/3 True, 1/3 False
                feedback_consent = (i % 5) == 0  # 1/5 True, 4/5 False

                profile = Profile(
                    user_id=user_id,
                    display_name=display_name,
                    headline=headline,
                    bio=bio,
                    goals=goals,
                    timezone=timezone_str,
                    discoverable=discoverable,
                    feedback_consent=feedback_consent,
                )
                profiles.append(profile)

                # Add 2-5 "has" skills with proficiency 1-3.
                num_has_skills = rng.randint(2, min(5, len(all_skills)))
                has_skills = rng.sample(all_skills, num_has_skills)
                for skill in has_skills:
                    proficiency = rng.randint(1, 3)
                    profile_skills.append(
                        ProfileSkill(
                            user_id=user_id,
                            skill_id=skill.id,
                            kind="has",
                            proficiency=proficiency,
                        )
                    )

                # Add 1-3 "seeking" skills, disjoint from "has" set.
                available_seeking = [s for s in all_skills if s not in has_skills]
                num_seeking = rng.randint(1, min(3, len(available_seeking)))
                seeking_skills = rng.sample(available_seeking, num_seeking)
                for skill in seeking_skills:
                    profile_skills.append(
                        ProfileSkill(
                            user_id=user_id,
                            skill_id=skill.id,
                            kind="seeking",
                            proficiency=None,
                        )
                    )

                # Add 1-4 interests.
                num_interests = rng.randint(1, min(4, len(all_interests)))
                interests = rng.sample(all_interests, num_interests)
                for interest in interests:
                    profile_interests.append(
                        ProfileInterest(
                            user_id=user_id,
                            interest_id=interest.id,
                        )
                    )

                # Add 1-5 availability slots on distinct weekdays and start times.
                num_slots = rng.randint(1, 5)
                used_pairs = set()
                for _ in range(num_slots):
                    while True:
                        weekday = rng.randint(0, 6)
                        # Align to 30 minutes: start_min must be a multiple of 30.
                        start_min = rng.randint(0, (1440 // 30) - 1) * 30
                        if (weekday, start_min) not in used_pairs:
                            used_pairs.add((weekday, start_min))
                            break

                    # End time is after start (at least 30 minutes later, aligned to 30 min).
                    minutes_remaining = (1440 - start_min) // 30
                    if minutes_remaining > 1:
                        duration_blocks = rng.randint(1, minutes_remaining - 1)
                        end_min = start_min + (duration_blocks * 30)
                    else:
                        end_min = 1440

                    availability_slots.append(
                        AvailabilitySlot(
                            user_id=user_id,
                            weekday=weekday,
                            start_min=start_min,
                            end_min=end_min,
                        )
                    )

            # Flush users first (foreign key dependency for profiles).
            session.add_all(users)
            await session.flush()

            # Add and flush profiles (foreign key dependency for skills, interests, slots).
            session.add_all(profiles)
            await session.flush()

            # Add all profile-related data.
            session.add_all(profile_skills)
            session.add_all(profile_interests)
            session.add_all(availability_slots)

            await session.commit()

        return count

    finally:
        await database.engine.dispose()


def main(argv: list[str] | None = None) -> None:
    """Entry point for the seed script."""
    parser = argparse.ArgumentParser(
        description="Create synthetic demo students in the development database."
    )
    parser.add_argument(
        "--count",
        type=int,
        default=40,
        help="Number of demo students to create (1-500).",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("DEMO_PASSWORD"),
        help="Password for demo accounts (env var DEMO_PASSWORD if not specified).",
    )

    args = parser.parse_args(argv)

    if not args.password:
        parser.error("--password is required (or set DEMO_PASSWORD environment variable)")

    if len(args.password) < 12:
        parser.error("Password must be at least 12 characters")

    if not 1 <= args.count <= 500:
        parser.error("--count must be between 1 and 500")

    result = asyncio.run(seed(get_settings(), count=args.count, password=args.password))
    email_range = f"student01@{DEMO_DOMAIN} .. student{result:02d}@{DEMO_DOMAIN}"
    print(f"Seeded {result} demo students ({email_range})")


if __name__ == "__main__":
    main()
