"""Prometheus per-update metrics: message count + handler duration."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from bot.observability.metrics import bot_messages_total, bot_request_duration_seconds


def _classify(event: TelegramObject) -> str:
    inner: Message | CallbackQuery | None
    if isinstance(event, Update):
        inner = event.message or event.callback_query
    elif isinstance(event, Message | CallbackQuery):
        inner = event
    else:
        return "other"

    if isinstance(inner, Message):
        if (inner.text or "").startswith("/"):
            return "command"
        if inner.document is not None:
            return "document"
        return "message"
    if isinstance(inner, CallbackQuery):
        return "callback"
    return "other"


class MetricsMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        kind = _classify(event)
        bot_messages_total.labels(type=kind).inc()
        started = time.monotonic()
        try:
            return await handler(event, data)
        finally:
            bot_request_duration_seconds.labels(handler=kind).observe(time.monotonic() - started)
