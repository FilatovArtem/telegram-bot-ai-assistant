"""Bind request_id, user_id, update_id to structlog contextvars for downstream logs."""

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

        inner: Message | CallbackQuery | None = None
        if isinstance(event, Update):
            ctx["update_id"] = event.update_id
            inner = event.message or event.callback_query
        elif isinstance(event, Message | CallbackQuery):
            inner = event

        if inner is not None:
            if inner.from_user is not None:
                ctx["user_id"] = inner.from_user.id
            if isinstance(inner, Message):
                ctx["handler"] = "message"
            elif isinstance(inner, CallbackQuery):
                ctx["handler"] = "callback"

        structlog.contextvars.bind_contextvars(**ctx)
        try:
            return await handler(event, data)
        finally:
            structlog.contextvars.clear_contextvars()
