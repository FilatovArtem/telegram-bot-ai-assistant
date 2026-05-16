---
status: draft
phase: architecture
track: A2 — Infrastructure
created: 2026-05-16
author: architect-A2
constitution: v1.0.0
scope: config, db models/repos, middlewares, structlog, prometheus, entry-point, docker, CI, Sentry
---

# A2 — Infrastructure Design

Зона: configuration, persistence, aiogram middlewares, observability (structlog + Prometheus + Sentry), lifecycle, containerisation, CI. НЕ покрывает: RAG pipeline (A3), handlers/FSM/keyboards (A4), API routes/auth (A5).

## 1. `bot/config.py` — pydantic-settings v2

**Решение**: pydantic-settings v2, `SecretStr` для 4 секретов, CSV-parser для `admin_ids`, range-check `relevance_threshold`. `extra="ignore"` чтобы `.env` мог содержать proxy-переменные. Один источник правды.

**Constitution**: III.1 (SecretStr), VI.5 (ENV only).

```python
"""Application settings. All secrets wrapped in SecretStr — redact in repr/logs."""
from __future__ import annotations

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8",
        extra="ignore", case_sensitive=False,
    )

    # Required secrets
    bot_token: SecretStr
    openai_api_key: SecretStr
    api_admin_token: SecretStr

    # Admin / ops
    admin_ids: list[int] = Field(default_factory=list)
    admin_chat_id: int = 0

    # Storage / models
    database_url: str = "sqlite+aiosqlite:///data/bot.db"
    openai_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"

    # RAG tuning
    relevance_threshold: float = 0.75
    rerank_enabled: bool = False
    bm25_enabled: bool = True
    top_k_dense: int = 50
    top_k_bm25: int = 50
    top_k_final: int = 5
    max_chunk_tokens: int = 500
    chunk_overlap: int = 100
    max_answer_tokens: int = 500

    # API / runtime / observability
    api_port: int = 8000
    log_level: str = "INFO"
    metrics_internal_only: bool = False
    sentry_dsn: SecretStr | None = None
    environment: str = "production"
    throttle_window_ms: int = 1000
    upload_max_bytes: int = 10 * 1024 * 1024

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [int(x) for x in v]
        raise ValueError(f"admin_ids: unsupported type {type(v)}")

    @field_validator("relevance_threshold")
    @classmethod
    def _check_threshold(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("relevance_threshold must be in [0, 1]")
        return v

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return v.upper()


settings = Settings()  # type: ignore[call-arg]
```

`.env.example` обновляется: добавить `API_ADMIN_TOKEN`, `SENTRY_DSN=`, `LOG_LEVEL=INFO`, `RERANK_ENABLED=false`, `BM25_ENABLED=true`, `TOP_K_*`, `MAX_*`, `THROTTLE_WINDOW_MS`, `UPLOAD_MAX_BYTES`, `ENVIRONMENT=production`, `METRICS_INTERNAL_ONLY=false`.

## 2. `bot/db/models.py` — SQLAlchemy 2 Mapped

**Решение**: расширяем существующие. Новые — `Feedback`, `AuditLog`. В `Conversation` добавляем `sources/top_scores` (JSON), `latency_ms`. В `Document` — `sha256` (unique для dedup), `uploaded_by`. JSON cross-dialect (SQLite TEXT → Postgres JSONB).

**Constitution**: I.3 (top-scores), III.5 (AuditLog).

```python
from __future__ import annotations
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Enum, Float,
    ForeignKey, Index, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase): pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    top_scores: Mapped[list[float]] = mapped_column(JSON, default=list)
    answered: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True,
    )
    user: Mapped["User"] = relationship(back_populates="conversations")
    feedback: Mapped["Feedback | None"] = relationship(
        back_populates="conversation", uselist=False,
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(256))
    chunk_count: Mapped[int] = mapped_column(default=0)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    uploaded_by: Mapped[int] = mapped_column(BigInteger)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True,
    )
    vote: Mapped[str] = mapped_column(Enum("up", "down", name="vote_t"))
    comment: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    conversation: Mapped["Conversation"] = relationship(back_populates="feedback")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(32))  # upload | delete | reindex
    target: Mapped[str] = mapped_column(String(256))
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True,
    )


Index("ix_conv_user_created", Conversation.user_id, Conversation.created_at.desc())
Index("ix_conv_ans_created", Conversation.answered, Conversation.created_at.desc())
```

