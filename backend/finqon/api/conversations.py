from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from finqon.api.dependencies import ChatServiceDep, CurrentUserDep
from finqon.schemas.conversations import (
    ConversationDetail,
    ConversationSummary,
    ConversationUpdate,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    user: CurrentUserDep,
    service: ChatServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ConversationSummary]:
    return await service.list_conversations(user.id, limit, offset)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, user: CurrentUserDep, service: ChatServiceDep
) -> ConversationDetail:
    return await service.get_conversation(user.id, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationSummary)
async def update_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    user: CurrentUserDep,
    service: ChatServiceDep,
) -> ConversationSummary:
    """Rename the chat, pin it, or both. Another user's chat is a 404."""
    return await service.update_conversation(user.id, conversation_id, body)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: CurrentUserDep, service: ChatServiceDep
) -> Response:
    """Delete the chat and its messages. Another user's chat is a 404."""
    await service.delete_conversation(user.id, conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
