"""AdminFilter: passes only when from_user.id is in settings.admin_ids."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from bot.config import settings


class AdminFilter(BaseFilter):
    async def __call__(self, obj: Message | CallbackQuery) -> bool:
        user = obj.from_user
        if user is None:
            return False
        return user.id in settings.admin_ids
