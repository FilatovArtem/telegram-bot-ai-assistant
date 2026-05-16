"""Admin panel: stats, document list, upload, reindex.

AdminFilter is applied at router level; handlers don't repeat the check."""

from __future__ import annotations

import structlog
from aiogram import Bot, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repositories import (
    audit_log_insert,
    count_conversations,
    count_unanswered,
    count_users,
    get_document,
    get_documents,
    get_feedback_ratio,
    get_top_unanswered_questions,
    mark_document_deleted,
)
from bot.filters.admin import AdminFilter
from bot.keyboards.common import (
    admin_kb,
    cancel_kb,
    confirm_kb,
    doc_list_kb,
)
from bot.services.embeddings import VectorStore
from bot.services.openai_client import OpenAIClient
from bot.services.reindex import reindex_after_document_delete
from bot.services.retrieval import HybridRetriever
from bot.services.upload import (
    DocumentAlreadyExists,
    UploadError,
    process_document,
)
from bot.states.upload import UploadStates

router = Router()
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

logger = structlog.get_logger(__name__)

ADMIN_TITLE = "🔧 <b>Админ-панель</b>"
UPLOAD_PROMPT = "📤 Отправьте .md или .txt файл (до 10 МБ)."
ALLOWED_EXTS: tuple[str, ...] = (".md", ".txt")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    await message.answer(ADMIN_TITLE, reply_markup=admin_kb())


@router.callback_query(F.data == "admin:menu")
async def cb_menu(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(ADMIN_TITLE, reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(ADMIN_TITLE, reply_markup=admin_kb())
    await callback.answer("Отменено")


@router.callback_query(F.data == "admin:stats")
async def cb_stats(
    callback: CallbackQuery,
    session: AsyncSession,
    store: VectorStore,
) -> None:
    users = await count_users(session)
    total_q = await count_conversations(session)
    unanswered = await count_unanswered(session)
    up, down = await get_feedback_ratio(session)
    total_fb = up + down
    fb_ratio = (up / total_fb * 100) if total_fb else 0.0
    unanswered_pct = (unanswered / total_q * 100) if total_q else 0.0
    top_unanswered = await get_top_unanswered_questions(session, limit=5)

    lines = [
        "📊 <b>Статистика</b>",
        "",
        f"👥 Пользователей: {users}",
        f"❓ Вопросов: {total_q}",
        f"❌ Без ответа: {unanswered} ({unanswered_pct:.0f}%)",
        f"👍/👎: {up}/{down} ({fb_ratio:.0f}% positive)",
        f"📄 Чанков в индексе: {store.size}",
    ]
    if top_unanswered:
        lines.append("")
        lines.append("🔝 <b>Топ неотвеченных:</b>")
        for question, cnt in top_unanswered:
            short = question[:80] + "…" if len(question) > 80 else question
            lines.append(f"• {short} ({cnt})")
    lines.append("")
    lines.append("💰 <i>Cost note: смотрите Grafana /metrics для cost-per-day.</i>")
    text = "\n".join(lines)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=admin_kb())
    await callback.answer()


@router.callback_query(F.data == "admin:docs")
async def cb_docs(callback: CallbackQuery, session: AsyncSession) -> None:
    docs = await get_documents(session)
    if not docs:
        text = "📄 Документов пока нет."
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text, reply_markup=admin_kb())
        await callback.answer()
        return

    lines = ["📄 <b>Документы</b>", ""]
    for doc in docs:
        lines.append(f"• <b>{doc.filename}</b> ({doc.chunk_count} чанков)")
    text = "\n".join(lines)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=doc_list_kb(docs))
    await callback.answer()


