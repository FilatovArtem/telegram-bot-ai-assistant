"""Router composition. Order is significant — catch-all (question) goes last."""

from __future__ import annotations

from aiogram import Router


def setup_routers() -> Router:
    from bot.handlers import admin, errors, feedback, operator, question, start  # noqa: PLC0415

    root = Router()
    root.include_router(errors.router)
    root.include_router(admin.router)
    root.include_router(operator.router)
    root.include_router(feedback.router)
    root.include_router(start.router)
    root.include_router(question.router)
    return root


__all__ = ["setup_routers"]
