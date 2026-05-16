"""Repository functions. Pure async, one commit per call. Concurrency handled by callers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import AuditLog, Conversation, Document, Feedback, User


async def upsert_user(
    session: AsyncSession,
    *,
    user_id: int,
    full_name: str,
    username: str | None = None,
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        user = User(id=user_id, full_name=full_name, username=username)
        session.add(user)
    else:
        user.full_name = full_name
        user.username = username
    await session.commit()
    return user


async def count_users(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(User.id)))
    return int(result.scalar_one())


async def save_conversation(
    session: AsyncSession,
    *,
    user_id: int,
    question: str,
    answer: str,
    sources: list[str],
    top_scores: list[float],
    answered: bool,
    latency_ms: int,
    relevance_score: float = 0.0,
) -> Conversation:
    conv = Conversation(
        user_id=user_id,
        question=question,
        answer=answer,
        sources=sources,
        top_scores=top_scores,
        answered=answered,
        latency_ms=latency_ms,
        relevance_score=relevance_score,
    )
    session.add(conv)
    await session.commit()
    return conv


async def get_conversation(session: AsyncSession, conversation_id: int) -> Conversation | None:
    return await session.get(Conversation, conversation_id)


async def count_conversations(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> int:
    stmt = select(func.count(Conversation.id))
    if since is not None:
        stmt = stmt.where(Conversation.created_at >= since)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def count_unanswered(
    session: AsyncSession,
    *,
    since: datetime | None = None,
) -> int:
    stmt = select(func.count(Conversation.id)).where(Conversation.answered.is_(False))
    if since is not None:
        stmt = stmt.where(Conversation.created_at >= since)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_top_unanswered_questions(
    session: AsyncSession,
    limit: int = 5,
) -> list[tuple[str, int]]:
    count_col = func.count(Conversation.id).label("cnt")
    stmt = (
        select(Conversation.question, count_col)
        .where(Conversation.answered.is_(False))
        .group_by(Conversation.question)
        .order_by(desc("cnt"))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return [(row.question, int(row.cnt)) for row in result.all()]


async def get_recent_user_conversations(
    session: AsyncSession,
    user_id: int,
    limit: int = 5,
) -> list[Conversation]:
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def save_document(
    session: AsyncSession,
    *,
    filename: str,
    chunk_count: int,
    sha256: str,
    uploaded_by: int | None,
) -> Document:
    doc = Document(
        filename=filename,
        chunk_count=chunk_count,
        sha256=sha256,
        uploaded_by=uploaded_by,
    )
    session.add(doc)
    await session.commit()
    return doc


async def document_exists_by_sha256(
    session: AsyncSession,
    sha256: str,
) -> Document | None:
    stmt = select(Document).where(
        Document.sha256 == sha256,
        Document.deleted_at.is_(None),
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_documents(
    session: AsyncSession,
    *,
    include_deleted: bool = False,
) -> list[Document]:
    stmt = select(Document).order_by(Document.uploaded_at.desc())
    if not include_deleted:
        stmt = stmt.where(Document.deleted_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_document(session: AsyncSession, doc_id: int) -> Document | None:
    return await session.get(Document, doc_id)


async def mark_document_deleted(session: AsyncSession, doc_id: int) -> bool:
    doc = await session.get(Document, doc_id)
    if doc is None or doc.deleted_at is not None:
        return False
    doc.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    return True


async def save_feedback(
    session: AsyncSession,
    *,
    conversation_id: int,
    vote: str,
    comment: str | None = None,
) -> Feedback:
    fb = Feedback(conversation_id=conversation_id, vote=vote, comment=comment)
    session.add(fb)
    await session.commit()
    return fb


async def get_feedback_ratio(session: AsyncSession) -> tuple[int, int]:
    stmt = select(Feedback.vote, func.count(Feedback.id)).group_by(Feedback.vote)
    result = await session.execute(stmt)
    counts = {vote: int(cnt) for vote, cnt in result.all()}
    return counts.get("up", 0), counts.get("down", 0)


async def audit_log_insert(
    session: AsyncSession,
    *,
    admin_id: int,
    action: str,
    target: str,
    meta: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(admin_id=admin_id, action=action, target=target, meta=meta)
    session.add(entry)
    await session.commit()
    return entry
