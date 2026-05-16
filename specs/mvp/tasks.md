---
status: ready
phase: tasks
created: 2026-05-16
constitution: v1.0.0
spec: specs/mvp/spec.md
design: specs/mvp/design.md
---

# Tasks — Telegram Bot AI Assistant MVP

Tracks с непересекающимися файлами. Track 0 первым (sequential). Затем A/B/C параллельно. Затем D. Затем T.

## Conventions

- Каждая задача = один commit-кандидат (но реальные коммиты — Phase 9).
- "Файлы" — точный список, выход за пределы запрещён.
- `[REPLACE]` — переписать существующий файл целиком.
- `[NEW]` — создать новый.
- Если файл в обоих списках tracks → ошибка декомпозиции, эскалировать.

---

## Track 0 — Foundation (sequential, blocks A/B/C/D)

**Owner**: developer-0
**Prerequisites**: spec.md, design.md прочитаны полностью.

### T0.1 — Обновить pyproject.toml
**Files**: `pyproject.toml` [REPLACE]
**Деталь**: см. design.md §3.12. Добавить: tenacity, structlog, prometheus-client, rank-bm25, slowapi, sentry-sdk[fastapi], pytest-mock, httpx. Опциональные extras: `rerank` (flashrank), `postgres`.
**Criteria**: `uv pip install -e ".[dev]"` проходит без ошибок.

### T0.2 — config.py
**Files**: `bot/config.py` [REPLACE]
**Деталь**: см. design.md §3.1. SecretStr на BOT_TOKEN/OPENAI_API_KEY/API_ADMIN_TOKEN/SENTRY_DSN. Validators (admin_ids CSV, threshold range).
**Criteria**: `from bot.config import settings` импортируется при пустом `.env` если все обязательные есть в env.

### T0.3 — Database (models + engine + repositories)
**Files**:
- `bot/db/__init__.py` [NEW]
- `bot/db/models.py` [REPLACE] — User, Conversation (+sources, top_scores, latency_ms), Document (+sha256, uploaded_by, deleted_at), Feedback, AuditLog
- `bot/db/engine.py` [REPLACE] — engine + async_session + init_db()
- `bot/db/repositories.py` [REPLACE] — все функции из design.md §3.3

**Criteria**: `pytest tests/test_repositories.py` (после T-track) проходит.

### T0.4 — Observability
**Files**:
- `bot/observability/__init__.py` [NEW]
- `bot/observability/logging_config.py` [NEW]
- `bot/observability/metrics.py` [NEW]

**Деталь**: см. design.md §3.5–3.6.
**Criteria**: `configure_logging()` вызывается без ошибок; `vector_store_size` exposed.

### T0.5 — Middlewares (общие)
**Files**:
- `bot/middlewares/__init__.py` [NEW]
- `bot/middlewares/db.py` [REPLACE] — typed `async_sessionmaker[AsyncSession]`
- `bot/middlewares/logging.py` [NEW] — structlog contextvars binding
- `bot/middlewares/metrics.py` [NEW] — bot_messages_total + duration

**Criteria**: smoke test `MockedBot` обрабатывает update с middleware chain.

### T0.6 — Services foundation
**Files**:
- `bot/services/__init__.py` [NEW]
- `bot/services/sanitizer.py` [NEW] — safe_filename + sanitize_text + escape_html (design §3.9)
- `bot/services/chunking.py` [REPLACE] — recursive, tiktoken hard-dep, Chunk(+char_start/char_end) (design §3.10)
- `bot/services/openai_client.py` [REPLACE] — OpenAIClient class + tenacity + SYSTEM_PROMPT + batch embeddings (design §3.11)

**Criteria**: `tests/test_sanitizer.py`, `test_chunking.py`, `test_openai_client.py` (после T-track) проходят.

### T0.7 — Mark Track 0 done
**Criteria**: `ruff check bot/config.py bot/db/ bot/observability/ bot/middlewares/db.py bot/middlewares/logging.py bot/middlewares/metrics.py bot/services/sanitizer.py bot/services/chunking.py bot/services/openai_client.py` clean. `mypy` clean на этих файлах.

---

## Track A — RAG Layer (parallel after Track 0)

**Owner**: developer-A
**Файлы НЕ пересекаются с B/C/D.**

### TA.1 — VectorStore
**Files**: `bot/services/embeddings.py` [REPLACE]
**Деталь**: см. design.md §4.1 + A1 §3.4. IndexFlatIP + normalize_L2 + asyncio.Lock + atomic save (tmp + replace + fsync) + meta.json schema versioning + recovery + snapshot-ref pattern. `load_async()`, `save_async()`, `add_chunks()`, `search()`, `rebuild()`.
**Criteria**: `tests/test_embeddings.py` проходит. Конкретно — atomic save survives mid-write kill.

