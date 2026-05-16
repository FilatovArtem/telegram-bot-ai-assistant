"""structlog configuration — JSON output with PII/secret redaction.

Call configure_logging() exactly once at startup BEFORE any get_logger() use.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog
from structlog.stdlib import LoggerFactory
from structlog.types import EventDict

from bot.config import settings

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{6,}"), r"\1***"),
    (
        re.compile(r"(Authorization['\"]?\s*[:=]\s*['\"]?)[^\s'\"]+", re.IGNORECASE),
        r"\1***",
    ),
    (re.compile(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b"), "***bot-token***"),
)

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "authorization",
        "api_key",
        "bot_token",
        "openai_api_key",
        "api_admin_token",
        "sentry_dsn",
        "password",
        "token",
        "secret",
    }
)


def _redact(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
            continue
        value = event_dict[key]
        if isinstance(value, str):
            for pattern, replacement in _SECRET_PATTERNS:
                value = pattern.sub(replacement, value)
            event_dict[key] = value
    return event_dict


def configure_logging(level: str | None = None) -> None:
    log_level_name = (level or settings.log_level).upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.dict_tracebacks,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    for noisy in ("aiosqlite", "sqlalchemy.engine", "httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
