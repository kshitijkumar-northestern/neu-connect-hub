import uuid
from typing import Any, cast

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.modules.auth.models import User
from app.modules.profiles.models import (
    GOALS,
    LEVELS,
    AvailabilitySlot,
    Interest,
    Profile,
    ProfileInterest,
    ProfileSkill,
    Skill,
)
from app.modules.profiles.schemas import (
    Goal,
    InterestItem,
    Level,
    LeveledSkill,
    NamedItem,
    OwnProfile,
    ProfileInput,
    PublicProfile,
    SkillItem,
    SlotOut,
)
from app.modules.profiles.visibility import visible_to

PROFILE_NOT_FOUND = "Profile not found."


def _by_name[T: NamedItem | SkillItem | InterestItem](items: list[T]) -> list[T]:
    return sorted(items, key=lambda item: item.name.casefold())


class ProfileService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- taxonomy ---------------------------------------------------------------------------

    async def skills(self) -> list[SkillItem]:
        rows = (await self.db.execute(select(Skill.slug, Skill.name, Skill.category))).all()
        return _by_name([SkillItem(slug=s, name=n, category=c) for s, n, c in rows])

    async def interests(self) -> list[InterestItem]:
        rows = (await self.db.execute(select(Interest.slug, Interest.name))).all()
        return _by_name([InterestItem(slug=s, name=n) for s, n in rows])

    # --- the owner's profile ----------------------------------------------------------------

    async def own(self, user_id: uuid.UUID) -> OwnProfile:
        profile = await self.db.get(Profile, user_id, populate_existing=True)
        if profile is None:
            raise AppError(404, "profile_not_found", "You haven't created a profile yet.")
        public = await self._public(profile)
        return OwnProfile(
            **public.model_dump(),
            discoverable=profile.discoverable,
            feedback_consent=profile.feedback_consent,
            updated_at=profile.updated_at,
        )

    async def save(self, user_id: uuid.UUID, data: ProfileInput) -> OwnProfile:
        """Replace the whole profile in one transaction."""
        has = {skill.slug: LEVELS.index(skill.level) + 1 for skill in data.skills}
        skill_ids = await self._ids(Skill, set(has) | set(data.seeking_skills), "unknown_skill")
        interest_ids = await self._ids(Interest, set(data.interests), "unknown_interest")

        fields = {
            "display_name": data.display_name,
            "headline": data.headline,
            "bio": data.bio,
            "goals": [goal for goal in GOALS if goal in data.goals],
            "timezone": data.timezone,
            "discoverable": data.discoverable,
            "feedback_consent": data.feedback_consent,
        }
        # The upsert locks the profile row until commit, so two saves by the same student run
        # one after the other instead of interleaving their deletes and inserts.
        await self.db.execute(
            pg_insert(Profile)
            .values(user_id=user_id, **fields)
            .on_conflict_do_update(
                index_elements=[Profile.user_id], set_={**fields, "updated_at": func.now()}
            )
        )
        for model in (ProfileSkill, ProfileInterest, AvailabilitySlot):
            await self.db.execute(delete(model).where(model.user_id == user_id))

        skill_rows: list[dict[str, Any]] = [
            {"user_id": user_id, "skill_id": skill_ids[slug], "kind": "has", "proficiency": level}
            for slug, level in has.items()
        ] + [
            {
                "user_id": user_id,
                "skill_id": skill_ids[slug],
                "kind": "seeking",
                "proficiency": None,
            }
            for slug in data.seeking_skills
        ]
        interest_rows = [
            {"user_id": user_id, "interest_id": interest_ids[slug]} for slug in data.interests
        ]
        slot_rows = [{"user_id": user_id, **slot.model_dump()} for slot in data.availability]
        for model, rows in (
            (ProfileSkill, skill_rows),
            (ProfileInterest, interest_rows),
            (AvailabilitySlot, slot_rows),
        ):
            if rows:
                await self.db.execute(insert(model), rows)
        await self.db.commit()
        return await self.own(user_id)

    # --- other students ---------------------------------------------------------------------

    async def visible(self, viewer_id: uuid.UUID, target_id: uuid.UUID) -> PublicProfile:
        """A profile the viewer may see. Hidden, blocked, suspended, unverified and missing
        profiles all get the same 404, so the answer never reveals which one it was."""
        profile = await self.db.scalar(
            select(Profile)
            .join(User, User.id == Profile.user_id)
            .where(Profile.user_id == target_id, visible_to(viewer_id))
        )
        if profile is None:
            raise AppError(404, "not_found", PROFILE_NOT_FOUND)
        return await self._public(profile)

    # --- helpers ----------------------------------------------------------------------------

    async def _public(self, profile: Profile) -> PublicProfile:
        user_id = profile.user_id
        skill_rows = (
            await self.db.execute(
                select(Skill.slug, Skill.name, ProfileSkill.kind, ProfileSkill.proficiency)
                .join(Skill, Skill.id == ProfileSkill.skill_id)
                .where(ProfileSkill.user_id == user_id)
            )
        ).all()
        interest_rows = (
            await self.db.execute(
                select(Interest.slug, Interest.name)
                .join(ProfileInterest, ProfileInterest.interest_id == Interest.id)
                .where(ProfileInterest.user_id == user_id)
            )
        ).all()
        slots = (
            await self.db.scalars(
                select(AvailabilitySlot)
                .where(AvailabilitySlot.user_id == user_id)
                .order_by(AvailabilitySlot.weekday, AvailabilitySlot.start_min)
            )
        ).all()
        return PublicProfile(
            user_id=user_id,
            display_name=profile.display_name,
            headline=profile.headline,
            bio=profile.bio,
            goals=cast(list[Goal], profile.goals),
            timezone=profile.timezone,
            skills=_by_name(
                [
                    LeveledSkill(slug=slug, name=name, level=cast(Level, LEVELS[level - 1]))
                    for slug, name, kind, level in skill_rows
                    if kind == "has" and level is not None
                ]
            ),
            seeking_skills=_by_name(
                [NamedItem(slug=s, name=n) for s, n, kind, _ in skill_rows if kind == "seeking"]
            ),
            interests=_by_name([NamedItem(slug=s, name=n) for s, n in interest_rows]),
            availability=[
                SlotOut(weekday=s.weekday, start_min=s.start_min, end_min=s.end_min) for s in slots
            ],
        )

    async def _ids(
        self, model: type[Skill] | type[Interest], slugs: set[str], code: str
    ) -> dict[str, int]:
        if not slugs:
            return {}
        rows = await self.db.execute(select(model.slug, model.id).where(model.slug.in_(slugs)))
        found = {slug: id_ for slug, id_ in rows.all()}
        if len(found) != len(slugs):
            what = "skills" if model is Skill else "interests"
            raise AppError(422, code, f"One of the {what} isn't in the list. Choose from the list.")
        return found