### TA.2 — HybridRetriever
**Files**: `bot/services/retrieval.py` [NEW]
**Деталь**: design §4.2. BM25Okapi (rank_bm25) + dense (VectorStore.search) + RRF k=60. `RankedChunk(...)`, `retrieve(query, top_k)`, `rebuild_corpus(chunks)`. `asyncio.gather(dense, bm25)`.
**Criteria**: `tests/test_retrieval.py` проходит. Fallback на dense-only при `bm25_enabled=false`.

### TA.3 — Reranker
**Files**: `bot/services/rerank.py` [NEW]
**Деталь**: design §4.3. `Reranker` Protocol + `NoOpReranker` + `FlashRankReranker` (lazy import) + `make_reranker(settings)`.
**Criteria**: `tests/test_rerank.py` проходит. NoOp identity sort.

### TA.4 — RAG pipeline
**Files**: `bot/services/rag.py` [REPLACE]
**Деталь**: design §4.4 + A1 §3.7. Pure `ask(question, *, retriever, reranker, openai, settings) -> RAGResult`. Multi-signal gate. При gate fail — handoff без LLM call.
**Criteria**: `tests/test_rag.py` проходит. На пустой KB — корректный handoff.

### TA.5 — Upload service
**Files**: `bot/services/upload.py` [NEW]
**Деталь**: shared между TG admin и REST API. `process_document(content, filename, uploaded_by, session, store, retriever, openai)`. Шаги: sanitize → sha256 → dedup check → chunk → batch-embed → atomic add → BM25 rebuild → save Document + AuditLog.
**Criteria**: интегрирован в admin.py (Track B) и api/routes.py (Track C).

### TA.6 — Mark Track A done
**Criteria**: ruff + mypy clean на TA-файлах.

---

## Track B — Bot UX Layer (parallel after Track 0)

**Owner**: developer-B

### TB.1 — Filters + Keyboards + States
**Files**:
- `bot/filters/__init__.py` [NEW]
- `bot/filters/admin.py` [NEW] — AdminFilter
- `bot/keyboards/__init__.py` [NEW]
- `bot/keyboards/common.py` [REPLACE] — start_kb, admin_kb, doc_list_kb, confirm_kb, operator_handoff_kb
- `bot/keyboards/feedback.py` [NEW] — feedback_kb(conv_id)
- `bot/states/__init__.py` [NEW]
- `bot/states/upload.py` [NEW] — UploadStates
- `bot/states/feedback.py` [NEW] — FeedbackStates

### TB.2 — Middlewares (track B)
**Files**:
- `bot/middlewares/throttling.py` [NEW] — in-memory per-user
- `bot/middlewares/error.py` [NEW] — try/except + Sentry capture

### TB.3 — Services text utility
**Files**: `bot/services/text.py` [NEW] — `split_long_message(text, limit=4096)`

### TB.4 — Handlers
**Files**:
- `bot/handlers/__init__.py` [NEW] — `setup_routers()` собирает все routers в порядке: errors → admin → operator → feedback → start → question (catch-all last)
- `bot/handlers/start.py` [REPLACE] — /start, /help, callbacks examples/about
- `bot/handlers/question.py` [REPLACE] — catch-all, operator-keyword detect, RAG, save Conversation, feedback_kb
- `bot/handlers/feedback.py` [NEW] — up/down/skip callbacks + FSM comment
- `bot/handlers/admin.py` [REPLACE] — /admin menu, FSM upload, stats, docs list+delete, reindex
- `bot/handlers/operator.py` [NEW] — handoff to ADMIN_CHAT_ID с rich payload
- `bot/handlers/errors.py` [NEW] — `dp.errors()` global

**Criteria**: `tests/test_handlers.py`, `tests/test_fsm.py` проходят.

### TB.5 — Mark Track B done
**Criteria**: ruff + mypy clean.

---

## Track C — REST API Layer (parallel after Track 0)

**Owner**: developer-C

### TC.1 — API skeleton
**Files**:
- `api/__init__.py` [NEW]
- `api/deps.py` [NEW] — DI factories (`get_session`, `get_store`, `get_retriever`, `get_openai`, `get_settings`)
- `api/auth.py` [NEW] — `require_api_key` Header dep, `hmac.compare_digest`
- `api/schemas.py` [NEW] — Pydantic responses (design §6.2)
- `api/limiter.py` [NEW] — slowapi Limiter

