"""Global error handler. Logs the failure, captures Sentry, replies gracefully."""

from __future__ import annotations

import contextlib

import structlog
from aiogram import Router
from aiogram.types import CallbackQuery, ErrorEvent, Message

from bot.observability.metrics import bot_errors_total

router = Router()
logger = structlog.get_logger(__name__)


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    exc = event.exception
    update = event.update
    bot_errors_total.labels(type=type(exc).__name__).inc()
    logger.exception(
        "handler.error",
        exc_type=type(exc).__name__,
        update_id=getattr(update, "update_id", None),
    )
    _capture_sentry(exc)
    await _notify(event)
    return True


def _capture_sentry(exc: BaseException) -> None:
    try:
        import sentry_sdk  # noqa: PLC0415
    except ImportError:
        return
    hub = getattr(sentry_sdk, "Hub", None)
    if hub is None:
        return
    client = getattr(hub.current, "client", None)
    if client is None:
        return
    with contextlib.suppress(Exception):
        sentry_sdk.capture_exception(exc)


async def _notify(event: ErrorEvent) -> None:
    update = event.update
    target: Message | CallbackQuery | None = None
    if update is not None:
        target = update.message or update.callback_query
    if target is None:
        return
    text = "⚠️ Произошла ошибка. Попробуйте позже."
    with contextlib.suppress(Exception):
        if isinstance(target, CallbackQuery):
            await target.answer(text, show_alert=False)
        else:
            await target.answer(text)
