"""Model gateway: one OpenAI-compatible client for Gemini, OpenRouter, or any other host.

Switching providers only changes ``FINQON_LLM_BASE_URL``, ``FINQON_LLM_API_KEY`` and
``FINQON_LLM_MODEL``. Rate limits and transient failures are retried with exponential
backoff and jitter.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

Message = Mapping[str, Any]
ToolSpec = Mapping[str, Any]
TextSink = Callable[[str], Awaitable[None]]

_RETRYABLE = (
    openai.RateLimitError,
    openai.APIConnectionError,  # includes APITimeoutError
    openai.InternalServerError,
)
_MAX_BACKOFF_SECONDS = 20.0


class LLMError(RuntimeError):
    """The model could not be reached or rejected the request."""

    def __init__(self, message: str, *, code: str = "llm_unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model
    # Gemini 3+ OpenAI-compat: extra_content.google.thought_signature. Must be replayed.
    extra_content: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str | None = None
    extra_content: dict[str, Any] | None = None

    def assistant_message(self) -> dict[str, Any]:
        """The assistant turn to append to the transcript before tool results.

        Unknown provider fields (Gemini ``thought_signature``) are replayed unchanged.
        Dropping them makes Gemini 3 reject the next request with HTTP 400.
        """
        message: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.extra_content:
            message["extra_content"] = self.extra_content
        if self.tool_calls:
            calls = []
            for call in self.tool_calls:
                item: dict[str, Any] = {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                if call.extra_content:
                    item["extra_content"] = call.extra_content
                calls.append(item)
            message["tool_calls"] = calls
        return message


class LLMProvider(Protocol):
    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        stream: bool = False,
        on_text: TextSink | None = None,
    ) -> ChatResult:
        """Run one completion.

        With ``stream=True`` the provider's event stream is consumed and aggregated into
        the same ``ChatResult``; this keeps long generations from hitting read timeouts.
        ``on_text`` receives each text delta as it arrives (and implies streaming).
        """
        ...


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        max_retries: int = 4,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        # SDK retries are disabled: backoff is handled here so it is observable and testable.
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout, max_retries=0
        )
        self._model = model
        self._max_retries = max_retries
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._client.close()

    async def chat(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        stream: bool = False,
        on_text: TextSink | None = None,
    ) -> ChatResult:
        started = time.perf_counter()
        attempt = 0
        emitted = False

        async def sink(piece: str) -> None:
            nonlocal emitted
            emitted = True
            if on_text is not None:
                await on_text(piece)

        while True:
            try:
                result = await (
                    self._complete_streaming(messages, tools, sink if on_text else None)
                    if stream or on_text
                    else self._complete(messages, tools)
                )
                return _with_latency(result, started)
            except _RETRYABLE as exc:
                if emitted or attempt >= self._max_retries:
                    raise LLMError(
                        "The model is busy or unreachable. Please try again.",
                        code="llm_rate_limited"
                        if isinstance(exc, openai.RateLimitError)
                        else "llm_unavailable",
                    ) from exc
                delay = _backoff_delay(attempt, exc)
                logger.warning(
                    "llm_retry",
                    extra={
                        "attempt": attempt + 1,
                        "delay_s": round(delay, 2),
                        "error": type(exc).__name__,
                    },
                )
                attempt += 1
                await self._sleep(delay)
            except openai.AuthenticationError as exc:
                raise LLMError("The model API key was rejected.", code="llm_auth") from exc
            except openai.APIStatusError as exc:
                body = str(getattr(exc, "body", None) or exc)[:500]
                logger.error("llm_rejected", extra={"status": exc.status_code, "body": body})
                raise LLMError(f"The model rejected the request ({exc.status_code}).") from exc

    def _request(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._model,
            "messages": list(messages),
            "temperature": 0,
        }
        if tools:
            params["tools"] = list(tools)
            params["tool_choice"] = "auto"
        return params

    async def _complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] | None
    ) -> ChatResult:
        response = await self._client.chat.completions.create(**self._request(messages, tools))
        choice = response.choices[0]
        calls = [
            ToolCall(
                id=call.id or f"call_{i}",
                name=call.function.name,
                arguments=call.function.arguments or "{}",
                extra_content=_extra_content(call),
            )
            for i, call in enumerate(choice.message.tool_calls or [])
            if call.type == "function"
        ]
        usage = response.usage
        return ChatResult(
            content=choice.message.content,
            tool_calls=calls,
            model=response.model or self._model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason,
            extra_content=_extra_content(choice.message),
        )

    async def _complete_streaming(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        on_text: TextSink | None = None,
    ) -> ChatResult:
        params = self._request(messages, tools)
        stream = await self._client.chat.completions.create(
            **params, stream=True, stream_options={"include_usage": True}
        )
        parts: list[str] = []
        pending: dict[int, _PendingCall] = {}
        prompt_tokens = completion_tokens = 0
        finish_reason: str | None = None
        model = self._model
        async for chunk in stream:
            model = chunk.model or model
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens
            for choice in chunk.choices:
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                if delta.content:
                    parts.append(delta.content)
                    if on_text is not None:
                        await on_text(delta.content)
                for call in delta.tool_calls or []:
                    slot = pending.setdefault(
                        call.index if call.index is not None else len(pending), _PendingCall()
                    )
                    slot.id = call.id or slot.id
                    extra = _extra_content(call)
                    if extra:
                        slot.extra_content = extra
                    if call.function:
                        slot.name += call.function.name or ""
                        slot.arguments += call.function.arguments or ""
        calls = [
            ToolCall(
                id=slot.id or f"call_{i}",
                name=slot.name,
                arguments=slot.arguments or "{}",
                extra_content=slot.extra_content,
            )
            for i, (_, slot) in enumerate(sorted(pending.items()))
        ]
        return ChatResult(
            content="".join(parts) or None,
            tool_calls=calls,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
        )


@dataclass(slots=True)
class _PendingCall:
    """A tool call being assembled from streamed deltas."""

    id: str = ""
    name: str = ""
    arguments: str = ""
    extra_content: dict[str, Any] | None = None


def _extra_content(obj: Any) -> dict[str, Any] | None:
    """Copy provider extensions such as Gemini ``thought_signature``."""
    extra = getattr(obj, "extra_content", None)
    if extra is None:
        extra = (getattr(obj, "model_extra", None) or {}).get("extra_content")
    if extra is None and hasattr(obj, "model_dump"):
        extra = obj.model_dump(exclude_unset=True).get("extra_content")
    if extra is None:
        return None
    if hasattr(extra, "model_dump"):
        extra = extra.model_dump()
    return extra if isinstance(extra, dict) and extra else None


def _with_latency(result: ChatResult, started: float) -> ChatResult:
    return ChatResult(
        content=result.content,
        tool_calls=result.tool_calls,
        model=result.model,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000),
        finish_reason=result.finish_reason,
        extra_content=result.extra_content,
    )


def _backoff_delay(attempt: int, exc: Exception) -> float:
    """Exponential backoff with jitter; honours a provider ``Retry-After`` header."""
    response = getattr(exc, "response", None)
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        try:
            return min(float(retry_after), _MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(2.0**attempt + random.uniform(0, 0.5), _MAX_BACKOFF_SECONDS)
