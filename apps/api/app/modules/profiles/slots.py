"""Weekly availability, batch-loaded for scoring: one query however many students are asked for,
shared by Discover's ranker and suggested teammates so the two read slots the same way."""

import uuid
from collections import defaultdict
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ml.scoring import WeeklySlot
from app.modules.profiles.models import AvailabilitySlot


async def load_slots(
    session: AsyncSession, user_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[WeeklySlot]]:
    """Each student's weekly slots, in their own timezone. Someone with none is simply absent."""
    slots: dict[uuid.UUID, list[WeeklySlot]] = defaultdict(list)
    rows = await session.execute(
        select(
            AvailabilitySlot.user_id,
            AvailabilitySlot.weekday,
            AvailabilitySlot.start_min,
            AvailabilitySlot.end_min,
        ).where(AvailabilitySlot.user_id.in_(list(user_ids)))
    )
    for user_id, weekday, start_min, end_min in rows:
        slots[user_id].append(WeeklySlot(weekday=weekday, start_min=start_min, end_min=end_min))
    return dict(slots)
