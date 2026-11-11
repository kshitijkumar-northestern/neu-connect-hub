import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.rooms.schemas import IceServersOut, RoomCreate, RoomOut
from app.modules.rooms.service import RoomService

router = APIRouter(prefix="/rooms", tags=["rooms"])


def room_service(request: Request, db: AsyncSession = Depends(get_session)) -> RoomService:
    return RoomService(
        db, request.app.state.limiter, request.app.state.calls, request.app.state.settings
    )


@router.post("")
async def start_room(
    body: RoomCreate,
    response: Response,
    current: CurrentSession = Depends(require_verified),
    service: RoomService = Depends(room_service),
) -> RoomOut:
    """201 for a new call, 200 when the conversation's call was already going."""
    room, created = await service.start(current.user.id, body.conversation_id)
    response.status_code = 201 if created else 200
    return room


@router.get("/{room_id}")
async def get_room(
    room_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: RoomService = Depends(room_service),
) -> RoomOut:
    """A room in any state; members only, the same 404 whether it's missing or they may not."""
    return await service.get(current.user.id, room_id)


@router.get("/{room_id}/ice-servers")
async def get_ice_servers(
    room_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: RoomService = Depends(room_service),
) -> IceServersOut:
    return await service.ice_servers(current.user.id, room_id)
