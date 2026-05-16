"""Reranker layer. NoOp keeps RRF order; FlashRank does a cross-encoder pass.
flashrank is imported lazily so installations without the extra still work.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

import structlog

from bot.config import Settings
from bot.services.retrieval import RankedChunk

logger = structlog.get_logger(__name__)


class Reranker(Protocol):
    async def rerank(
        self,
        query: str,
        items: list[RankedChunk],
    ) -> list[RankedChunk]: ...


class NoOpReranker:
    async def rerank(
        self,
        query: str,  # noqa: ARG002
        items: list[RankedChunk],
    ) -> list[RankedChunk]:
        return items


class FlashRankReranker:
    def __init__(self, model_name: str = "ms-marco-MiniLM-L-12-v2") -> None:
        from flashrank import Ranker  # noqa: PLC0415

        self._ranker = Ranker(model_name=model_name)

    async def rerank(
        self,
        query: str,
        items: list[RankedChunk],
    ) -> list[RankedChunk]:
        if not items:
            return items

        ranker = self._ranker

        def _score() -> list[RankedChunk]:
            from flashrank import RerankRequest  # noqa: PLC0415

            passages = [{"id": i, "text": item.chunk.text} for i, item in enumerate(items)]
            request = RerankRequest(query=query, passages=passages)
            ranked = ranker.rerank(request)
            scored: dict[int, float] = {int(entry["id"]): float(entry["score"]) for entry in ranked}
            for idx, item in enumerate(items):
                item.rerank_score = scored.get(idx)
            return sorted(
                items,
                key=lambda x: x.rerank_score if x.rerank_score is not None else -1.0,
                reverse=True,
            )

        return await asyncio.to_thread(_score)


def make_reranker(settings: Settings) -> Reranker:
    if not settings.rerank_enabled:
        return NoOpReranker()
    try:
        return FlashRankReranker()
    except ImportError as exc:
        logger.warning("rerank.flashrank_missing", error=str(exc))
        return NoOpReranker()


__all__ = ["FlashRankReranker", "NoOpReranker", "Reranker", "make_reranker"]
