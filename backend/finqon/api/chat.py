from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from finqon.api.dependencies import ChatServiceDep, CurrentUserDep
from finqon.schemas.chat import ChatRequest, encode_sse

router = APIRouter(tags=["chat"])

_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",  # keep reverse proxies from buffering the stream
}

CHAT_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "Server-Sent Events. Each frame is `event: <type>` and `data: <ChatEvent JSON>`. "
            "See the `ChatEvent` schema."
        ),
        "content": {"text/event-stream": {"schema": {"$ref": "#/components/schemas/ChatEvent"}}},
    },
    401: {"description": "Missing, invalid or expired access token."},
    404: {"description": "Conversation not found."},
}


@router.post("/chat", responses=CHAT_RESPONSES)
async def chat(
    body: ChatRequest, user: CurrentUserDep, service: ChatServiceDep
) -> StreamingResponse:
    # Resolve the conversation before streaming so ownership problems are real 404s.
    conversation = await service.open_conversation(user.id, body.conversation_id, body.message)

    async def frames() -> AsyncIterator[str]:
        async for event in service.stream_turn(user.id, conversation, body.message):
            yield encode_sse(event)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={**_SSE_HEADERS, "X-Conversation-Id": str(conversation.id)},
    )