## 3. `bot/db/repositories.py` — расширение

**Решение**: чистые функции `(session, *kwargs)`. Один commit на функцию. Конкуренция — на стороне сервиса (`asyncio.Lock`). `get_top_unanswered_questions` — простой GROUP BY (semantic clustering — v1.1).

**Constitution**: VI.3 (без абстракций на будущее), VII.3 (coverage).

Signatures (тела стандартные; критичный — `get_top_unanswered_questions` и `feedback_ratio`):

```python
# Users
async def upsert_user(session, user_id: int, full_name: str, username: str | None = None) -> User
async def count_users(session) -> int

# Conversations
async def save_conversation(
    session, *, user_id: int, question: str, answer: str,
    relevance_score: float, sources: list[str], top_scores: list[float],
    answered: bool, latency_ms: int,
) -> Conversation
async def count_conversations(session) -> int
async def count_conversations_since(session, since: datetime) -> int
async def count_unanswered(session) -> int
async def count_unanswered_since(session, since: datetime) -> int

async def get_top_unanswered_questions(session, limit: int = 5) -> list[tuple[str, int]]:
    stmt = (select(Conversation.question, func.count(Conversation.id).label("cnt"))
            .where(Conversation.answered.is_(False))
            .group_by(Conversation.question)
            .order_by(desc("cnt")).limit(limit))
    return [(r.question, r.cnt) for r in (await session.execute(stmt)).all()]

# Documents
async def save_document(session, *, filename: str, chunk_count: int, sha256: str, uploaded_by: int) -> Document
async def document_exists_by_sha256(session, sha256: str) -> bool
async def get_documents(session) -> list[Document]
async def get_document(session, doc_id: int) -> Document | None
async def delete_document(session, doc_id: int) -> bool  # False if not found

# Feedback
async def save_feedback(session, *, conversation_id: int, vote: str, comment: str | None = None) -> Feedback

async def feedback_ratio(session) -> tuple[int, int]:
    """Returns (up_count, down_count); caller computes ratio (avoid div/0)."""
    stmt = select(Feedback.vote, func.count(Feedback.id)).group_by(Feedback.vote)
    counts = dict((await session.execute(stmt)).all())
    return int(counts.get("up", 0)), int(counts.get("down", 0))

# Audit
async def audit_log_insert(session, *, admin_id: int, action: str, target: str,
                           meta: dict[str, Any] | None = None) -> AuditLog
```

## 4. `bot/db/engine.py` — миграции

**Решение**: оставляем `Base.metadata.create_all()` в startup. **Alembic не нужен в MVP**:
- SQLite — однофайловая, регенерация дёшева.
- Схема стабильна (5 таблиц зафиксированы spec §4.4).
- Postgres-поддержка опц. — клиенты стартуют с чистой БД.
- Alembic = +1 dep + +2 файла конфига + CLI-шаги — ломает goal "deploy за <5 мин".
- v1.1: добавить через design ADR при необходимости backwards-compatible migrations.

Добавляется `dispose_engine()` для shutdown:

```python
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False,
)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session

async def dispose_engine() -> None:
    await engine.dispose()
```

## 5. `bot/middlewares/`

### 5.1 `db.py` — без изменений (только type-vars: `async_sessionmaker[AsyncSession]`)

### 5.2 `logging.py` (новый) — structlog binding

```python
"""Bind request_id, user_id, update_id to contextvars for downstream logs."""
from __future__ import annotations
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        structlog.contextvars.clear_contextvars()
        ctx: dict[str, Any] = {"request_id": uuid.uuid4().hex[:12]}
        if isinstance(event, Update):
            ctx["update_id"] = event.update_id
            inner = event.message or event.callback_query
            if inner and inner.from_user:
                ctx["user_id"] = inner.from_user.id
            if isinstance(inner, Message): ctx["handler"] = "message"
            elif isinstance(inner, CallbackQuery): ctx["handler"] = "callback"
        structlog.contextvars.bind_contextvars(**ctx)
        try:
            return await handler(event, data)
        finally:
            structlog.contextvars.clear_contextvars()
```

