"""Prometheus collectors. Names and labels are public API — adding new is safe,
renaming breaks Grafana dashboards.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram

_LATENCY_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
_SCORE_BUCKETS: tuple[float, ...] = (0.0, 0.3, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)
_VECTOR_OP_BUCKETS: tuple[float, ...] = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)

bot_messages_total = Counter(
    "bot_messages_total",
    "Telegram events processed by type",
    labelnames=("type",),
)
bot_request_duration_seconds = Histogram(
    "bot_request_duration_seconds",
    "End-to-end Telegram handler latency",
    labelnames=("handler",),
    buckets=_LATENCY_BUCKETS,
)
bot_errors_total = Counter(
    "bot_errors_total",
    "Unhandled exceptions inside handlers",
    labelnames=("type",),
)
bot_throttled_total = Counter(
    "bot_throttled_total",
    "Updates dropped by throttling middleware",
)

rag_pipeline_duration_seconds = Histogram(
    "rag_pipeline_duration_seconds",
    "Per-stage RAG latency",
    labelnames=("stage",),
    buckets=_LATENCY_BUCKETS,
)
rag_unanswered_total = Counter(
    "rag_unanswered_total",
    "Questions without LLM answer",
    labelnames=("reason",),
)
rag_relevance_score = Histogram(
    "rag_relevance_score",
    "Top-1 cosine score distribution",
    buckets=_SCORE_BUCKETS,
)

openai_tokens_total = Counter(
    "openai_tokens_total",
    "Tokens consumed by OpenAI calls",
    labelnames=("model", "type"),
)
openai_errors_total = Counter(
    "openai_errors_total",
    "OpenAI client errors",
    labelnames=("type",),
)

feedback_total = Counter(
    "feedback_total",
    "User feedback votes",
    labelnames=("vote",),
)

vector_store_size = Gauge(
    "vector_store_size",
    "FAISS index ntotal",
)
vector_store_op_duration_seconds = Histogram(
    "vector_store_op_duration_seconds",
    "Vector store operation latency",
    labelnames=("op",),
    buckets=_VECTOR_OP_BUCKETS,
)

__all__ = [
    "CONTENT_TYPE_LATEST",
    "bot_errors_total",
    "bot_messages_total",
    "bot_request_duration_seconds",
    "bot_throttled_total",
    "feedback_total",
    "openai_errors_total",
    "openai_tokens_total",
    "rag_pipeline_duration_seconds",
    "rag_relevance_score",
    "rag_unanswered_total",
    "vector_store_op_duration_seconds",
    "vector_store_size",
]
