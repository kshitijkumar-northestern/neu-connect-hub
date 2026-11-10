import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.connections.schemas import ConnectionOut, ConnectionRequest
from app.modules.connections.service import ConnectionService

router = APIRouter(prefix="/connections", tags=["connections"])


def connection_service(
    request: Request, db: AsyncSession = Depends(get_session)
) -> ConnectionService:
    return ConnectionService(db, request.app.state.limiter)


@router.get("")
async def list_connections(
    current: CurrentSession = Depends(require_verified),
    service: ConnectionService = Depends(connection_service),
) -> list[ConnectionOut]:
    return await service.list(current.user.id)


@router.post("", status_code=201)
async def request_connection(
    body: ConnectionRequest,
    current: CurrentSession = Depends(require_verified),
    service: ConnectionService = Depends(connection_service),
) -> ConnectionOut:
    return await service.request(current.user.id, body.user_id)


@router.post("/{connection_id}/accept")
async def accept_connection(
    connection_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: ConnectionService = Depends(connection_service),
) -> ConnectionOut:
    """Recipient only. A retry returns the same accepted connection and conversation."""
    return await service.accept(current.user.id, connection_id)


@router.post("/{connection_id}/decline", status_code=204)
async def decline_connection(
    connection_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: ConnectionService = Depends(connection_service),
) -> Response:
    await service.decline(current.user.id, connection_id)
    return Response(status_code=204)


@router.post("/{connection_id}/withdraw", status_code=204)
async def withdraw_connection(
    connection_id: uuid.UUID,
    current: CurrentSession = Depends(require_verified),
    service: ConnectionService = Depends(connection_service),
) -> Response:
    await service.withdraw(current.user.id, connection_id)
    return Response(status_code=204)
