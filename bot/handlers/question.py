"""Catch-all message handler: routes user text through the RAG pipeline."""

from __future__ import annotations

import html
import re

import structlog
from aiogram import Bot, F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.repositories import save_conversation, upsert_user
from bot.handlers.operator import do_handoff
from bot.keyboards.feedback import feedback_kb
from bot.services.openai_client import OpenAIClient
from bot.services.rag import RAGResult, ask
from bot.services.rerank import Reranker
from bot.services.retrieval import HybridRetriever
from bot.services.text import split_long_message

router = Router()
logger = structlog.get_logger(__name__)

OPERATOR_RE = re.compile(
    r"(?i)\b("
    r"позов(и|ите)\s+(оператора|человека|менеджера)|"
    r"нужен\s+(оператор|человек|менеджер)|"
    r"свяжите\s+с\s+(оператором|человеком|менеджером)|"
    r"talk\s+to\s+(human|agent|operator|manager)|"
    r"human\s+please|operator\s+please|live\s+agent"
    r")\b"
)

NON_TEXT_REPLY = "Я понимаю только текст. Напишите вопрос текстом."


@router.message(F.text)
async def handle_question(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    retriever: HybridRetriever,
    reranker: Reranker,
    openai: OpenAIClient,
) -> None:
    if message.from_user is None or not message.text:
        return

    text = message.text.strip()
    if not text or text.startswith("/"):
        return

    await upsert_user(
        session,
        user_id=message.from_user.id,
        full_name=message.from_user.full_name,
        username=message.from_user.username,
    )

    if OPERATOR_RE.search(text):
        await do_handoff(
            bot=bot,
            session=session,
            conv_id=None,
            user=message.from_user,
            reason="explicit_keyword",
            question=text,
        )
        await message.answer("🙋 Передал ваш вопрос оператору.")
        return

    if message.bot is not None:
        await message.bot.send_chat_action(message.chat.id, "typing")

    result: RAGResult = await ask(
        text,
        retriever=retriever,
        reranker=reranker,
        openai=openai,
        threshold=settings.relevance_threshold,
        min_gap=settings.relevance_min_gap,
        top_k_final=settings.top_k_final,
        rerank_top_n=settings.rerank_top_n,
    )

    conv = await save_conversation(
        session,
        user_id=message.from_user.id,
        question=text,
        answer=result.answer,
        sources=result.sources,
        top_scores=result.top_scores,
        answered=result.answered,
        latency_ms=result.latency_ms,
        relevance_score=result.top_scores[0] if result.top_scores else 0.0,
    )

    if not result.answered:
        await message.answer(result.answer)
        await do_handoff(
            bot=bot,
            session=session,
            conv_id=conv.id,
            user=message.from_user,
            reason="low_relevance",
            question=text,
        )
        return

    body = _format_answer(result)
    parts = split_long_message(body)
    if not parts:
        return
    for idx, part in enumerate(parts):
        markup = feedback_kb(conv.id) if idx == len(parts) - 1 else None
        await message.answer(part, reply_markup=markup)


@router.message(~F.text)
async def handle_non_text(message: Message) -> None:
    if message.from_user is None:
        return
    if message.document is not None:
        return
    await message.answer(NON_TEXT_REPLY)


def _format_answer(result: RAGResult) -> str:
    answer = result.answer
    if not result.sources:
        return answer
    sources = ", ".join(html.escape(name) for name in result.sources)
    return f"{answer}\n\n📎 <i>Источник: {sources}</i>"