### 5.3 `throttling.py` (новый) — in-memory per-user rate limit

```python
"""Drop messages faster than throttle_window_ms per user. In-memory dict —
single-instance MVP only; v1.1 → Redis-backed."""
from __future__ import annotations
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config import settings
from bot.observability.metrics import bot_throttled_total

log = structlog.get_logger(__name__)


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, window_ms: int | None = None) -> None:
        self._window = (window_ms or settings.throttle_window_ms) / 1000.0
        self._last_seen: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        uid = self._uid(event)
        if uid is None:
            return await handler(event, data)
        now = time.monotonic()
        if now - self._last_seen.get(uid, 0.0) < self._window:
            bot_throttled_total.inc()
            log.info("throttled", user_id=uid)
            if isinstance(event, Message):
                await event.answer("Слишком быстро. Подождите секунду.")
            elif isinstance(event, CallbackQuery):
                await event.answer("Слишком быстро.", show_alert=False)
            return None
        self._last_seen[uid] = now
        return await handler(event, data)

    @staticmethod
    def _uid(event):
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            return event.from_user.id
        return None
```

TTL cleanup пока не нужен (миллион уникальных user_id нереалистичен для MVP).

### 5.4 `error.py` (новый) — try/except + Sentry capture

```python
from __future__ import annotations
import structlog
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.observability.metrics import bot_errors_total
log = structlog.get_logger(__name__)


class ErrorMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except Exception as exc:
            bot_errors_total.labels(type=type(exc).__name__).inc()
            log.exception("handler_error", exc_class=type(exc).__name__)
            try:
                import sentry_sdk
                sentry_sdk.capture_exception(exc)
            except ImportError:
                pass
            msg = "Произошла внутренняя ошибка. Мы уже разбираемся."
            if isinstance(event, Message):
                await event.answer(msg)
            elif isinstance(event, CallbackQuery):
                await event.answer(msg, show_alert=True)
            return None
```

### 5.5 `metrics.py` (новый) — Prometheus per-update

```python
from __future__ import annotations
import time
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.observability.metrics import bot_messages_total, bot_request_duration_seconds


class MetricsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        kind = self._classify(event)
        bot_messages_total.labels(type=kind).inc()
        started = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            bot_request_duration_seconds.labels(handler=kind).observe(time.monotonic() - started)

    @staticmethod
    def _classify(event):
        if isinstance(event, Message):
            if (event.text or "").startswith("/"): return "command"
            if event.document is not None: return "document"
            return "question"
        if isinstance(event, CallbackQuery): return "callback"
        return "other"
```

**Middleware chain order** (см. §8 registration):
1. LoggingMiddleware (outer) — bind context первым.
2. MetricsMiddleware (outer) — measure full path.
3. ErrorMiddleware (outer) — catch до DB-commit-а.
4. ThrottlingMiddleware (inner) — drop до session.
5. DbSessionMiddleware (inner) — session per update.

Outer = `dp.update.outer_middleware()`; inner = `dp.update.middleware()`.

## 6. structlog setup — `bot/observability/logging_config.py`

**Решение**: вызывается из `__main__.py` ДО любых logger calls. Processors: contextvars merge → level → ISO TimeStamper → redact secrets → stack info → tracebacks → JSON. Stdlib loggers (uvicorn, sqlalchemy, aiogram) идут через `logging.basicConfig(force=True)` → структурируются root handler-ом. `wrapper_class = make_filtering_bound_logger(level)` для фильтрации.

**Constitution**: V.1 (`basicConfig` запрещён напрямую — мы используем его исключительно как bridge); V.5 (redact PII/secrets).