### TC.2 — Routes
**Files**: `api/routes.py` [REPLACE]
**Endpoints**: см. design.md §6.4. Validation: file size 10MB, ext .md|.txt, sha256 dedup, sanitize.
**Criteria**: `tests/test_api.py` проходит. Все endpoints возвращают ожидаемые statuses.

### TC.3 — Mark Track C done
**Criteria**: ruff + mypy clean на `api/`.

---

## Track D — Entry + Deploy + Docs (sequential after A+B+C)

**Owner**: developer-D

### TD.1 — Entry point
**Files**: `bot/__main__.py` [NEW]
**Деталь**: design §7.1 wires all together (configure_logging, init_db, load store, retriever rebuild, reranker, bot+dp+middlewares+routers, FastAPI app, uvicorn.Server, asyncio.gather, shutdown handlers).
**Criteria**: `python -m bot` стартует, `/health` 200.

### TD.2 — Dockerfile (multi-stage)
**Files**: `Dockerfile` [REPLACE]
**Деталь**: design §7.2. Builder + runtime, non-root, HEALTHCHECK.
**Criteria**: `docker build .` успешно, image ≤500MB.

### TD.3 — docker-compose
**Files**: `docker-compose.yml` [REPLACE]
**Деталь**: design §7.3. Healthcheck, restart, volumes, resource limits.

### TD.4 — .env.example
**Files**: `.env.example` [REPLACE]
**Деталь**: design §7.5.

### TD.5 — CI
**Files**: `.github/workflows/ci.yml` [NEW]
**Деталь**: design §7.4. Lint + typecheck + test + docker-build.

### TD.6 — README + setup docs
**Files**:
- `README.md` [REPLACE] — quickstart, badges, features, mermaid, cost table, roadmap, contributing, license
- `docs/setup/01-tokens-and-keys.md` [NEW] — пошагово BOT_TOKEN, ADMIN_IDS, OPENAI_API_KEY, API_ADMIN_TOKEN, ADMIN_CHAT_ID, SENTRY_DSN
- `docs/setup/02-deployment.md` [NEW] — docker compose, env, troubleshooting
- `docs/setup/03-admin-guide.md` [NEW] — admin TG-flow + REST-API + reindex

**Criteria**: `docker compose up --build` стартует и `/health` 200.

### TD.7 — Mark Track D done

---

## Track T — Tests (after Tracks 0/A/B/C/D ready)

**Owner**: test-writer
**ВНИМАНИЕ**: test-writer пишет тесты **по spec.md + design.md**, не читая код тестируемых модулей.

### TT.1 — Fixtures
**Files**: `tests/conftest.py` [NEW]
- `mock_openai` — deterministic embeddings via SHA256
- `tmp_index` — VectorStore в tmpdir
- `in_memory_db` — async_session с :memory: SQLite
- `test_client` — FastAPI TestClient
- `mocked_bot` — aiogram MockedBot

### TT.2 — Service tests
**Files**:
- `tests/test_sanitizer.py`
- `tests/test_chunking.py`
- `tests/test_openai_client.py`
- `tests/test_embeddings.py` — persist/load roundtrip, atomic save, concurrent
- `tests/test_retrieval.py` — BM25, dense, RRF, fallback
- `tests/test_rerank.py`
- `tests/test_rag.py` — pipeline (mock openai)
- `tests/test_repositories.py`

### TT.3 — Integration tests
**Files**:
- `tests/test_handlers.py`
- `tests/test_fsm.py`
- `tests/test_api.py`
- `tests/test_smoke.py`

### TT.4 — Eval (опционально)
**Files**:
- `tests/eval/golden_questions.yaml` — ≥30 in-scope + ≥10 out-of-scope
- `tests/eval/run_ragas.py` — опц.

**Criteria**: coverage `bot/services/` ≥80%, `api/` ≥75%, handlers ≥60%. `pytest -v` all green.

---

## Dependencies graph

```
Track 0 ──┬── Track A
          ├── Track B
          └── Track C
                ↓
            Track D
                ↓
            Track T (tests for all)
```

## File-intersection check

| Track | Files |
|---|---|
| 0 | config, db/*, observability/*, middlewares/{__init__, db, logging, metrics}, services/{__init__, sanitizer, chunking, openai_client}, pyproject |
| A | services/{embeddings, retrieval, rerank, rag, upload} |
| B | filters/*, keyboards/*, states/*, middlewares/{throttling, error}, services/text, handlers/* |
| C | api/* |
| D | bot/__main__, Dockerfile, docker-compose.yml, .env.example, .github/workflows/ci.yml, README, docs/setup/* |
| T | tests/* |

Пересечений нет. ✅
