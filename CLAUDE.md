# CLAUDE.md

## Project Overview

**telegram-bot-ai-assistant** — Telegram-бот техподдержки на базе RAG (Retrieval-Augmented Generation). Отвечает на вопросы пользователей по корпоративной базе знаний (FAQ, документация), эскалирует к оператору при низкой релевантности. Цель: показывать клиентам как готовый MVP + публикация на GitHub.

## Tech Stack

Backend: Python 3.12 · aiogram 3 (Telegram) · FastAPI (admin API) · SQLAlchemy 2 async + SQLite (aiosqlite) · pydantic-settings
RAG: OpenAI API (gpt-4o-mini + text-embedding-3-small) · FAISS · tiktoken
Infra: Docker · uv (package manager)
Dev: ruff (lint+format) · mypy (strict) · pytest + pytest-asyncio

## Development Environment

Все команды через `uv run` из корня проекта:

```bash
uv run pytest -v
uv run ruff check . && uv run ruff format --check .
uv run mypy bot api
uv run python -m bot
```

Docker:
```bash
docker compose up -d --build
```

Перед запуском заполнить `.env` (см. `.env.example`). Обязательные ключи: `BOT_TOKEN`, `OPENAI_API_KEY`, `API_ADMIN_TOKEN`. `ADMIN_IDS` - для админ-команд бота.

## Architecture Principles

- **RAG pipeline = pure function**: `ask(question) -> RAGResult`. Никаких side-effects кроме внешних API.
- **Async-first**: все I/O (DB, OpenAI, Telegram, FAISS persistence) — асинхронно. Блокирующие вызовы FAISS оборачивать в `asyncio.to_thread`.
- **Persistence atomicity**: FAISS-индекс пишется через tmp-файл + atomic rename. Никаких partial writes.
- **Boundary validation**: input от пользователя/админа/API валидируется на входе. Внутренний код доверяет своим типам.
- **Cost-conscious**: embeddings — батчем (один запрос на N чанков), retry с экспоненциальным backoff, top-k ограничен.

## Repository Layout

```
bot/                     — Telegram-бот (aiogram 3)
  handlers/              — start, question, admin, feedback, operator, errors
  services/              — rag, retrieval, rerank, embeddings (FAISS), chunking, upload, reindex, sanitizer, openai_client
  db/                    — SQLAlchemy models + repositories
  middlewares/           — db session, logging, metrics, throttling
  observability/         — structlog, Prometheus metrics
  keyboards/             — inline keyboards
  config.py              — pydantic-settings (.env)
  __main__.py            — entrypoint (bot + FastAPI together)
api/                     — FastAPI routes (health, stats, docs upload)
tests/                   — pytest
data/
  knowledge/             — seed-документы (.md, .txt)
  faiss_index/           — persisted index (gitignored)
  bot.db                 — SQLite (gitignored)
specs/                   — constitution, research, architecture, mvp spec/design/tasks, review, backlog
docs/                    — пользовательская/админ документация, скриншоты
```

## Code Style

- Python 3.12, **type hints обязательны** (`mypy --strict` проходит)
- `ruff` правила: E, F, I, N, UP, ANN, B, A, SIM (см. `pyproject.toml`)
- Async-функции: `async def`, не оборачивать sync в `loop.run_in_executor` без необходимости
- Pydantic для всех внешних границ (FastAPI request/response, settings)
- SQLAlchemy 2 mapped-style (`Mapped[T]`), не legacy declarative
- Без комментариев, если код самообъясняющий. Комментарий = объяснить «почему», не «что»
- Без emoji в исходниках кроме user-facing строк (TG-сообщения)

## Testing

- `pytest -v` из корня. `asyncio_mode = "auto"` уже в pyproject
- Тесты пишутся **по спеке**, не по коду
- Mock OpenAI клиент через `pytest-mock` или DI override
- Smoke-тесты для FAISS персистенса (save → reload → search)
- E2E aiogram — через `aiogram.test_utils` или прямые вызовы handlers с замоканной сессией
- Coverage цель: ≥70% на `bot/services/` и `api/`

## Git

- Conventional Commits: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `ci`
- Ветка-на-фичу: `feat/<short-name>`, `fix/<short-name>`
- **Push только с явного разрешения пользователя**
- `CLAUDE.md` и `specs/` в репозитории. `.claude/settings.local.json` скрыт глобальным gitignore — не добавлять в локальный `.gitignore`

## Security & Production

- `OPENAI_API_KEY`, `BOT_TOKEN` — только через env, никогда в логах
- Admin-эндпоинты API (`POST /api/docs`) защищены токеном (см. `API_ADMIN_TOKEN` в `.env`)
- Загружаемые документы санитизируются от prompt-injection (см. `bot/services/sanitizer.py`)
- Rate limiting на пользователя — middleware `bot/middlewares/throttling.py`
- Логи структурированные (`structlog`), без PII (имена/контакты не пишем целиком)

## References

- Research-отчёт: `specs/research/01-research-report.md`
- Constitution: `specs/CONSTITUTION.md`
- Backlog: `specs/backlog.md`