```python
"""structlog configuration — JSON output with PII/secret redaction.
Call configure_logging() exactly once at startup BEFORE any get_logger()."""
from __future__ import annotations
import logging, re, sys
from typing import Any

import structlog
from structlog.stdlib import LoggerFactory
from structlog.types import EventDict

_SECRET_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I), r"\1***"),
    (re.compile(r"(sk-[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]{6,}"), r"\1***"),
    (re.compile(r"(Authorization['\"]?\s*[:=]\s*['\"]?)[^\s'\"]+", re.I), r"\1***"),
    (re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"), "***bot-token***"),
]
_SENSITIVE_KEYS = {
    "authorization", "api_key", "bot_token", "openai_api_key",
    "api_admin_token", "sentry_dsn", "password", "token", "secret",
}


def _redact(_lg: Any, _name: str, event_dict: EventDict) -> EventDict:
    for k in list(event_dict.keys()):
        if k.lower() in _SENSITIVE_KEYS:
            event_dict[k] = "***"; continue
        v = event_dict[k]
        if isinstance(v, str):
            for pat, rep in _SECRET_PATTERNS:
                v = pat.sub(rep, v)
            event_dict[k] = v
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=lvl, force=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    for noisy in ("aiosqlite", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
```

## 7. Prometheus metrics — `bot/observability/metrics.py`

**Решение**: все коллекторы — на уровне модуля. `/metrics` — `make_asgi_app()` mount-ом в FastAPI. Buckets под p95 ≤ 5s. Names/labels — public API (Grafana dashboards), переименование = breaking.

**Constitution**: IV.5 (tokens metric), V.3 (Prometheus required).

```python
"""Prometheus collectors. Names/labels are public API — do not rename, add new."""
from __future__ import annotations
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram

_LAT = (0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
_SCORE = (0.0, 0.3, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)
_VEC = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0)

# Bot
bot_messages_total = Counter("bot_messages_total",
    "Telegram events processed", ["type"])  # question|command|callback|document|other
bot_request_duration_seconds = Histogram("bot_request_duration_seconds",
    "End-to-end handler latency", ["handler"], buckets=_LAT)
bot_errors_total = Counter("bot_errors_total",
    "Unhandled exceptions inside handlers", ["type"])
bot_throttled_total = Counter("bot_throttled_total", "Updates dropped by throttling")

# RAG
rag_pipeline_duration_seconds = Histogram("rag_pipeline_duration_seconds",
    "Per-stage RAG latency", ["stage"], buckets=_LAT)  # embed|search|rerank|llm|total
rag_unanswered_total = Counter("rag_unanswered_total",
    "Questions without LLM answer", ["reason"])  # low_relevance|empty_index|error|operator_requested
rag_relevance_score = Histogram("rag_relevance_score",
    "Top-1 cosine score distribution", buckets=_SCORE)

# OpenAI
openai_tokens_total = Counter("openai_tokens_total",
    "Tokens consumed", ["model", "type"])  # type: input|output|embedding
openai_errors_total = Counter("openai_errors_total",
    "OpenAI client errors", ["type"])  # rate_limit|timeout|connection|other

# Feedback / vector
feedback_total = Counter("feedback_total", "User votes", ["vote"])  # up|down
vector_store_size = Gauge("vector_store_size", "FAISS index size")
vector_store_op_duration_seconds = Histogram("vector_store_op_duration_seconds",
    "FAISS op latency", ["op"], buckets=_VEC)  # add|search|save|load

__all__ = [
    "CONTENT_TYPE_LATEST",
    "bot_messages_total", "bot_request_duration_seconds",
    "bot_errors_total", "bot_throttled_total",
    "rag_pipeline_duration_seconds", "rag_unanswered_total", "rag_relevance_score",
    "openai_tokens_total", "openai_errors_total",
    "feedback_total", "vector_store_size", "vector_store_op_duration_seconds",
]
```

## 8. `bot/__main__.py` — lifecycle

**Решение**: единый `asyncio.Event` для остановки. `dp.start_polling(handle_signals=False)` — мы владеем signal flow. Обе задачи (polling, server.serve) выходят через cancellation. `shutdown` — `store.persist()`, `bot.session.close()`, `dispose_engine()`. Total ≤ 10s (spec §5.2).

