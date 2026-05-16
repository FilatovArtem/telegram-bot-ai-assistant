"""Tests for aiogram handlers — spec A3 §12.1.

Handlers are async functions accepting Message/CallbackQuery + DI keyword args.
We invoke them directly with manually crafted mocks (no full aiogram dispatcher).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers.feedback import cb_feedback_up
from bot.handlers.question import handle_non_text, handle_question
from bot.handlers.start import cmd_start


def _make_user(user_id: int = 100, full_name: str = "Alice", username: str | None = "alice") -> Any:
    user = MagicMock()
    user.id = user_id
    user.full_name = full_name
    user.username = username
    return user


def _make_message(text: str | None = "hello", user: Any = None) -> Any:
    msg = MagicMock()
    msg.text = text
    msg.from_user = user or _make_user()
    msg.chat = MagicMock(id=12345)
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    msg.document = None
    return msg


class TestStart:
    async def test_cmd_start_replies_welcome(self, in_memory_db: Any) -> None:
        msg = _make_message(text="/start")
        await cmd_start(msg, in_memory_db)
        msg.answer.assert_called_once()
        args, kwargs = msg.answer.call_args
        assert "AI-ассистент" in args[0]
        assert "reply_markup" in kwargs

    async def test_cmd_start_upserts_user(self, in_memory_db: Any) -> None:
        from bot.db.repositories import count_users  # noqa: PLC0415

        msg = _make_message(text="/start", user=_make_user(user_id=777))
        await cmd_start(msg, in_memory_db)
        assert await count_users(in_memory_db) == 1


class TestQuestion:
    async def test_high_relevance_replies_with_answer(
        self,
        in_memory_db: Any,
        mock_openai: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bot.services.rag import RAGResult  # noqa: PLC0415

        fake_result = RAGResult(
            answer="Здесь ответ [^1]",
            sources=["sample.md"],
            top_scores=[0.9],
            answered=True,
            latency_ms=42,
        )

        async def fake_ask(*args: Any, **kwargs: Any) -> RAGResult:
            return fake_result

        monkeypatch.setattr("bot.handlers.question.ask", fake_ask)

        msg = _make_message(text="что у вас есть?")
        bot = MagicMock()

        await handle_question(
            msg,
            in_memory_db,
            bot,
            retriever=MagicMock(),
            reranker=MagicMock(),
            openai=mock_openai,
        )
        msg.answer.assert_called()
        first_call = msg.answer.call_args_list[-1].args[0]
        assert "Здесь ответ" in first_call
        assert "sample.md" in first_call

    async def test_low_relevance_triggers_handoff(
        self,
        in_memory_db: Any,
        mock_openai: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from bot.services.rag import LOW_RELEVANCE_RESPONSE, RAGResult  # noqa: PLC0415

        fake_result = RAGResult(
            answer=LOW_RELEVANCE_RESPONSE,
            sources=[],
            top_scores=[0.3],
            answered=False,
            latency_ms=10,
        )

        async def fake_ask(*args: Any, **kwargs: Any) -> RAGResult:
            return fake_result

        handoff_calls: list[dict[str, Any]] = []

        async def fake_handoff(**kw: Any) -> None:
            handoff_calls.append(kw)

        monkeypatch.setattr("bot.handlers.question.ask", fake_ask)
        monkeypatch.setattr("bot.handlers.question.do_handoff", fake_handoff)

        msg = _make_message(text="неизвестный вопрос")
        bot = MagicMock()

        await handle_question(
            msg,
            in_memory_db,
            bot,
            retriever=MagicMock(),
            reranker=MagicMock(),
            openai=mock_openai,
        )
        assert len(handoff_calls) == 1
        assert handoff_calls[0]["reason"] == "low_relevance"

    async def test_operator_keyword_skips_rag(
        self,
        in_memory_db: Any,
        mock_openai: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ask_called = False

        async def fake_ask(*args: Any, **kwargs: Any) -> Any:
            nonlocal ask_called
            ask_called = True
            raise AssertionError("ask should not be called for operator keyword")

        handoff_calls: list[dict[str, Any]] = []

        async def fake_handoff(**kw: Any) -> None:
            handoff_calls.append(kw)

        monkeypatch.setattr("bot.handlers.question.ask", fake_ask)
        monkeypatch.setattr("bot.handlers.question.do_handoff", fake_handoff)

        msg = _make_message(text="Позовите оператора пожалуйста")
        bot = MagicMock()

        await handle_question(
            msg,
            in_memory_db,
            bot,
            retriever=MagicMock(),
            reranker=MagicMock(),
            openai=mock_openai,
        )
        assert ask_called is False
        assert len(handoff_calls) == 1
        assert handoff_calls[0]["reason"] == "explicit_keyword"

    async def test_command_ignored(
        self,
        in_memory_db: Any,
        mock_openai: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ask_called = False

        async def fake_ask(*args: Any, **kwargs: Any) -> Any:
            nonlocal ask_called
            ask_called = True
            return None

        monkeypatch.setattr("bot.handlers.question.ask", fake_ask)
        msg = _make_message(text="/start")
        bot = MagicMock()

        await handle_question(
            msg,
            in_memory_db,
            bot,
            retriever=MagicMock(),
            reranker=MagicMock(),
            openai=mock_openai,
        )
        assert ask_called is False
        msg.answer.assert_not_called()

    async def test_empty_text_ignored(
        self,
        in_memory_db: Any,
        mock_openai: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ask_called = False

        async def fake_ask(*args: Any, **kwargs: Any) -> Any:
            nonlocal ask_called
            ask_called = True

        monkeypatch.setattr("bot.handlers.question.ask", fake_ask)
        msg = _make_message(text="   ")
        bot = MagicMock()
        await handle_question(
            msg,
            in_memory_db,
            bot,
            retriever=MagicMock(),
            reranker=MagicMock(),
            openai=mock_openai,
        )
        assert ask_called is False

    async def test_non_text_fallback(self) -> None:
        msg = _make_message(text=None)
        msg.document = None
        await handle_non_text(msg)
        msg.answer.assert_called_once()
        assert "только текст" in msg.answer.call_args.args[0]


class TestFeedback:
    async def test_up_saves_vote(
        self,
        in_memory_db: Any,
    ) -> None:
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
        cb.from_user = _make_user(user_id=100)
        cb.data = f"fb:up:{conv.id}"
        cb.message = _make_message()
        cb.message.edit_reply_markup = AsyncMock()
        cb.answer = AsyncMock()

        await cb_feedback_up(cb, in_memory_db)

        from sqlalchemy import select  # noqa: PLC0415

        from bot.db.models import Feedback  # noqa: PLC0415

        res = await in_memory_db.execute(select(Feedback))
        items = list(res.scalars())
        assert len(items) == 1
        assert items[0].vote == "up"

    async def test_up_other_user_rejected(self, in_memory_db: Any) -> None:
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
        cb.from_user = _make_user(user_id=999)
        cb.data = f"fb:up:{conv.id}"
        cb.message = _make_message()
        cb.answer = AsyncMock()

        await cb_feedback_up(cb, in_memory_db)
        cb.answer.assert_called_with("Неверная сессия.", show_alert=False)
