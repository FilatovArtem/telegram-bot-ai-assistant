"""Tests for bot.services.openai_client — spec A1 §3.3, A3 §7.

Mocks AsyncOpenAI .embeddings.create / .chat.completions.create at the
internal client level so the real OpenAI SDK is never reached.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import APITimeoutError, RateLimitError

from bot.services.openai_client import SYSTEM_PROMPT, OpenAIClient


def _fake_embedding_response(texts: list[str]) -> Any:
    data: list[Any] = []
    for i, _ in enumerate(texts):
        item = MagicMock()
        item.index = i
        item.embedding = [0.0] * 1536
        data.append(item)
    resp = MagicMock()
    resp.data = data
    resp.usage = MagicMock(total_tokens=len(texts) * 5)
    return resp


def _fake_chat_response(content: str = "answer") -> Any:
    resp = MagicMock()
    choice = MagicMock()
    choice.message = MagicMock(content=content)
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=12, completion_tokens=24)
    return resp


def _rate_limit_error() -> RateLimitError:
    response = MagicMock()
    response.request = MagicMock()
    body: dict[str, Any] = {"error": {"message": "rate"}}
    return RateLimitError("rate", response=response, body=body)


def _timeout_error() -> APITimeoutError:
    return APITimeoutError(MagicMock())


def _make_client(monkeypatch: pytest.MonkeyPatch, max_retries: int = 6) -> OpenAIClient:
    client = OpenAIClient(
        api_key="dummy",
        chat_model="gpt-4o-mini",
        embed_model="text-embedding-3-small",
        timeout=5.0,
        max_retries=max_retries,
    )
    return client


class TestEmbeddings:
    async def test_empty_input_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        result = await client.get_embeddings_batch([])
        assert result == []

    async def test_batch_single_call_for_50(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(side_effect=lambda model, input: _fake_embedding_response(input))
        monkeypatch.setattr(client._client.embeddings, "create", create)
        texts = [f"text {i}" for i in range(50)]
        result = await client.get_embeddings_batch(texts)
        assert len(result) == 50
        assert create.call_count == 1

    async def test_batch_splits_at_100(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(side_effect=lambda model, input: _fake_embedding_response(input))
        monkeypatch.setattr(client._client.embeddings, "create", create)
        texts = [f"t{i}" for i in range(250)]
        result = await client.get_embeddings_batch(texts)
        assert len(result) == 250
        assert create.call_count == 3

    async def test_preserves_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)

        def shuffled(model: str, input: list[str]) -> Any:
            data: list[Any] = []
            for i, _ in enumerate(input):
                item = MagicMock()
                item.index = i
                item.embedding = [float(i)] * 1536
                data.append(item)
            data.reverse()
            resp = MagicMock()
            resp.data = data
            resp.usage = MagicMock(total_tokens=1)
            return resp

        monkeypatch.setattr(
            client._client.embeddings,
            "create",
            AsyncMock(side_effect=shuffled),
        )
        result = await client.get_embeddings_batch(["a", "b", "c"])
        assert result[0][0] == 0.0
        assert result[1][0] == 1.0
        assert result[2][0] == 2.0

    async def test_retries_on_rate_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch, max_retries=4)
        seq = [
            _rate_limit_error(),
            _rate_limit_error(),
            _rate_limit_error(),
            _fake_embedding_response(["ok"]),
        ]
        create = AsyncMock(side_effect=seq)
        monkeypatch.setattr(client._client.embeddings, "create", create)

        async def no_sleep(*_a: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr("asyncio.sleep", no_sleep)
        result = await client.get_embeddings_batch(["ok"])
        assert len(result) == 1
        assert create.call_count == 4

    async def test_propagates_non_retryable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(side_effect=ValueError("bad request"))
        monkeypatch.setattr(client._client.embeddings, "create", create)
        with pytest.raises(ValueError, match="bad request"):
            await client.get_embeddings_batch(["x"])

    async def test_tokens_metric_increments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from bot.observability.metrics import openai_tokens_total  # noqa: PLC0415

        client = _make_client(monkeypatch)
        create = AsyncMock(side_effect=lambda model, input: _fake_embedding_response(input))
        monkeypatch.setattr(client._client.embeddings, "create", create)
        before = openai_tokens_total.labels(
            model="text-embedding-3-small",
            type="embedding",
        )._value.get()
        await client.get_embeddings_batch(["one", "two"])
        after = openai_tokens_total.labels(
            model="text-embedding-3-small",
            type="embedding",
        )._value.get()
        assert after > before


class TestChat:
    async def test_chat_calls_with_temperature_and_max_tokens(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(return_value=_fake_chat_response("hi"))
        monkeypatch.setattr(client._client.chat.completions, "create", create)

        result = await client.chat(question="q?", context_block="ctx")
        assert result.text == "hi"
        kwargs = create.call_args.kwargs
        assert kwargs["temperature"] == pytest.approx(0.3)
        assert kwargs["max_tokens"] == 500
        assert kwargs["model"] == "gpt-4o-mini"

    async def test_chat_includes_system_prompt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(return_value=_fake_chat_response("ok"))
        monkeypatch.setattr(client._client.chat.completions, "create", create)
        await client.chat(question="что?", context_block="ctx-block")
        msgs = create.call_args.kwargs["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == SYSTEM_PROMPT
        assert "CONTEXT:" in msgs[1]["content"]
        assert "ctx-block" in msgs[1]["content"]

    async def test_chat_respects_custom_system_prompt(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(return_value=_fake_chat_response())
        monkeypatch.setattr(client._client.chat.completions, "create", create)
        await client.chat(question="q", context_block="c", system_prompt="custom")
        assert create.call_args.kwargs["messages"][0]["content"] == "custom"

    async def test_chat_retries_on_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch, max_retries=3)
        create = AsyncMock(
            side_effect=[_timeout_error(), _timeout_error(), _fake_chat_response("ok")],
        )
        monkeypatch.setattr(client._client.chat.completions, "create", create)

        async def no_sleep(*_a: Any, **_kw: Any) -> None:
            return None

        monkeypatch.setattr("asyncio.sleep", no_sleep)
        result = await client.chat(question="q", context_block="c")
        assert result.text == "ok"
        assert create.call_count == 3

    async def test_chat_propagates_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(side_effect=ValueError("bad"))
        monkeypatch.setattr(client._client.chat.completions, "create", create)
        with pytest.raises(ValueError):
            await client.chat(question="q", context_block="c")

    async def test_chat_cache_key_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client(monkeypatch)
        create = AsyncMock(return_value=_fake_chat_response())
        monkeypatch.setattr(client._client.chat.completions, "create", create)
        await client.chat(question="q", context_block="c", cache_key="k123")
        assert create.call_args.kwargs.get("prompt_cache_key") == "k123"


class TestSystemPrompt:
    def test_contains_anti_injection_clause(self) -> None:
        assert "ДАННЫЕ" in SYSTEM_PROMPT or "data" in SYSTEM_PROMPT.lower()

    def test_contains_not_found_phrase(self) -> None:
        assert "не нашёл" in SYSTEM_PROMPT

    def test_contains_citation_marker(self) -> None:
        assert "[^" in SYSTEM_PROMPT

    def test_forbids_role_change(self) -> None:
        assert "роль" in SYSTEM_PROMPT.lower() or "role" in SYSTEM_PROMPT.lower()
