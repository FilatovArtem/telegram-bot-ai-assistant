"""Inline keyboard rendered under bot answers — vote up/down or call operator."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def feedback_kb(conv_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👍 Помог", callback_data=f"fb:up:{conv_id}"),
                InlineKeyboardButton(text="👎 Не помог", callback_data=f"fb:down:{conv_id}"),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Оператор",
                    callback_data=f"operator:request:{conv_id}",
                ),
            ],
        ],
    )


__all__ = ["feedback_kb"]
