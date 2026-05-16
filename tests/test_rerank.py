"""Tests for bot.services.rerank — spec A1 §3.6.

NoOp = identity. Factory returns NoOp when disabled; FlashRank when enabled.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from bot.config import Settings
from bot.services.chunking import Chunk
from bot.services.rerank import NoOpReranker, make_reranker
from bot.services.retrieval import RankedChunk


def _ranked(text: str, rrf: float) -> RankedChunk:
    return RankedChunk(
        chunk=Chunk(text=text, filename="a.md", chunk_index=0, char_start=0, char_end=len(text)),
        rrf_score=rrf,
    )


def _settings(rerank_enabled: bool = False) -> Settings:
    return Settings(  # type: ignore[call-arg]
        bot_token="dummy",
        openai_api_key="dummy",
        api_admin_token="dummy",
        admin_ids=[1],
        rerank_enabled=rerank_enabled,
    )


class TestNoOpReranker:
    async def test_identity(self) -> None:
        items = [_ranked(f"t{i}", 1.0 - i * 0.1) for i in range(5)]
        rer = NoOpReranker()
        result = await rer.rerank("query", items)
        assert result == items

    async def test_empty(self) -> None:
        rer = NoOpReranker()
        result = await rer.rerank("query", [])
        assert result == []


class TestMakeReranker:
    def test_disabled_returns_noop(self) -> None:
        rer = make_reranker(_settings(rerank_enabled=False))
        assert isinstance(rer, NoOpReranker)

    def test_enabled_without_flashrank_falls_back(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Hide flashrank module if installed; otherwise just patch import to raise
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "flashrank" or name.startswith("flashrank."):
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        monkeypatch.delitem(sys.modules, "flashrank", raising=False)

        rer = make_reranker(_settings(rerank_enabled=True))
        assert isinstance(rer, NoOpReranker)

    def test_enabled_with_flashrank_returns_flashrank(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_module = MagicMock()
        fake_module.Ranker = MagicMock(return_value=MagicMock())
        monkeypatch.setitem(sys.modules, "flashrank", fake_module)

        from bot.services.rerank import FlashRankReranker  # noqa: PLC0415

        rer = make_reranker(_settings(rerank_enabled=True))
        assert isinstance(rer, FlashRankReranker)
