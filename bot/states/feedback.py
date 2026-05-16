from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class FeedbackStates(StatesGroup):
    waiting_comment = State()
