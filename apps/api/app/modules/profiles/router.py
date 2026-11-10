import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.profiles.schemas import (
    InterestItem,
    OwnProfile,
    ProfileInput,
    PublicProfile,
    SkillItem,
)
from app.modules.profiles.service import ProfileService

# Profiles are for verified students only: unverified accounts can't create or view one.
router = APIRouter(tags=["profiles"])


def profile_service(db: AsyncSession = Depends(get_session)) -> ProfileService:
    return ProfileService(db)


@router.get("/skills")
async def list_skills(
    _: CurrentSession = Depends(require_verified),
    service: ProfileService = Depends(profile_service),
) -> list[SkillItem]:
    return await service.skills()


@router.get("/interests")
async def list_interests(
    _: CurrentSession = Depends(require_verified),
    service: ProfileService = Depends(profile_service),
) -> list[InterestItem]:
    return await service.interests()


@router.get("/me/profile")
async def get_my_profile(
    current: CurrentSession = Depends(require_verified),
    service: ProfileService = Depends(profile_service),
) -> OwnProfile:
    return await service.own(current.user.id)


@router.put("/me/profile")
async def save_my_profile(
    body: ProfileInput,
    current: CurrentSession = Depends(require_verified),
    service: ProfileService = Depends(profile_service),
) -> OwnProfile:
    """Replaces the caller's whole profile. There is no route to change anyone else's."""
    return await service.save(current.user.id, body)


@router.get("/profiles/{user_id}")
async def get_profile(
    user_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: ProfileService = Depends(profile_service),
) -> PublicProfile:
    return await service.visible(current.user.id, user_id)
