"""Hybrid retrieval: dense (FAISS cosine) + BM25Okapi merged via Reciprocal
Rank Fusion. Dense and BM25 run concurrently; BM25 sync work is offloaded to a
thread.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import cast

import structlog
from rank_bm25 import BM25Okapi

from bot.observability.metrics import vector_store_op_duration_seconds
from bot.services.chunking import Chunk
from bot.services.embeddings import VectorStore
from bot.services.openai_client import OpenAIClient

logger = structlog.get_logger(__name__)

_TOKEN_RE = re.compile(r"\w+", flags=re.UNICODE)


@dataclass(slots=True)
class RankedChunk:
    chunk: Chunk
    rrf_score: float
    dense_rank: int | None = None
    bm25_rank: int | None = None
    dense_score: float | None = None
    bm25_score: float | None = None
    rerank_score: float | None = None


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore,
        openai: OpenAIClient,
        *,
        bm25_enabled: bool = True,
        rrf_k: int = 60,
        top_k_dense: int = 50,
        top_k_bm25: int = 50,
    ) -> None:
        self._store = store
        self._openai = openai
        self._bm25_enabled = bm25_enabled
        self._rrf_k = rrf_k
        self._top_k_dense = top_k_dense
        self._top_k_bm25 = top_k_bm25
        self._bm25: BM25Okapi | None = None
        self._bm25_chunks: tuple[Chunk, ...] = ()
        self._lock = asyncio.Lock()

    @property
    def store(self) -> VectorStore:
        return self._store

    async def rebuild_corpus(self, chunks: list[Chunk]) -> None:
        if not self._bm25_enabled or not chunks:
            async with self._lock:
                self._bm25 = None
                self._bm25_chunks = tuple(chunks) if self._bm25_enabled else ()
            return
        tokenized = [_tokenize(c.text) for c in chunks]
        bm25 = await asyncio.to_thread(BM25Okapi, tokenized)
        async with self._lock:
            self._bm25 = bm25
            self._bm25_chunks = tuple(chunks)

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 10,
    ) -> list[RankedChunk]:
        if self._store.size == 0:
            return []
        embeddings = await self._openai.get_embeddings_batch([query])
        if not embeddings:
            return []
        query_vec = embeddings[0]

        dense_task = self._store.search(query_vec, top_k=self._top_k_dense)
        bm25_task = self._bm25_top_n(query)
        dense_results, bm25_results = await asyncio.gather(dense_task, bm25_task)

        return self._rrf_merge(dense_results, bm25_results, top_k=top_k)

    async def _bm25_top_n(self, query: str) -> list[tuple[Chunk, float, int]]:
        if not self._bm25_enabled or self._bm25 is None or not self._bm25_chunks:
            return []
        bm25 = self._bm25
        chunks = self._bm25_chunks
        tokens = _tokenize(query)
        if not tokens:
            return []

        def _score() -> list[tuple[Chunk, float, int]]:
            scores = bm25.get_scores(tokens)
            order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            results: list[tuple[Chunk, float, int]] = []
            for rank, idx in enumerate(order[: self._top_k_bm25], start=1):
                score = float(scores[idx])
                if score <= 0:
                    break
                results.append((chunks[idx], score, rank))
            return results

        started = time.perf_counter()
        results = await asyncio.to_thread(_score)
        vector_store_op_duration_seconds.labels(op="bm25").observe(
            time.perf_counter() - started,
        )
        return results

    def _rrf_merge(
        self,
        dense: list[tuple[Chunk, float]],
        bm25: list[tuple[Chunk, float, int]],
        *,
        top_k: int,
    ) -> list[RankedChunk]:
        merged: dict[tuple[str, int], RankedChunk] = {}

        for rank, (chunk, score) in enumerate(dense, start=1):
            key = (chunk.filename, chunk.chunk_index)
            merged[key] = RankedChunk(
                chunk=chunk,
                rrf_score=1.0 / (self._rrf_k + rank),
                dense_rank=rank,
                dense_score=score,
            )

        for chunk, score, rank in bm25:
            key = (chunk.filename, chunk.chunk_index)
            existing = merged.get(key)
            contribution = 1.0 / (self._rrf_k + rank)
            if existing is None:
                merged[key] = RankedChunk(
                    chunk=chunk,
                    rrf_score=contribution,
                    bm25_rank=rank,
                    bm25_score=score,
                )
            else:
                existing.rrf_score += contribution
                existing.bm25_rank = rank
                existing.bm25_score = score

        ordered = sorted(merged.values(), key=_sort_key, reverse=True)
        return ordered[:top_k]


def _tokenize(text: str) -> list[str]:
    return cast("list[str]", _TOKEN_RE.findall(text.lower()))


def _sort_key(item: RankedChunk) -> tuple[float, float]:
    return (item.rrf_score, item.dense_score if item.dense_score is not None else 0.0)


__all__ = ["HybridRetriever", "RankedChunk"]
