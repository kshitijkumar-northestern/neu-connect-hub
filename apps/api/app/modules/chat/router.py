import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_verified
from app.db.session import get_session
from app.modules.auth.sessions import CurrentSession
from app.modules.chat.schemas import DEFAULT_PAGE, MAX_PAGE, ConversationOut, MessagePage
from app.modules.chat.service import ChatService

# Sending happens over Socket.IO (chat.send); HTTP serves the list and history.
router = APIRouter(prefix="/conversations", tags=["chat"])


def chat_service(db: AsyncSession = Depends(get_session)) -> ChatService:
    return ChatService(db)


@router.get("")
async def list_conversations(
    request: Request,
    current: CurrentSession = Depends(require_verified),
    service: ChatService = Depends(chat_service),
) -> list[ConversationOut]:
    return await service.conversations(current.user.id, request.app.state.presence)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: uuid.UUID,
    request: Request,
    current: CurrentSession = Depends(require_verified),
    service: ChatService = Depends(chat_service),
) -> ConversationOut:
    """Members only, like the history."""
    return await service.conversation(current.user.id, conversation_id, request.app.state.presence)


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    before: str | None = Query(default=None, max_length=200),
    after: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    current: CurrentSession = Depends(require_verified),
    service: ChatService = Depends(chat_service),
) -> MessagePage:
    """Members only; anyone else gets 404, the same as for a missing conversation."""
    return await service.page(
        current.user.id, conversation_id, before=before, after=after, limit=limit
    )