@router.callback_query(F.data.startswith("admin:doc_delete:"))
async def cb_doc_delete_prompt(callback: CallbackQuery) -> None:
    doc_id = _parse_id(callback.data, "admin:doc_delete:")
    if doc_id is None:
        await callback.answer()
        return
    text = "Удалить документ? Действие необратимо."
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=confirm_kb("doc_delete", str(doc_id)),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm:doc_delete:"))
async def cb_doc_delete_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    store: VectorStore,
    retriever: HybridRetriever,
) -> None:
    doc_id = _parse_id(callback.data, "confirm:doc_delete:")
    admin = callback.from_user
    if doc_id is None or admin is None:
        await callback.answer()
        return
    doc = await get_document(session, doc_id)
    if doc is None or doc.deleted_at is not None:
        await callback.answer("Документ уже удалён", show_alert=True)
        return
    filename = doc.filename
    deleted = await mark_document_deleted(session, doc_id)
    if not deleted:
        await callback.answer("Документ уже удалён", show_alert=True)
        return
    await reindex_after_document_delete(
        deleted_filename=filename,
        store=store,
        retriever=retriever,
    )
    await audit_log_insert(
        session,
        admin_id=admin.id,
        action="doc_delete",
        target=str(doc_id),
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "✅ Документ удалён, индекс перестроен.",
            reply_markup=admin_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "confirm:cancel")
async def cb_confirm_cancel(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(ADMIN_TITLE, reply_markup=admin_kb())
    await callback.answer("Отменено")


@router.callback_query(F.data == "admin:upload")
async def cb_upload(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UploadStates.waiting_file)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(UPLOAD_PROMPT, reply_markup=cancel_kb())
    await callback.answer()


@router.message(StateFilter(UploadStates.waiting_file), F.document)
async def handle_upload_document(
    message: Message,
    session: AsyncSession,
    bot: Bot,
    state: FSMContext,
    store: VectorStore,
    retriever: HybridRetriever,
    openai: OpenAIClient,
) -> None:
    if message.from_user is None or message.document is None:
        return
    doc = message.document
    filename = doc.file_name or "unnamed"

    if not filename.lower().endswith(ALLOWED_EXTS):
        await message.answer("⚠️ Поддерживаются только .md и .txt файлы.")
        return
    if doc.file_size and doc.file_size > MAX_UPLOAD_BYTES:
        await message.answer("⚠️ Файл больше 10 МБ.")
        return

    await message.answer("⏳ Обрабатываю документ...")
    file_io = await bot.download(doc)
    if file_io is None:
        await message.answer("❌ Не удалось скачать файл.")
        return
    content = file_io.read()

    try:
        result = await process_document(
            content=content,
            filename=filename,
            uploaded_by=message.from_user.id,
            session=session,
            store=store,
            retriever=retriever,
            openai=openai,
        )
    except DocumentAlreadyExists as exc:
        await state.clear()
        await message.answer(
            f"♻️ Документ <b>{exc.filename}</b> уже загружен ранее.",
            reply_markup=admin_kb(),
        )
        return
    except UploadError as exc:
        await state.clear()
        await message.answer(f"⚠️ Файл отклонён: {exc}", reply_markup=admin_kb())
        return

    await state.clear()
    text = (
        f"✅ Документ <b>{result.filename}</b> загружен.\n"
        f"Создано чанков: {result.chunks_created}\n"
        f"Всего в индексе: {result.total_index_size}"
    )
    await message.answer(text, reply_markup=admin_kb())


@router.callback_query(F.data == "admin:reindex")
async def cb_reindex_prompt(callback: CallbackQuery) -> None:
    text = "Перестроить индекс из текущих документов? Это может занять время."
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=confirm_kb("reindex", "all"),
        )
    await callback.answer()


@router.callback_query(F.data == "confirm:reindex:all")
async def cb_reindex_confirm(
    callback: CallbackQuery,
    session: AsyncSession,
    store: VectorStore,
    retriever: HybridRetriever,
) -> None:
    admin = callback.from_user
    if admin is None:
        await callback.answer()
        return
    active = await get_documents(session)
    active_names = {d.filename for d in active}
    orphan_names = {c.filename for c in store.chunks} - active_names
    for filename in orphan_names:
        await reindex_after_document_delete(
            deleted_filename=filename,
            store=store,
            retriever=retriever,
        )
    if not orphan_names:
        await retriever.rebuild_corpus(store.chunks)
    await audit_log_insert(
        session,
        admin_id=admin.id,
        action="reindex",
        target="all",
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "✅ Индекс перестроен.",
            reply_markup=admin_kb(),
        )
    await callback.answer()


def _parse_id(data: str | None, prefix: str) -> int | None:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix) :]
    return int(raw) if raw.isdigit() else None
