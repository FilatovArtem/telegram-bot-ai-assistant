"""Feedback collection: thumbs up/down with optional comment via FSM."""

from __future__ import annotations

import contextlib

import structlog
from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Feedback
from bot.db.repositories import get_conversation, save_feedback
from bot.observability.metrics import feedback_total
from bot.states.feedback import FeedbackStates

router = Router()
logger = structlog.get_logger(__name__)

COMMENT_MAX_CHARS = 500
PROMPT_COMMENT = "Спасибо за фидбек! Что было не так? Можете кратко описать или нажать /skip."


@router.callback_query(F.data.startswith("fb:up:"))
async def cb_feedback_up(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    conv_id = _parse_id(callback.data, "fb:up:")
    if conv_id is None or callback.from_user is None:
        await callback.answer()
        return
    conv = await get_conversation(session, conv_id)
    if conv is None or conv.user_id != callback.from_user.id:
        await callback.answer("Неверная сессия.", show_alert=False)
        return
    await save_feedback(session, conversation_id=conv_id, vote="up")
    feedback_total.labels(vote="up").inc()
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Спасибо!")


@router.callback_query(F.data.startswith("fb:down:"))
async def cb_feedback_down(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    conv_id = _parse_id(callback.data, "fb:down:")
    if conv_id is None or callback.from_user is None:
        await callback.answer()
        return
    conv = await get_conversation(session, conv_id)
    if conv is None or conv.user_id != callback.from_user.id:
        await callback.answer("Неверная сессия.", show_alert=False)
        return
    feedback = await save_feedback(session, conversation_id=conv_id, vote="down")
    feedback_total.labels(vote="down").inc()
    if isinstance(callback.message, Message):
        with contextlib.suppress(Exception):
            await callback.message.edit_reply_markup(reply_markup=None)
    await state.set_state(FeedbackStates.waiting_comment)
    await state.update_data(feedback_id=feedback.id, conv_id=conv_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(PROMPT_COMMENT)


@router.callback_query(StateFilter(FeedbackStates.waiting_comment), F.data == "fb:skip")
async def cb_feedback_skip(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Хорошо")
    if isinstance(callback.message, Message):
        await callback.message.answer("Ок, без комментария.")


@router.message(StateFilter(FeedbackStates.waiting_comment), Command("skip"))
async def cmd_skip_comment(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Ок, без комментария.")


@router.message(StateFilter(FeedbackStates.waiting_comment), F.text)
async def fsm_feedback_comment(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    if not message.text:
        return
    data = await state.get_data()
    feedback_id = data.get("feedback_id")
    if not isinstance(feedback_id, int):
        await state.clear()
        return
    comment = message.text.strip()[:COMMENT_MAX_CHARS]
    fb = await session.scalar(select(Feedback).where(Feedback.id == feedback_id))
    if fb is not None:
        fb.comment = comment
        await session.commit()
    await state.clear()
    await message.answer("Спасибо за фидбек!")


def _parse_id(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    return int(raw) if raw.isdigit() else None
