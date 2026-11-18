import uuid
from typing import Any, cast

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.rate_limit import Limit, RateLimiter, enforce
from app.ml.models import Embedding
from app.modules.auth.models import User
from app.modules.discovery.models import MatchFeedback
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
from app.modules.scan.models import ProfileShareToken

PROFILE_NOT_FOUND = "Profile not found."
# Each save rewrites the whole profile and queues a re-encode, so a script can't hammer it.
SAVES_PER_USER = Limit(max_events=30, window_seconds=3600)
SAVES_LIMITED = "Too many profile saves. Please wait and try again."


def _by_name[T: NamedItem | SkillItem | InterestItem](items: list[T]) -> list[T]:
    return sorted(items, key=lambda item: item.name.casefold())


class ProfileService:
    def __init__(self, db: AsyncSession, limiter: RateLimiter) -> None:
        self.db = db
        self.limiter = limiter

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

        # Lock order: the account's users row first, as ScanService.rotate and disable take it,
        # then the profile row (the upsert), then the share codes. It also makes two saves by one
        # student run one after the other. FOR NO KEY UPDATE, not FOR UPDATE: it still waits for
        # a rotation (FOR UPDATE), but never for the FOR KEY SHARE a foreign-key check takes.
        # DiscoveryService.feedback holds the profile row and then inserts rows referencing this
        # account, so FOR UPDATE here would deadlock with it.
        await self.db.execute(
            select(User.id).where(User.id == user_id).with_for_update(key_share=True)
        )
        await self._limit_unless_reducing_exposure(user_id, data)

        fields = {
            "display_name": data.display_name,
            "headline": data.headline,
            "bio": data.bio,
            "goals": [goal for goal in GOALS if goal in data.goals],
            "timezone": data.timezone,
            "discoverable": data.discoverable,
            "feedback_consent": data.feedback_consent,
        }
        await self.db.execute(
            pg_insert(Profile)
            .values(user_id=user_id, **fields)
            .on_conflict_do_update(
                index_elements=[Profile.user_id], set_={**fields, "updated_at": func.now()}
            )
        )
        for model in (ProfileSkill, ProfileInterest, AvailabilitySlot):
            await self.db.execute(delete(model).where(model.user_id == user_id))

        # Discoverable profiles queue a re-encode. Opting out purges what was derived from being
        # discoverable at once: the vector, and every share code, active or not, since a
        # stranger's scan of a hidden profile can only 404. Opting back in brings no code back.
        # All of it happens in this same transaction, so a validation failure above leaves none
        # of it behind.
        if data.discoverable:
            await self.db.execute(
                pg_insert(Embedding)
                .values(user_id=user_id, stale=True, claimed_until=None, updated_at=func.now())
                .on_conflict_do_update(
                    index_elements=[Embedding.user_id],
                    set_={"stale": True, "claimed_until": None, "updated_at": func.now()},
                )
            )
        else:
            await self.db.execute(delete(Embedding).where(Embedding.user_id == user_id))
            await self.db.execute(
                delete(ProfileShareToken).where(ProfileShareToken.user_id == user_id)
            )

        # Feedback is kept only while the viewer both consents and stays discoverable; losing
        # either purges what they left behind, in this same transaction.
        if not data.feedback_consent or not data.discoverable:
            await self.db.execute(delete(MatchFeedback).where(MatchFeedback.viewer_id == user_id))

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

    async def _limit_unless_reducing_exposure(self, user_id: uuid.UUID, data: ProfileInput) -> None:
        """Every save that passed validation counts toward the limit, except one that takes
        exposure away: hiding the profile or withdrawing feedback consent is never refused, and
        costs nothing to serve (it purges rather than queueing an encode). Compared with the
        stored profile under the users-row lock, so nothing can change it meanwhile."""
        stored = (
            await self.db.execute(
                select(Profile.discoverable, Profile.feedback_consent).where(
                    Profile.user_id == user_id
                )
            )
        ).one_or_none()
        if stored is not None and (
            (stored.discoverable and not data.discoverable)
            or (stored.feedback_consent and not data.feedback_consent)
        ):
            return
        try:
            enforce(self.limiter, "profile-save", str(user_id), SAVES_PER_USER, SAVES_LIMITED)
        except AppError:
            await self.db.rollback()
            raise

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