`/metrics` — mount sub-app: `app.mount("/metrics", make_asgi_app())` (рекомендованный async-safe способ для `prometheus-client`).

**Constitution**: II.5 (graceful), II.6 (mount /health,/ready через A5), V.1 (configure_logging первым).

```python
"""Entry point — aiogram polling + uvicorn together, single signal flow."""
from __future__ import annotations
import asyncio, signal
from contextlib import suppress

import structlog
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from api.routes import router as api_router
from bot.config import settings
from bot.db.engine import async_session, dispose_engine, engine
from bot.db.models import Base
from bot.handlers import setup_routers
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.error import ErrorMiddleware
from bot.middlewares.logging import LoggingMiddleware
from bot.middlewares.metrics import MetricsMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.observability.logging_config import configure_logging
from bot.observability.metrics import vector_store_size
from bot.services.embeddings import get_store

configure_logging(level=settings.log_level)
log = structlog.get_logger("bot.main")


def _init_sentry() -> None:
    if settings.sentry_dsn is None:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
    except ImportError:
        log.warning("sentry_sdk_missing"); return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.environment,
        integrations=[AsyncioIntegration(), FastApiIntegration()],
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    log.info("sentry_initialized", env=settings.environment)


def _build_app() -> FastAPI:
    app = FastAPI(title="AI Assistant Bot API", version="0.1.0")
    app.include_router(api_router)
    app.mount("/metrics", make_asgi_app())
    return app


async def _startup(bot: Bot) -> None:
    log.info("startup_begin")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    store = get_store()
    await store.load()
    vector_store_size.set(store.size)
    log.info("startup_done", vector_size=store.size)


async def _shutdown(bot: Bot) -> None:
    log.info("shutdown_begin")
    store = get_store()
    with suppress(Exception): await store.persist()
    with suppress(Exception): await bot.session.close()
    with suppress(Exception): await dispose_engine()
    log.info("shutdown_done")


def _register_middlewares(dp: Dispatcher) -> None:
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(MetricsMiddleware())
    dp.update.outer_middleware(ErrorMiddleware())
    dp.update.middleware(ThrottlingMiddleware())
    dp.update.middleware(DbSessionMiddleware(session_pool=async_session))


def _install_signals(loop: asyncio.AbstractEventLoop, stop: asyncio.Event) -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)


async def main() -> None:
    _init_sentry()
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dp = Dispatcher()
    _register_middlewares(dp)
    dp.include_router(setup_routers())
    dp.startup.register(lambda: _startup(bot))
    dp.shutdown.register(lambda: _shutdown(bot))

    app = _build_app()
    server = uvicorn.Server(uvicorn.Config(
        app, host="0.0.0.0", port=settings.api_port,
        log_config=None, access_log=False, lifespan="on",
    ))

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    _install_signals(loop, stop)

    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False), name="polling",
    )
    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    stop_task = asyncio.create_task(stop.wait(), name="stop")
    log.info("started", api_port=settings.api_port)

    _, pending = await asyncio.wait(
        {polling_task, server_task, stop_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    log.info("stopping")
    server.should_exit = True
    await dp.stop_polling()
    for t in pending: t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    log.info("stopped")


if __name__ == "__main__":
    asyncio.run(main())
```

Контракт A3 (vector store): `await store.load()`, `await store.persist()`, `store.size: int`. Уточняется при синтезе design.md.

## 9. Dockerfile + docker-compose.yml + `.dockerignore`

### 9.1 Dockerfile (multi-stage, non-root, healthcheck)

**Решение**: builder ставит deps в venv → runtime копирует venv + код. Финал ≤ 500 MB (python:3.12-slim ~120MB + faiss-cpu ~80MB + остальные ~150MB). Non-root `appuser` (uid 1001). HEALTHCHECK через Python `urllib.request`, без `curl`.

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
RUN uv venv /opt/venv \
 && uv pip install --python /opt/venv/bin/python --no-cache ".[observability,sentry,rag]"

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/opt/venv/bin:$PATH"
RUN groupadd --system --gid 1001 appgroup \
 && useradd  --system --uid 1001 --gid appgroup --create-home appuser
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appgroup bot ./bot
COPY --chown=appuser:appgroup api ./api
COPY --chown=appuser:appgroup data/knowledge ./data/knowledge
COPY --chown=appuser:appgroup pyproject.toml ./
RUN mkdir -p /app/data/faiss_index && chown -R appuser:appgroup /app/data
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; \
      sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status==200 else sys.exit(1)" \
    || exit 1
