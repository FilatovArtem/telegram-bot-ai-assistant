"""FSM tests: feedback comment + upload file flow — spec A3 §12.5."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from bot.handlers.feedback import (
    cb_feedback_down,
    cmd_skip_comment,
    fsm_feedback_comment,
)
from bot.states.feedback import FeedbackStates


def _make_user(user_id: int = 100) -> Any:
    user = MagicMock()
    user.id = user_id
    user.full_name = "A"
    user.username = "a"
    return user


def _make_message(text: str = "comment text", user_id: int = 100) -> Any:
    msg = MagicMock()
    msg.text = text
    msg.from_user = _make_user(user_id)
    msg.answer = AsyncMock()
    return msg


class FakeState:
    """Minimal stand-in for FSMContext."""

    def __init__(self) -> None:
        self._state: Any = None
        self._data: dict[str, Any] = {}

    async def set_state(self, state: Any) -> None:
        self._state = state

    async def get_state(self) -> Any:
        return self._state

    async def update_data(self, **kw: Any) -> None:
        self._data.update(kw)

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def clear(self) -> None:
        self._state = None
        self._data = {}


class TestFeedbackFsm:
    async def test_down_enters_waiting_comment(self, in_memory_db: Any) -> None:
        from bot.db.repositories import save_conversation, upsert_user  # noqa: PLC0415

        await upsert_user(in_memory_db, user_id=100, full_name="A")
        conv = await save_conversation(
            in_memory_db,
            user_id=100,
            question="q",
            answer="a",
            sources=[],
            top_scores=[],
            answered=True,
            latency_ms=1,
        )

        cb = MagicMock()
        cb.from_user = _make_user(100)
        cb.data = f"fb:down:{conv.id}"
        cb.message = _make_message()
        cb.message.edit_reply_markup = AsyncMock()
        cb.answer = AsyncMock()
        state = FakeState()

        await cb_feedback_down(cb, in_memory_db, state)  # type: ignore[arg-type]

        assert state._state == FeedbackStates.waiting_comment
        assert "feedback_id" in state._data

    async def test_comment_text_saves_and_clears(self, in_memory_db: Any) -> None:
        from bot.db.models import Feedback  # noqa: PLC0415
        from bot.db.repositories import save_conversation, save_feedback, upsert_user  # noqa: PLC0415

        await upsert_user(in_memory_db, user_id=100, full_name="A")
        conv = await save_conversation(
            in_memory_db,
            user_id=100,
            question="q",
            answer="a",
            sources=[],
            top_scores=[],
            answered=True,
            latency_ms=1,
        )
        fb = await save_feedback(in_memory_db, conversation_id=conv.id, vote="down")

        state = FakeState()
        await state.set_state(FeedbackStates.waiting_comment)
        await state.update_data(feedback_id=fb.id, conv_id=conv.id)

        msg = _make_message(text="не точно ответил")
        await fsm_feedback_comment(msg, in_memory_db, state)  # type: ignore[arg-type]

        from sqlalchemy import select  # noqa: PLC0415

        res = await in_memory_db.execute(select(Feedback).where(Feedback.id == fb.id))
        loaded = res.scalar_one()
        assert loaded.comment == "не точно ответил"
        assert state._state is None

    async def test_skip_clears_state(self, in_memory_db: Any) -> None:
        state = FakeState()
        await state.set_state(FeedbackStates.waiting_comment)
        msg = _make_message(text="/skip")
        await cmd_skip_comment(msg, state)  # type: ignore[arg-type]
        assert state._state is None


class TestUploadFsm:
    async def test_upload_state_class_defines_waiting_file(self) -> None:
        from bot.states.upload import UploadStates  # noqa: PLC0415

        assert hasattr(UploadStates, "waiting_file")
