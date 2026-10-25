import httpx
import openai
import pytest

from finqon.llm.client import ChatResult, LLMError, OpenAICompatibleProvider, ToolCall


def rate_limit_error(retry_after: str | None = None) -> openai.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after else {}
    request = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    response = httpx.Response(429, request=request, headers=headers)
    return openai.RateLimitError("slow down", response=response, body=None)


def make_provider(sleeps: list[float], max_retries: int = 3) -> OpenAICompatibleProvider:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return OpenAICompatibleProvider(
        base_url="http://llm.test/v1",
        api_key="k",
        model="m",
        max_retries=max_retries,
        sleep=fake_sleep,
    )


async def test_rate_limits_are_retried_with_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    provider = make_provider(sleeps)
    attempts = {"n": 0}

    async def flaky(messages, tools):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise rate_limit_error()
        return ChatResult(content="ok", model="m", prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(provider, "_complete", flaky)
    result = await provider.chat([{"role": "user", "content": "hi"}], None)

    assert result.content == "ok"
    assert attempts["n"] == 3
    assert len(sleeps) == 2
    assert sleeps[1] > sleeps[0]  # exponential growth
    assert result.latency_ms >= 0


async def test_retry_after_header_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    provider = make_provider(sleeps)
    calls = {"n": 0}

    async def once(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            raise rate_limit_error(retry_after="7")
        return ChatResult(content="ok")

    monkeypatch.setattr(provider, "_complete", once)
    await provider.chat([], None)
    assert sleeps == [7.0]


async def test_gives_up_after_max_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    provider = make_provider(sleeps, max_retries=2)

    async def always(messages, tools):
        raise rate_limit_error()

    monkeypatch.setattr(provider, "_complete", always)
    with pytest.raises(LLMError) as info:
        await provider.chat([], None)
    assert info.value.code == "llm_rate_limited"
    assert len(sleeps) == 2


async def test_streaming_flag_uses_the_streaming_path(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = make_provider([])
    used: list[str] = []

    async def blocking(messages, tools):
        used.append("blocking")
        return ChatResult(content="a")

    async def streaming(messages, tools, on_text=None):
        used.append("streaming")
        return ChatResult(content="b")

    monkeypatch.setattr(provider, "_complete", blocking)
    monkeypatch.setattr(provider, "_complete_streaming", streaming)
    await provider.chat([], None, stream=False)
    await provider.chat([], None, stream=True)
    assert used == ["blocking", "streaming"]


def test_assistant_message_carries_tool_calls() -> None:
    result = ChatResult(content=None, tool_calls=[ToolCall("c1", "run_sql_query", '{"a":1}')])
    message = result.assistant_message()
    assert message["role"] == "assistant"
    assert message["tool_calls"][0]["id"] == "c1"
    assert message["tool_calls"][0]["function"]["name"] == "run_sql_query"
    assert "extra_content" not in message["tool_calls"][0]


def test_assistant_message_replays_gemini_thought_signature() -> None:
    signature = {"google": {"thought_signature": "abc"}}
    result = ChatResult(
        content=None,
        tool_calls=[
            ToolCall("c1", "run_sql_query", '{"a":1}', extra_content=signature),
        ],
        extra_content={"google": {"thought_signature": "msg"}},
    )
    message = result.assistant_message()
    assert message["extra_content"] == {"google": {"thought_signature": "msg"}}
    assert message["tool_calls"][0]["extra_content"] == signature
