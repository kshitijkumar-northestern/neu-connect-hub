"""The background worker that turns stale profile rows into vectors, in batches, never while a
request is in flight. `run_once` is one unit of work (useful to drive directly from tests);
`run_forever` polls it until cancelled.

`run_once` is two short transactions with the encode call in between and no transaction open:
claiming a row holds its lock only long enough to decide what to do with it, never for the
duration of a model call, so a profile save is never kept waiting behind an encode.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import Database
from app.ml.encoders import Encoder, validate_batch
from app.ml.models import Embedding
from app.ml.profile_text import ProfileTextFields, profile_text, source_hash
from app.modules.auth.models import User
from app.modules.profiles.models import Interest, Profile, ProfileInterest, ProfileSkill, Skill

logger = logging.getLogger("app.ml")

# How long a claimed row is leased for. A batch that keeps failing validation therefore sits out
# for this long instead of blocking the queue forever, and a crashed worker's claims expire on
# their own instead of leaving rows stuck.
CLAIM_LEASE = timedelta(minutes=5)


@dataclass(frozen=True)
class _Claimed:
    """A row claimed for encoding: the text it produced and the hash that text hashes to."""

    user_id: uuid.UUID
    text: str
    digest: str


class EmbeddingWorker:
    """Claims a batch of stale, unleased rows (`FOR UPDATE SKIP LOCKED`, so two workers never
    claim the same one), re-encodes what changed, and settles the rest."""

    def __init__(self, database: Database, encoder: Encoder, settings: Settings) -> None:
        self._database = database
        self._encoder = encoder
        self._settings = settings

    async def run_once(self) -> int:
        """Processes one batch and returns how many rows it settled: encoded, freshened without
        encoding (unchanged text), or deleted (no longer eligible)."""
        claimed = await self._claim()
        if claimed is None:
            return 0
        settled, to_encode, claim_until = claimed
        if not to_encode:
            return settled

        texts = [item.text for item in to_encode]
        vectors = await asyncio.to_thread(self._encoder.encode, texts)
        if not validate_batch(vectors, len(to_encode)):
            logger.error(
                "Rejected an embedding batch; the rows stay stale",
                extra={
                    "count": len(to_encode),
                    "user_ids": [str(item.user_id) for item in to_encode],
                },
            )
            return settled

        if claim_until is None:  # to_encode non-empty always pairs with a lease; defensive only
            return settled
        return settled + await self._write_back(to_encode, vectors, claim_until)

    async def _claim(self) -> tuple[int, list[_Claimed], datetime | None] | None:
        """Transaction 1: claim, delete the ineligible, freshen the unchanged, and lease the
        rest. Returns None when there was nothing stale and unleased to claim."""
        async with self._database.sessionmaker() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(Embedding)
                        .where(
                            Embedding.stale,
                            or_(
                                Embedding.claimed_until.is_(None),
                                Embedding.claimed_until < func.now(),
                            ),
                        )
                        .order_by(Embedding.updated_at)
                        .limit(self._settings.embedding_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return None

            user_ids = [row.user_id for row in rows]
            eligible = await self._eligible_profiles(session, user_ids)
            texts = await self._texts(session, eligible)

            settled = 0
            to_delete: list[uuid.UUID] = []
            to_encode: list[_Claimed] = []
            for row in rows:
                profile = eligible.get(row.user_id)
                if profile is None:
                    to_delete.append(row.user_id)
                    continue
                text = texts[row.user_id]
                digest = source_hash(self._encoder.model_version, text)
                if row.source_hash == digest and row.model_version == self._encoder.model_version:
                    row.stale = False
                    row.claimed_until = None
                    settled += 1
                    continue
                to_encode.append(_Claimed(user_id=row.user_id, text=text, digest=digest))

            if to_delete:
                await session.execute(delete(Embedding).where(Embedding.user_id.in_(to_delete)))
                settled += len(to_delete)

            claim_until: datetime | None = None
            if to_encode:
                now = await session.scalar(select(func.clock_timestamp()))
                if now is None:
                    raise RuntimeError("clock_timestamp() returned no value")
                claim_until = now + CLAIM_LEASE
                await session.execute(
                    update(Embedding)
                    .where(Embedding.user_id.in_([item.user_id for item in to_encode]))
                    .values(claimed_until=claim_until)
                )

            return settled, to_encode, claim_until

    async def _write_back(
        self, to_encode: Sequence[_Claimed], vectors: list[list[float]], claim_until: datetime
    ) -> int:
        """Transaction 2: store each validated vector, guarded on the row still being stale under
        *this* claim. A profile save in the meantime clears `claimed_until`, so an older write-back
        simply misses (the row stays stale, and the newer text is encoded next pass)."""
        settled = 0
        async with self._database.sessionmaker() as session, session.begin():
            for item, vector in zip(to_encode, vectors, strict=True):
                written = await session.scalar(
                    update(Embedding)
                    .where(
                        Embedding.user_id == item.user_id,
                        Embedding.stale,
                        Embedding.claimed_until == claim_until,
                    )
                    .values(
                        embedding=vector,
                        model_version=self._encoder.model_version,
                        source_hash=item.digest,
                        stale=False,
                        claimed_until=None,
                        updated_at=func.now(),
                    )
                    .returning(Embedding.user_id)
                )
                if written is not None:
                    settled += 1
        return settled

    async def _eligible_profiles(
        self, session: AsyncSession, user_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Profile]:
        """A stranger could see this profile: discoverable, verified and not suspended (the same
        conditions `visible_to` uses for someone who isn't connected)."""
        rows = await session.execute(
            select(Profile, User.verified_at, User.suspended_at)
            .join(User, User.id == Profile.user_id)
            .where(Profile.user_id.in_(user_ids))
        )
        return {
            profile.user_id: profile
            for profile, verified_at, suspended_at in rows
            if profile.discoverable and verified_at is not None and suspended_at is None
        }

    async def _texts(
        self, session: AsyncSession, profiles: dict[uuid.UUID, Profile]
    ) -> dict[uuid.UUID, str]:
        """Builds each profile's text from a fixed, small number of queries regardless of batch
        size, so a bigger batch never means an N+1."""
        if not profiles:
            return {}
        user_ids = list(profiles)

        has: dict[uuid.UUID, list[str]] = defaultdict(list)
        seeking: dict[uuid.UUID, list[str]] = defaultdict(list)
        skill_rows = await session.execute(
            select(ProfileSkill.user_id, ProfileSkill.kind, Skill.name)
            .join(Skill, Skill.id == ProfileSkill.skill_id)
            .where(ProfileSkill.user_id.in_(user_ids))
        )
        for user_id, kind, name in skill_rows:
            (has if kind == "has" else seeking)[user_id].append(name)

        interests: dict[uuid.UUID, list[str]] = defaultdict(list)
        interest_rows = await session.execute(
            select(ProfileInterest.user_id, Interest.name)
            .join(Interest, Interest.id == ProfileInterest.interest_id)
            .where(ProfileInterest.user_id.in_(user_ids))
        )
        for user_id, name in interest_rows:
            interests[user_id].append(name)

        return {
            user_id: profile_text(
                ProfileTextFields(
                    headline=profile.headline,
                    bio=profile.bio,
                    goals=profile.goals,
                    has_skills=has.get(user_id, []),
                    seeking_skills=seeking.get(user_id, []),
                    interests=interests.get(user_id, []),
                )
            )
            for user_id, profile in profiles.items()
        }

    async def run_forever(self) -> None:
        """Polls `run_once` until cancelled. An iteration that raises is logged and backed off;
        it never takes the loop down, and cancellation always stops it cleanly."""
        while True:
            try:
                settled = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Embedding worker iteration failed")
                settled = 0
            if settled == 0:
                await asyncio.sleep(self._settings.embedding_poll_seconds)
