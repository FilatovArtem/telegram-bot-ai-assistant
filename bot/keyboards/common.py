"""Inline keyboards for the bot. Literal UTF-8 emoji, no escape sequences."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class _DocLike(Protocol):
    id: int
    filename: str


def start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="ask")],
            [
                InlineKeyboardButton(text="💡 Примеры", callback_data="examples"),
                InlineKeyboardButton(text="ℹ️ О боте", callback_data="about"),
            ],
            [InlineKeyboardButton(text="👤 Оператор", callback_data="operator:request")],
        ],
    )


def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="📄 Документы", callback_data="admin:docs")],
            [
                InlineKeyboardButton(text="📤 Загрузить", callback_data="admin:upload"),
                InlineKeyboardButton(text="🔄 Reindex", callback_data="admin:reindex"),
            ],
        ],
    )


def doc_list_kb(docs: Sequence[_DocLike]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for doc in docs:
        label = f"🗑 {doc.filename[:48]}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"admin:doc_delete:{doc.id}")],
        )
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(action: str, payload: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да",
                    callback_data=f"confirm:{action}:{payload}",
                ),
                InlineKeyboardButton(text="❌ Нет", callback_data="confirm:cancel"),
            ],
        ],
    )


def operator_handoff_kb(conv_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✋ Взять",
                    callback_data=f"operator:claim:{conv_id}",
                ),
                InlineKeyboardButton(
                    text="🚫 Игнорировать",
                    callback_data=f"operator:ignore:{conv_id}",
                ),
            ],
        ],
    )


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel")],
        ],
    )


__all__ = [
    "admin_kb",
    "cancel_kb",
    "confirm_kb",
    "doc_list_kb",
    "operator_handoff_kb",
    "start_kb",
]
