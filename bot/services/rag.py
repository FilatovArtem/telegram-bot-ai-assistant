"""Pure RAG pipeline. `ask(...)` is a free function that takes its
collaborators as keyword arguments to keep the call site testable without
Telegram/FastAPI runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from bot.observability.metrics import (
    rag_pipeline_duration_seconds,
    rag_relevance_score,
    rag_unanswered_total,
)
from bot.services.openai_client import OpenAIClient
from bot.services.rerank import NoOpReranker, Reranker
from bot.services.retrieval import HybridRetriever, RankedChunk
from bot.services.sanitizer import is_safe_for_query, sanitize_text

EMPTY_RESPONSE = "База знаний пуста. Свяжитесь с администратором."
LOW_RELEVANCE_RESPONSE = "Я не нашёл точного ответа в базе знаний. Передаю ваш вопрос оператору."
INVALID_QUERY_RESPONSE = (
    "Сообщение слишком длинное или содержит недопустимые символы. Пожалуйста, переформулируйте."
)

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class RAGResult:
    answer: str
    sources: list[str]
    top_scores: list[float]
    answered: bool
    latency_ms: int


async def ask(
    question: str,
    *,
    retriever: HybridRetriever,
    reranker: Reranker,
    openai: OpenAIClient,
    threshold: float = 0.75,
    min_gap: float = 0.05,
    top_k_final: int = 10,
    rerank_top_n: int = 5,
    cache_key: str | None = None,
) -> RAGResult:
    t0 = time.monotonic()
    sanitized = sanitize_text(question).strip()

    if not is_safe_for_query(sanitized):
        return _handoff(
            INVALID_QUERY_RESPONSE,
            sources=[],
            top_scores=[],
            t0=t0,
            reason="invalid_query",
        )

    if retriever.store.size == 0:
        return _handoff(
            EMPTY_RESPONSE,
            sources=[],
            top_scores=[],
            t0=t0,
            reason="empty_store",
        )

    ranked = await retriever.retrieve(sanitized, top_k=top_k_final)
    if not ranked:
        return _handoff(
            LOW_RELEVANCE_RESPONSE,
            sources=[],
            top_scores=[],
            t0=t0,
            reason="no_candidates",
        )

    if not isinstance(reranker, NoOpReranker):
        ranked = await reranker.rerank(sanitized, ranked[:rerank_top_n])

    top_scores = [_score(r) for r in ranked[:5]]
    top1 = top_scores[0] if top_scores else 0.0
    rag_relevance_score.observe(top1)

    if top1 < threshold:
        return _handoff(
            LOW_RELEVANCE_RESPONSE,
            sources=_dedup_sources(ranked[:3]),
            top_scores=top_scores,
            t0=t0,
            reason="low_top1",
        )

    if len(top_scores) >= 2:
        tail = top_scores[1:]
        gap = top1 - (sum(tail) / len(tail))
        if gap < min_gap:
            return _handoff(
                LOW_RELEVANCE_RESPONSE,
                sources=_dedup_sources(ranked[:3]),
                top_scores=top_scores,
                t0=t0,
                reason="small_gap",
            )

    context_block = _build_context(ranked)
    chat = await openai.chat(
        question=sanitized,
        context_block=context_block,
        cache_key=cache_key,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    rag_pipeline_duration_seconds.labels(stage="total").observe(latency_ms / 1000.0)

    return RAGResult(
        answer=chat.text,
        sources=_dedup_sources(ranked),
        top_scores=top_scores,
        answered=True,
        latency_ms=latency_ms,
    )


def _handoff(
    answer: str,
    *,
    sources: list[str],
    top_scores: list[float],
    t0: float,
    reason: str,
) -> RAGResult:
    latency_ms = int((time.monotonic() - t0) * 1000)
    rag_unanswered_total.labels(reason=reason).inc()
    rag_pipeline_duration_seconds.labels(stage="handoff").observe(latency_ms / 1000.0)
    logger.info(
        "rag.handoff",
        reason=reason,
        top1=top_scores[0] if top_scores else None,
        sources=sources,
        latency_ms=latency_ms,
    )
    return RAGResult(
        answer=answer,
        sources=sources,
        top_scores=top_scores,
        answered=False,
        latency_ms=latency_ms,
    )


def _score(item: RankedChunk) -> float:
    if item.dense_score is not None:
        return item.dense_score
    return item.rrf_score


def _build_context(ranked: list[RankedChunk]) -> str:
    parts: list[str] = []
    for idx, item in enumerate(ranked, start=1):
        parts.append(f"[^{idx}] {item.chunk.filename}\n{item.chunk.text}")
    return "\n\n".join(parts)


def _dedup_sources(ranked: list[RankedChunk]) -> list[str]:
    seen: set[str] = set()
    sources: list[str] = []
    for item in ranked:
        name = item.chunk.filename
        if name in seen:
            continue
        seen.add(name)
        sources.append(name)
    return sources


__all__ = [
    "EMPTY_RESPONSE",
    "INVALID_QUERY_RESPONSE",
    "LOW_RELEVANCE_RESPONSE",
    "RAGResult",
    "ask",
]
