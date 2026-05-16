"""In-memory throttling: at most one update per user per configured interval."""

from __future__ import annotations

import contextlib
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update

from bot.observability.metrics import bot_throttled_total


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, per_sec: float = 1.0) -> None:
        self.interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._last: dict[int, float] = defaultdict(lambda: 0.0)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if self.interval <= 0:
            return await handler(event, data)

        inner: Message | CallbackQuery | None = _inner(event)
        user = inner.from_user if inner is not None else None
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last[user.id]
        if now - last < self.interval:
            bot_throttled_total.inc()
            if isinstance(inner, Message):
                with contextlib.suppress(Exception):
                    await inner.answer("⚠️ Слишком быстро. Подождите секунду.")
            elif isinstance(inner, CallbackQuery):
                with contextlib.suppress(Exception):
                    await inner.answer("Слишком часто", show_alert=False)
            return None

        self._last[user.id] = now
        return await handler(event, data)


def _inner(event: TelegramObject) -> Message | CallbackQuery | None:
    if isinstance(event, Update):
        return event.message or event.callback_query
    if isinstance(event, Message | CallbackQuery):
        return event
    return None
