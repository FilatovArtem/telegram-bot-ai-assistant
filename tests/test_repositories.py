"""Tests for bot.db.repositories — spec A2 §3, design §3.3."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bot.db.models import Conversation
from bot.db.repositories import (
    audit_log_insert,
    count_conversations,
    count_unanswered,
    count_users,
    document_exists_by_sha256,
    get_conversation,
    get_documents,
    get_feedback_ratio,
    get_recent_user_conversations,
    get_top_unanswered_questions,
    mark_document_deleted,
    save_conversation,
    save_document,
    save_feedback,
    upsert_user,
)


async def _conv(session: Any, user_id: int, *, answered: bool = True, question: str = "q") -> Conversation:
    return await save_conversation(
        session,
        user_id=user_id,
        question=question,
        answer="a",
        sources=["f.md"],
        top_scores=[0.9],
        answered=answered,
        latency_ms=100,
    )


class TestUsers:
    async def test_upsert_creates(self, in_memory_db: Any) -> None:
        user = await upsert_user(in_memory_db, user_id=42, full_name="A B", username="ab")
        assert user.id == 42
        assert user.full_name == "A B"
        assert user.username == "ab"

    async def test_upsert_updates(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=42, full_name="Old", username="x")
        user = await upsert_user(in_memory_db, user_id=42, full_name="New", username="y")
        assert user.full_name == "New"
        assert user.username == "y"
        assert await count_users(in_memory_db) == 1


class TestConversations:
    async def test_save_and_get(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        conv = await _conv(in_memory_db, 1)
        loaded = await get_conversation(in_memory_db, conv.id)
        assert loaded is not None
        assert loaded.sources == ["f.md"]
        assert loaded.top_scores == [0.9]

    async def test_count_total(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        await _conv(in_memory_db, 1)
        await _conv(in_memory_db, 1)
        assert await count_conversations(in_memory_db) == 2

    async def test_count_since(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        await _conv(in_memory_db, 1)
        future = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
        assert await count_conversations(in_memory_db, since=future) == 0

    async def test_count_unanswered(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        await _conv(in_memory_db, 1, answered=True)
        await _conv(in_memory_db, 1, answered=False)
        await _conv(in_memory_db, 1, answered=False)
        assert await count_unanswered(in_memory_db) == 2

    async def test_top_unanswered(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        for _ in range(3):
            await _conv(in_memory_db, 1, answered=False, question="same")
        await _conv(in_memory_db, 1, answered=False, question="other")
        result = await get_top_unanswered_questions(in_memory_db, limit=5)
        assert ("same", 3) in result

    async def test_recent_user_conversations(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        for i in range(7):
            await _conv(in_memory_db, 1, question=f"q{i}")
        recent = await get_recent_user_conversations(in_memory_db, 1, limit=5)
        assert len(recent) == 5


class TestDocuments:
    async def test_save_and_exists(self, in_memory_db: Any) -> None:
        doc = await save_document(
            in_memory_db,
            filename="a.md",
            chunk_count=3,
            sha256="abc",
            uploaded_by=1,
        )
        assert doc.id is not None
        existing = await document_exists_by_sha256(in_memory_db, "abc")
        assert existing is not None
        assert existing.filename == "a.md"

    async def test_exists_returns_none_for_missing(self, in_memory_db: Any) -> None:
        assert await document_exists_by_sha256(in_memory_db, "missing") is None

    async def test_mark_deleted_filters_listing(self, in_memory_db: Any) -> None:
        doc1 = await save_document(
            in_memory_db,
            filename="a.md",
            chunk_count=1,
            sha256="a",
            uploaded_by=1,
        )
        await save_document(
            in_memory_db,
            filename="b.md",
            chunk_count=1,
            sha256="b",
            uploaded_by=1,
        )
        await mark_document_deleted(in_memory_db, doc1.id)
        active = await get_documents(in_memory_db)
        assert all(d.filename != "a.md" for d in active)
        all_docs = await get_documents(in_memory_db, include_deleted=True)
        assert len(all_docs) == 2

    async def test_mark_deleted_missing_returns_false(self, in_memory_db: Any) -> None:
        assert await mark_document_deleted(in_memory_db, 999) is False


class TestFeedback:
    async def test_save_and_ratio(self, in_memory_db: Any) -> None:
        await upsert_user(in_memory_db, user_id=1, full_name="U")
        conv = await _conv(in_memory_db, 1)
        await save_feedback(in_memory_db, conversation_id=conv.id, vote="up")
        await save_feedback(in_memory_db, conversation_id=conv.id, vote="up")
        await save_feedback(in_memory_db, conversation_id=conv.id, vote="down")
        up, down = await get_feedback_ratio(in_memory_db)
        assert up == 2
        assert down == 1

    async def test_empty_ratio(self, in_memory_db: Any) -> None:
        up, down = await get_feedback_ratio(in_memory_db)
        assert up == 0
        assert down == 0


class TestAudit:
    async def test_insert(self, in_memory_db: Any) -> None:
        entry = await audit_log_insert(
            in_memory_db,
            admin_id=1,
            action="upload",
            target="x.md",
            meta={"doc_id": 1},
        )
        assert entry.id is not None
        assert entry.action == "upload"
        assert entry.meta == {"doc_id": 1}
