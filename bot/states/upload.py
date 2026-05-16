from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class UploadStates(StatesGroup):
    waiting_file = State()
