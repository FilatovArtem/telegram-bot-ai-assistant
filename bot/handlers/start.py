"""Welcome handlers: /start, /help, callbacks for examples/about/ask."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.repositories import get_documents, upsert_user
from bot.keyboards.common import start_kb

router = Router()

WELCOME_TEXT = (
    "🤖 <b>AI-ассистент</b>\n\n"
    "Я отвечаю на вопросы по базе знаний компании.\n"
    "Просто напишите ваш вопрос в свободной форме."
)

HELP_TEXT = (
    "ℹ️ <b>Справка</b>\n\n"
    "• Напишите вопрос — я найду ответ в базе знаний.\n"
    "• «💡 Примеры» — подсказки по доступным темам.\n"
    "• «👤 Оператор» — связаться с человеком."
)

ABOUT_TEXT = (
    "ℹ️ <b>О боте</b>\n\n"
    "Бот использует технологию RAG (Retrieval-Augmented Generation):\n"
    "1. Ищет релевантные фрагменты в базе знаний.\n"
    "2. Формирует ответ через LLM.\n"
    "3. Указывает источник.\n\n"
    "Если ответ не найден — передаёт запрос оператору."
)

FALLBACK_EXAMPLES: tuple[str, ...] = (
    "Какие у вас курсы?",
    "Как оплатить?",
    "Сколько длится обучение?",
    "Как получить сертификат?",
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    if message.from_user is not None:
        await upsert_user(
            session,
            user_id=message.from_user.id,
            full_name=message.from_user.full_name,
            username=message.from_user.username,
        )
    await message.answer(WELCOME_TEXT, reply_markup=start_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=start_kb())


@router.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(ABOUT_TEXT, reply_markup=start_kb())
    await callback.answer()


@router.callback_query(F.data == "ask")
async def cb_ask(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text("❓ Напишите ваш вопрос:")
    await callback.answer()


@router.callback_query(F.data == "examples")
async def cb_examples(callback: CallbackQuery, session: AsyncSession) -> None:
    examples = await _build_examples(session)
    lines = ["💡 <b>Примеры вопросов</b>", ""]
    lines.extend(f"• {item}" for item in examples)
    text = "\n".join(lines)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=start_kb())
    await callback.answer()


async def _build_examples(session: AsyncSession) -> tuple[str, ...]:
    docs = await get_documents(session)
    if not docs:
        return FALLBACK_EXAMPLES
    titles = [_title_from_filename(d.filename) for d in docs[:4]]
    titles = [t for t in titles if t]
    if not titles:
        return FALLBACK_EXAMPLES
    return tuple(f"Расскажите про «{t}»" for t in titles)


def _title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").strip()
