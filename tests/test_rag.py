"""Tests for bot.services.rag.ask() — spec A1 §3.7, design §4.4.

Pipeline behavior is tested via stub retriever + mock OpenAI. Real implementations
of retrieval/embeddings are exercised in their own files.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from bot.services.chunking import Chunk
from bot.services.rag import (
    EMPTY_RESPONSE,
    INVALID_QUERY_RESPONSE,
    LOW_RELEVANCE_RESPONSE,
    ask,
)
from bot.services.rerank import NoOpReranker
from bot.services.retrieval import RankedChunk


class StubStore:
    def __init__(self, size: int) -> None:
        self._size = size

    @property
    def size(self) -> int:
        return self._size


class StubRetriever:
    def __init__(self, *, store_size: int = 1, ranked: list[RankedChunk] | None = None) -> None:
        self.store = StubStore(store_size)
        self._ranked = ranked or []
        self.retrieve_calls: list[str] = []

    async def retrieve(self, query: str, *, top_k: int = 10) -> list[RankedChunk]:
        self.retrieve_calls.append(query)
        return self._ranked


def _ranked(text: str, *, dense: float | None, filename: str = "a.md", idx: int = 0) -> RankedChunk:
    return RankedChunk(
        chunk=Chunk(
            text=text,
            filename=filename,
            chunk_index=idx,
            char_start=0,
            char_end=len(text),
        ),
        rrf_score=dense if dense is not None else 0.01,
        dense_score=dense,
        dense_rank=1 if dense is not None else None,
    )


class TestEdgeCases:
    async def test_invalid_query_too_long(self, mock_openai: Any) -> None:
        retriever = StubRetriever()
        result = await ask(
            "x" * 5000,
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
        )
        assert result.answered is False
        assert result.answer == INVALID_QUERY_RESPONSE
        assert len(mock_openai.chat_calls) == 0

    async def test_empty_store(self, mock_openai: Any) -> None:
        retriever = StubRetriever(store_size=0)
        result = await ask(
            "что у вас есть?",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
        )
        assert result.answered is False
        assert result.answer == EMPTY_RESPONSE
        assert len(mock_openai.chat_calls) == 0

    async def test_no_candidates(self, mock_openai: Any) -> None:
        retriever = StubRetriever(store_size=1, ranked=[])
        result = await ask(
            "что-то",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
        )
        assert result.answered is False
        assert result.answer == LOW_RELEVANCE_RESPONSE
        assert len(mock_openai.chat_calls) == 0


class TestGate:
    async def test_below_threshold_no_llm(self, mock_openai: Any) -> None:
        ranked = [_ranked(f"snippet {i}", dense=0.5) for i in range(3)]
        retriever = StubRetriever(store_size=3, ranked=ranked)
        result = await ask(
            "qq",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
        )
        assert result.answered is False
        assert result.answer == LOW_RELEVANCE_RESPONSE
        assert len(mock_openai.chat_calls) == 0

    async def test_small_gap_fails(self, mock_openai: Any) -> None:
        ranked = [
            _ranked("top", dense=0.80, idx=0),
            _ranked("near1", dense=0.79, idx=1),
            _ranked("near2", dense=0.78, idx=2),
        ]
        retriever = StubRetriever(store_size=3, ranked=ranked)
        result = await ask(
            "q",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
            min_gap=0.05,
        )
        assert result.answered is False
        assert len(mock_openai.chat_calls) == 0

    async def test_passes_calls_llm(self, mock_openai: Any) -> None:
        ranked = [
            _ranked("best", dense=0.95, idx=0),
            _ranked("ok", dense=0.50, idx=1),
        ]
        retriever = StubRetriever(store_size=2, ranked=ranked)
        result = await ask(
            "q",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
            min_gap=0.05,
        )
        assert result.answered is True
        assert len(mock_openai.chat_calls) == 1

    async def test_single_chunk_skips_gap(self, mock_openai: Any) -> None:
        ranked = [_ranked("only", dense=0.80, idx=0)]
        retriever = StubRetriever(store_size=1, ranked=ranked)
        result = await ask(
            "q",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
            min_gap=0.05,
        )
        assert result.answered is True


class TestResult:
    async def test_sources_dedup(self, mock_openai: Any) -> None:
        ranked = [
            _ranked("a1", dense=0.90, filename="a.md", idx=0),
            _ranked("a2", dense=0.50, filename="a.md", idx=1),
            _ranked("b1", dense=0.40, filename="b.md", idx=0),
        ]
        retriever = StubRetriever(store_size=3, ranked=ranked)
        result = await ask(
            "q",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
            min_gap=0.05,
        )
        assert result.sources == ["a.md", "b.md"]

    async def test_latency_ms_positive(self, mock_openai: Any) -> None:
        ranked = [_ranked("x", dense=0.90, idx=0)]
        retriever = StubRetriever(store_size=1, ranked=ranked)
        # Force at least 1ms by tiny sleep on chat
        original = mock_openai.chat

        async def slow_chat(**kw: Any) -> Any:
            time.sleep(0.002)
            return await original(**kw)

        mock_openai.chat = slow_chat
        result = await ask(
            "q",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
        )
        assert result.latency_ms >= 0

    async def test_top_scores_carry_dense(self, mock_openai: Any) -> None:
        ranked = [
            _ranked("a", dense=0.91, idx=0),
            _ranked("b", dense=0.45, idx=1),
        ]
        retriever = StubRetriever(store_size=2, ranked=ranked)
        result = await ask(
            "q",
            retriever=retriever,  # type: ignore[arg-type]
            reranker=NoOpReranker(),
            openai=mock_openai,
            threshold=0.75,
        )
        assert result.top_scores[0] == pytest.approx(0.91)
        assert result.top_scores[1] == pytest.approx(0.45)
