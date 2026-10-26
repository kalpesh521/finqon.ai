"""Configuration, composition and the service hook for the semantic cache."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from finqon.agent.contracts import AgentDeps, TurnInput
from finqon.cache.redis_store import RedisStore
from finqon.cache.store import MemoryStore
from finqon.core.config import ConfigurationError
from finqon.resources import Resources, _build_cache
from finqon.schemas.chat import ChatEvent, DoneEvent, TokenEvent, TokenUsage
from finqon.services.chat import ChatService, Gate, OpenedConversation
from tests.conftest import unit_settings

USER = uuid.uuid4()


class Embedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 768 for _ in texts]


# ---------------------------------------------------------------------------- settings


def test_the_cache_is_off_by_default() -> None:
    settings = unit_settings()
    assert settings.cache_mode == "off" and _build_cache(settings, Embedder()) is None


@pytest.mark.parametrize("threshold", [0.5, 0.79, 1.01])
def test_an_unsafe_similarity_threshold_is_refused(threshold: float) -> None:
    with pytest.raises(ValidationError, match="cache_similarity_threshold"):
        unit_settings(cache_similarity_threshold=threshold)


@pytest.mark.parametrize(
    "name", ["cache_ttl_seconds", "cache_op_timeout_ms", "cache_embed_timeout_ms"]
)
def test_non_positive_budgets_are_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        unit_settings(**{name: 0})


def test_redis_backend_requires_a_url_when_the_cache_is_on() -> None:
    settings = unit_settings(cache_mode="on", cache_backend="redis")
    with pytest.raises(ConfigurationError, match="FINQON_REDIS_URL"):
        settings.require_runtime_settings()
    unit_settings(cache_mode="off", cache_backend="redis").require_runtime_settings()
    unit_settings(
        cache_mode="on", cache_backend="redis", redis_url="redis://localhost:6379/0"
    ).require_runtime_settings()


# --------------------------------------------------------------------------- building


def test_memory_backend_builds_a_memory_store() -> None:
    cache = _build_cache(unit_settings(cache_mode="on", cache_backend="memory"), Embedder())
    assert cache is not None and isinstance(cache._store, MemoryStore)


def test_redis_backend_builds_a_redis_store_without_connecting() -> None:
    settings = unit_settings(
        cache_mode="shadow", cache_backend="redis", redis_url="redis://localhost:6399/0"
    )
    cache = _build_cache(settings, Embedder())
    assert cache is not None and isinstance(cache._store, RedisStore)
    assert cache.similarity_threshold == settings.cache_similarity_threshold


async def test_forgetting_cached_answers_never_raises() -> None:
    class Failing:
        async def purge_user(self, _: uuid.UUID) -> int:
            raise ConnectionError("redis down")

    resources = SimpleNamespace(cache=Failing())
    await Resources.forget_cached_answers(resources, USER)  # type: ignore[arg-type]
    await Resources.forget_cached_answers(SimpleNamespace(cache=None), USER)  # type: ignore[arg-type]


# --------------------------------------------------------------------- the status probe


async def test_status_reports_off_when_there_is_no_cache() -> None:
    resources = SimpleNamespace(settings=unit_settings(), cache=None)
    assert await Resources.cache_status(resources) == {  # type: ignore[arg-type]
        "mode": "off",
        "enabled": False,
    }


async def test_status_reports_counters_and_store_reachability() -> None:
    settings = unit_settings(cache_mode="shadow", cache_backend="memory")
    cache = _build_cache(settings, Embedder())
    assert cache is not None
    cache.metrics.incr("lookup", 4)
    cache.metrics.incr("hit_exact", 1)
    status = await Resources.cache_status(SimpleNamespace(settings=settings, cache=cache))  # type: ignore[arg-type]
    assert status["mode"] == "shadow" and status["enabled"] is True
    assert status["backend"] == "memory" and status["store_reachable"] is True
    assert status["hit_rate"] == 0.25 and status["breaker_open"] is False
    assert status["counters"] == {"lookup": 4, "hit_exact": 1}


async def test_status_survives_an_unreachable_store() -> None:
    settings = unit_settings(cache_mode="on", cache_backend="memory")
    cache = _build_cache(settings, Embedder())
    assert cache is not None

    async def down() -> None:
        raise ConnectionError("redis down")

    cache.ping = down  # type: ignore[method-assign]
    status = await Resources.cache_status(SimpleNamespace(settings=settings, cache=cache))  # type: ignore[arg-type]
    assert status["store_reachable"] is False


def test_the_status_endpoint_is_public_and_always_200() -> None:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from finqon.main import create_app  # noqa: PLC0415
    from tests.unit.test_orchestrator import ScriptedLLM  # noqa: PLC0415

    app = create_app(unit_settings(cache_mode="on", cache_backend="memory"), llm=ScriptedLLM([]))
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/health/cache")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True and body["mode"] == "on" and body["counters"] == {}


# --------------------------------------------------------------------- the service hook


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self, turn: TurnInput, deps: AgentDeps, *, data_version: str
    ) -> AsyncIterator[ChatEvent]:
        self.calls.append(data_version)
        yield TokenEvent(text="from the runner")
        yield DoneEvent(
            message_id=uuid.uuid4(), conversation_id=turn.conversation_id, usage=TokenUsage()
        )


def service(runner: Any) -> ChatService:
    resources = SimpleNamespace(
        settings=unit_settings(),
        llm=None,
        executor=None,
        catalog=None,
        embedder=Embedder(),
        turn_runner=runner,
    )
    return ChatService(resources)  # type: ignore[arg-type]


def opened(version: str = "ledger-7") -> OpenedConversation:
    return OpenedConversation(uuid.uuid4(), [], None, [], version)


async def test_turns_go_through_the_runner_with_the_ledger_version() -> None:
    runner = RecordingRunner()
    events = [e async for e in service(runner).stream_turn(USER, opened("ledger-7"), "q")]
    assert runner.calls == ["ledger-7"]
    assert any(isinstance(e, TokenEvent) and e.text == "from the runner" for e in events)


async def test_canned_replies_never_touch_the_cache() -> None:
    runner = RecordingRunner()
    svc = service(runner)

    async def save(_: Any) -> uuid.UUID:
        return uuid.uuid4()

    svc._save_turn = save  # type: ignore[method-assign]
    gate = Gate("hi", "Hello!", "info")
    events = [e async for e in svc.stream_turn(USER, opened(), "hi", gate)]
    assert runner.calls == []
    assert any(isinstance(e, TokenEvent) for e in events)


def test_the_turn_input_dates_are_what_the_cache_keys_on() -> None:
    turn = TurnInput(
        user_id=USER, conversation_id=uuid.uuid4(), question="q", today=date(2026, 10, 21)
    )
    assert turn.today == date(2026, 10, 21)