CMD ["python", "-m", "bot"]
```

### 9.2 `.dockerignore` (новый)

```
.git
.gitignore
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/
.venv/
venv/
.env
.env.*
!.env.example
data/faiss_index/
data/bot.db
data/*.db
data/*.db-journal
tests/
.coverage
htmlcov/
.claude/
.specify/
specs/_codebase/
docs/screenshots/
.idea/
.vscode/
*.swp
dist/
build/
```

### 9.3 `docker-compose.yml`

```yaml
services:
  bot:
    build:
      context: .
      dockerfile: Dockerfile
    image: telegram-bot-ai-assistant:latest
    container_name: ai-support-bot
    env_file: .env
    ports: ["${API_PORT:-8000}:8000"]
    volumes:
      - bot-data:/app/data
    restart: unless-stopped
    stop_grace_period: 15s
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status==200 else sys.exit(1)"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    deploy:
      resources:
        limits:    { cpus: "1.0", memory: 1G }
        reservations: { memory: 256M }
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "3" }

volumes:
  bot-data:
```

### 9.4 Опциональный observability profile (`docker-compose.observability.yml`)

Запускается: `docker compose -f docker-compose.yml -f docker-compose.observability.yml up`.

```yaml
services:
  prometheus:
    image: prom/prometheus:latest
    ports: ["9090:9090"]
    volumes:
      - ./ops/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prom-data:/prometheus
    restart: unless-stopped
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - grafana-data:/var/lib/grafana
    depends_on: [prometheus]
    restart: unless-stopped
volumes:
  prom-data:
  grafana-data:
```

`ops/prometheus.yml`:
```yaml
global: { scrape_interval: 15s }
scrape_configs:
  - job_name: ai-support-bot
    static_configs:
      - targets: ["bot:8000"]
    metrics_path: /metrics
```

## 10. CI — `.github/workflows/ci.yml`

**Решение**: 4 параллельных job-а: lint, type-check, test, docker-build. `uv` для скорости (~5× быстрее pip). `mypy --strict bot api`. `pytest --cov`. Image-size budget enforce ≤ 500 MB.

```yaml
name: CI
on:
  push: { branches: [main] }
  pull_request: { branches: [main] }
permissions: { contents: read }
env:
  PYTHON_VERSION: "3.12"
  UV_VERSION: "0.5.0"

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}" }
      - uses: astral-sh/setup-uv@v3
        with: { version: "${{ env.UV_VERSION }}" }
      - run: |
          uv venv .venv
          uv pip install --python .venv/bin/python ".[dev,observability,rag]"
      - run: .venv/bin/ruff check .
      - run: .venv/bin/ruff format --check .

  type-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}" }
      - uses: astral-sh/setup-uv@v3
        with: { version: "${{ env.UV_VERSION }}" }
      - run: |
          uv venv .venv
          uv pip install --python .venv/bin/python ".[dev,observability,rag]"
      - run: .venv/bin/mypy bot api

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ env.PYTHON_VERSION }}" }
      - uses: astral-sh/setup-uv@v3
        with: { version: "${{ env.UV_VERSION }}" }
      - run: |
          uv venv .venv
          uv pip install --python .venv/bin/python ".[dev,observability,rag]"
      - env:
          BOT_TOKEN: ci-stub
          OPENAI_API_KEY: ci-stub
          API_ADMIN_TOKEN: ci-stub
          DATABASE_URL: "sqlite+aiosqlite:///:memory:"
        run: .venv/bin/pytest -v --cov=bot --cov=api --cov-report=term-missing

  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - uses: docker/build-push-action@v6
        with:
          context: .
          push: false
          load: true
          tags: ai-support-bot:ci
      - name: Image size budget
        run: |
          size_mb=$(docker image inspect ai-support-bot:ci --format='{{.Size}}' | awk '{print int($1/1024/1024)}')
          echo "Image size: ${size_mb} MB"
          [ "$size_mb" -le 500 ] || { echo "::error::Image exceeds 500 MB"; exit 1; }
```

`pyproject.toml` дополнения (coverage):

```toml
[tool.coverage.run]
source = ["bot", "api"]
omit = ["bot/__main__.py", "tests/*"]

[tool.coverage.report]
fail_under = 70
```

## 11. Sentry (опционально)

**Решение**: уже встроено в `_init_sentry()` (§8). DSN из `SecretStr` через `.get_secret_value()`. Integrations: `AsyncioIntegration`, `FastApiIntegration`. **Logging-integration не подключаем** — structlog уже сериализует, integration ловит INFO как breadcrumbs и засоряет issues. Capture — явный в ErrorMiddleware. `traces_sample_rate=0.0` (free-tier 5k events/month). `send_default_pii=False`.

**Constitution**: V.4 (опционален), III.7 (no full dumps).

`pyproject.toml` дополнения:

```toml
[project.dependencies]
# default deps уже есть, добавить:
# structlog>=24.4, prometheus-client>=0.21, tenacity>=9.0, rank-bm25>=0.2.2

[project.optional-dependencies]
sentry = ["sentry-sdk[fastapi]>=2.18"]
observability = ["prometheus-client>=0.21", "structlog>=24.4"]
postgres = ["asyncpg>=0.30"]
rag = ["rank-bm25>=0.2.2", "tenacity>=9.0", "flashrank>=0.2"]
dev = [
    "ruff>=0.8", "mypy>=1.13",
    "pytest>=8.0", "pytest-asyncio>=0.24", "pytest-cov>=5.0", "pytest-mock>=3.14",
    "respx>=0.21", "httpx>=0.27",
]
```

Docker инсталит `.[observability,sentry,rag]`. Если `SENTRY_DSN` пуст — init no-op, sentry-sdk ~3 MB лишних — приемлемо.

## 12. Test Specification (для test-writer)

Тесты — по этой спеке, **без чтения реализации** (Constitution VII.1).

**12.1 Config** (`tests/test_config.py`):
- ADMIN_IDS CSV → list[int]; пустая строка → []; уже list → ok.
- relevance_threshold: -0.1 / 1.5 → ValidationError.
- log_level: "info" → "INFO"; "nope" → ValidationError.
- `repr(settings.bot_token)` не содержит raw value (SecretStr).
- Settings() без BOT_TOKEN → ValidationError.

**12.2 Repositories** (`tests/test_repositories.py`):
- `upsert_user` creates / updates.
- `save_conversation`: sources, top_scores serialise/roundtrip JSON.
- `count_conversations_since`: 3 conv, 2 в последний час → since(now-1h) == 2.
- `count_unanswered` исключает answered=True.
- `get_top_unanswered_questions`: 3 одинаковых → top1.count == 3, sorted DESC.
- `document_exists_by_sha256`: False до insert, True после.
- `save_document` сохраняет uploaded_by.
- `save_feedback` повторно для того же conv → IntegrityError (UNIQUE).
- `feedback_ratio`: 3 up + 1 down → (3,1); empty → (0,0).
- `audit_log_insert` с meta — roundtrip.
- `delete_document(missing)` → False, без exception.

**12.3 Middlewares**:
- *logging*: после middleware contextvars содержат request_id, user_id; вне scope — пусты.
- *throttling*: первое сообщение проходит; второе за 100мс — dropped, `bot_throttled_total` += 1; через 1.1с — оба пройдут.
- *error*: handler raises ValueError → middleware returns None, `bot_errors_total{type=ValueError}` += 1; пользователь получает "Произошла внутренняя ошибка...".
- *metrics*: `bot_messages_total{type=...}` инкрементирован; `bot_request_duration_seconds` histogram имеет 1 sample.

**12.4 Observability**:
- *logging_config*: redact заменяет `Bearer abc123` → `Bearer ***`; `sk-proj-abcdefghij` → `sk-pr***`; `api_key='secret'` → `'***'`. stdout — валидный JSON с полем `event`.
- *metrics*: GET /metrics → 200, body содержит `bot_messages_total` и `# HELP`.

**12.5 Smoke E2E** (`tests/test_smoke.py`):
- GET /health → 200 {status:'ok'}.
- GET /ready → 200; статусы db + index.
- GET /metrics → 200 (public по умолчанию).
- После startup — 5 таблиц в sqlite_master.
- Shutdown вызывает `store.persist()` и `dispose_engine()`.

**12.6 Coverage** (Constitution VII.3): `bot/config.py` ≥ 90%; `bot/db/repositories.py` ≥ 85%; `bot/middlewares/` ≥ 80%; `bot/observability/` ≥ 80%; `bot/__main__.py` excluded.

## 13. Constitution mapping

| Принцип | Где соблюдён |
|---|---|
| **I. RAG Correctness** | top_scores логируется в `Conversation` (для калибровки). Основное — в A3. |
| **II. Production Safety** | `dispose_engine()` shutdown, asyncio.Event signal flow, healthcheck в Dockerfile + compose. Atomic FAISS — контракт A3. |
| **III. Security First** | SecretStr × 4; redact processor; AuditLog; `.dockerignore`; non-root user; throttling. |
| **IV. Cost-Conscious** | `openai_tokens_total` metric; image budget 500 MB enforced в CI. |
| **V. Observability by Default** | structlog JSON + contextvars; metrics module; Sentry опц.; PII redact. |
| **VI. Simplicity & Surgical** | Нет Alembic; нет YAML config; `.dockerignore` явный. |
| **VII. Test by Spec** | §12 — спецификация написана без референсов в код. |

## 14. Open questions для синтеза

1. **Vector store interface** (`store.load()`, `store.persist()`, `store.size`) — контракт A3, но `__main__.py` зависит. Минимум: `load() async`, `persist() async`, `size: int` (property).
2. **`/health`, `/ready` body** — A5. Здесь только mount. Pings: `SELECT 1` + cached `openai.models.list()` (30s TTL).
3. **`/metrics` IP-allowlist** при `METRICS_INTERNAL_ONLY=true` — A5 владеет FastAPI middleware.
4. **AdminFilter** — A4. Source-of-truth `admin_ids` остаётся в config.
5. **Sentry в default Docker** — да, через `.[observability,sentry,rag]`. Если DSN пуст — no-op, ~3 MB лишних приемлемо.

## 15. Файловые изменения

| Файл | Действие | §|
|---|---|---|
| `bot/config.py` | rewrite | 1 |
| `bot/db/models.py` | rewrite | 2 |
| `bot/db/repositories.py` | rewrite | 3 |
| `bot/db/engine.py` | +`dispose_engine` | 4 |
| `bot/middlewares/db.py` | type-vars | 5.1 |
| `bot/middlewares/{logging,throttling,error,metrics}.py` | new × 4 | 5.2–5.5 |
| `bot/observability/__init__.py` | new (empty) | 6 |
| `bot/observability/logging_config.py` | new | 6 |
| `bot/observability/metrics.py` | new | 7 |
| `bot/__main__.py` | rewrite | 8 |
| `Dockerfile` | rewrite | 9.1 |
| `.dockerignore` | new | 9.2 |
| `docker-compose.yml` | rewrite | 9.3 |
| `docker-compose.observability.yml` + `ops/prometheus.yml` | new (opt) | 9.4 |
| `.github/workflows/ci.yml` | new | 10 |
| `.env.example` | rewrite | 1 |
| `pyproject.toml` | update deps + coverage | 11 |
| `tests/test_config.py` | new | 12.1 |
| `tests/test_repositories.py` | new | 12.2 |
| `tests/test_middlewares_{logging,throttling,error,metrics}.py` | new × 4 | 12.3 |
| `tests/test_logging_config.py` | new | 12.4 |
| `tests/test_metrics.py` | new | 12.4 |
| `tests/test_smoke.py` | new | 12.5 |
