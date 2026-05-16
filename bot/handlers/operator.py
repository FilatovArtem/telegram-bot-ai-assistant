"""Operator handoff: forwards user request into ADMIN_CHAT_ID with rich payload."""

from __future__ import annotations

import contextlib
import html
from datetime import UTC, datetime

import structlog
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message, User
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.repositories import audit_log_insert, get_conversation
from bot.filters.admin import AdminFilter
from bot.keyboards.common import operator_handoff_kb
from bot.services.text import split_long_message

router = Router()
logger = structlog.get_logger(__name__)


@router.callback_query(F.data == "operator:request")
async def cb_operator_request(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    await callback.answer()
    await do_handoff(
        bot=bot,
        session=session,
        conv_id=None,
        user=user,
        reason="explicit_button",
        question="(пользователь нажал кнопку «Оператор»)",
    )
    if isinstance(callback.message, Message):
        await callback.message.answer("🙋 Ваш запрос передан оператору.")


@router.callback_query(F.data.startswith("operator:request:"))
async def cb_operator_request_for_conv(
    callback: CallbackQuery,
    session: AsyncSession,
    bot: Bot,
) -> None:
    user = callback.from_user
    if user is None:
        await callback.answer()
        return
    conv_id = _parse_id(callback.data, prefix="operator:request:")
    question = "(пользователь запросил оператора)"
    if conv_id is not None:
        conv = await get_conversation(session, conv_id)
        if conv is not None:
            question = conv.question
    await callback.answer()
    await do_handoff(
        bot=bot,
        session=session,
        conv_id=conv_id,
        user=user,
        reason="explicit_button",
        question=question,
    )
    if isinstance(callback.message, Message):
        await callback.message.answer("🙋 Ваш запрос передан оператору.")


@router.callback_query(AdminFilter(), F.data.startswith("operator:claim:"))
async def cb_operator_claim(callback: CallbackQuery, session: AsyncSession) -> None:
    admin = callback.from_user
    conv_id = _parse_id(callback.data, prefix="operator:claim:")
    if admin is None or conv_id is None:
        await callback.answer()
        return
    admin_tag = f"@{admin.username}" if admin.username else admin.full_name
    text = f"✅ Взято в работу: {html.escape(admin_tag)}"
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text)
        except TelegramBadRequest:
            await callback.message.answer(text)
    await audit_log_insert(
        session,
        admin_id=admin.id,
        action="operator_claim",
        target=str(conv_id),
    )
    await callback.answer("Готово")


@router.callback_query(AdminFilter(), F.data.startswith("operator:ignore:"))
async def cb_operator_ignore(callback: CallbackQuery, session: AsyncSession) -> None:
    admin = callback.from_user
    conv_id = _parse_id(callback.data, prefix="operator:ignore:")
    if admin is None or conv_id is None:
        await callback.answer()
        return
    admin_tag = f"@{admin.username}" if admin.username else admin.full_name
    text = f"🚫 Игнорировано: {html.escape(admin_tag)}"
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_text(text)
        except TelegramBadRequest:
            await callback.message.answer(text)
    await audit_log_insert(
        session,
        admin_id=admin.id,
        action="operator_ignore",
        target=str(conv_id),
    )
    await callback.answer()


async def do_handoff(
    *,
    bot: Bot,
    session: AsyncSession,
    conv_id: int | None,
    user: User,
    reason: str,
    question: str,
) -> None:
    chat_id = settings.admin_chat_id
    if chat_id == 0:
        logger.warning("operator.handoff.disabled", reason=reason)
        try:
            await bot.send_message(
                user.id,
                "🙋 Запрос зафиксирован. Оператор свяжется с вами при первой возможности.",
            )
        except Exception:  # noqa: BLE001
            logger.exception("operator.notify_user_failed")
        return

    payload = _render_payload(user=user, question=question, conv_id=conv_id, reason=reason)
    parts = split_long_message(payload)
    if not parts:
        return

    last_sent_id: int | None = None
    for idx, part in enumerate(parts):
        markup = operator_handoff_kb(conv_id) if idx == len(parts) - 1 and conv_id else None
        try:
            sent = await bot.send_message(chat_id, part, reply_markup=markup)
            last_sent_id = sent.message_id
        except TelegramBadRequest as exc:
            logger.error("operator.handoff.send_failed", error=str(exc))
            with contextlib.suppress(Exception):
                await bot.send_message(
                    user.id,
                    "⚠️ Не удалось передать оператору. Попробуйте позже.",
                )
            return

    logger.info(
        "operator.handoff.sent",
        reason=reason,
        conv_id=conv_id,
        user_id=user.id,
        admin_msg_id=last_sent_id,
    )

    await audit_log_insert(
        session,
        admin_id=0,
        action="handoff",
        target=str(conv_id) if conv_id is not None else "",
        meta={"reason": reason, "user_id": user.id},
    )


def _render_payload(*, user: User, question: str, conv_id: int | None, reason: str) -> str:
    full_name = html.escape(user.full_name)
    username = f"@{html.escape(user.username)}" if user.username else "—"
    link = f"tg://user?id={user.id}"
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    conv_line = f"Conversation: #{conv_id}" if conv_id is not None else "Conversation: —"
    safe_question = html.escape(question)
    return (
        "🆘 <b>Hand-off</b>\n"
        f'User: <b>{full_name}</b> ({username}, ID <a href="{link}">{user.id}</a>)\n'
        f"{conv_line}\n"
        f"Reason: {reason}\n"
        f"At: {when}\n\n"
        f"Question:\n{safe_question}"
    )


def _parse_id(data: str | None, *, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    return int(raw) if raw.isdigit() else None


__all__ = ["do_handoff", "router"]
