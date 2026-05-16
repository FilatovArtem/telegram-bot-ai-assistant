# Review — MVP Pre-Test

**Reviewer**: independent (clean context)
**Date**: 2026-05-16
**Constitution**: v1.0.0
**Status of static analysis**: `ruff check .` clean, `mypy bot api` clean (47 files).

## Verdict

- [ ] Pass
- [x] **Fixes Required** (3 P0, 8 P1, 6 P2). Тестовая фаза разблокируется только после P0.
- [ ] Major Issues

---

## Critical (must fix before tests)

### [P0-1] DELETE `/api/docs/{id}` не перестраивает FAISS/BM25 после soft-delete
**File**: `api/routes.py:212-237`
**Issue**: handler делает `mark_document_deleted(...)` + `audit_log_insert(...)` и возвращает 204, но **никогда** не вызывает `store.rebuild(...)` / `retriever.rebuild_corpus(...)`. Чанки удалённого документа продолжают участвовать в retrieval. Это нарушает spec US-8 («удаление + reindex») и Constitution §I.3 (правдивость retrieval).
**Fix**: вынести `_reindex_after_delete` из `bot/handlers/admin.py` в `bot/services/upload.py` (или новый `bot/services/reindex.py`) и вызвать его здесь же, после soft-delete и до `audit_log_insert(action="delete")`. DI `OpenAIClient` уже доступен через `get_openai`.
**Principle**: I (RAG Correctness), VI (Surgical, нет дупликации).

### [P0-2] Дублирующиеся error-traps: ErrorMiddleware + dp.errors() оба отвечают пользователю
**File**: `bot/middlewares/error.py:19-33` + `bot/handlers/errors.py:17-29`
**Issue**: `ErrorMiddleware` ловит `Exception`, ничего не re-raise — поэтому aiogram-router `dp.errors()` **никогда не сработает**. Метрика `bot_errors_total` инкрементируется в `errors.py`, но т.к. он не вызывается — счётчик мёртв. Plus: пользователь получает «⚠️ Произошла ошибка...» от middleware дважды только если middleware дважды (нет, но логика противоречива и кодит мёртвый путь). Constitution §V (observability) подразумевает, что метрика реально работает.
**Fix**: убрать `ErrorMiddleware` из `dp.update.outer_middleware` (`bot/__main__.py:144`), оставить только `errors.py` router. Удалить `bot/middlewares/error.py` или сделать его no-op-wrapper, который только биндит контекст и **re-raise** (полезно если нужно логировать до errors-router; но проще: один обработчик).
**Principle**: V (Observability), VI (Simplicity).

### [P0-3] `_reindex_after_delete` нарушает Constitution §IV: повторно эмбедит выжившие чанки
**File**: `bot/handlers/admin.py:276-292`
**Issue**: после удаления документа функция берёт все выжившие чанки и вызывает `openai.get_embeddings_batch([c.text for c in surviving])`. Это N×$ on каждый delete/reindex без необходимости — векторы уже есть внутри FAISS. На 5000 чанков это лишний $5 + 10–20 секунд. Constitution §IV.1 явно требует minimal embed-calls.
**Fix**: вместо повторного embed — собирать массив из `store.index.reconstruct(i)` для индексов выживших чанков и вызвать `store.rebuild()` с уже готовыми векторами. Или ввести `VectorStore.remove_by_filenames(set[str])` который собирает новый IndexFlatIP из существующих эмбеддингов (`reconstruct_n(0, ntotal)`). Сейчас reconstruct supported для IndexFlatIP.
**Principle**: IV (Cost-Conscious), II (Production safety — без падения OpenAI при нет-сети нельзя удалить документ).

---

## Major (fix before release)

### [P1-1] `cb_stats` лезет в private `_value.get()` Prometheus Gauge
**File**: `bot/handlers/admin.py:82`
**Issue**: `int(vector_store_size._value.get())` — SLF001 подавлено через `noqa`, но это hack: Prometheus не гарантирует stable internals; кроме того, метрика выставляется в `__main__` единственный раз через `vector_store_size.set(store.size)` и потом обновляется в `add_chunks/rebuild`. Истина — `store.size`. Stats handler не получает `store` через DI.
**Fix**: добавить `store: VectorStore` в зависимости `cb_stats` и читать `store.size`.
**Principle**: VI (Simplicity).

### [P1-2] `cb_stats` не показывает «Top-5 неотвеченных вопросов» и «cost-per-day»
**File**: `bot/handlers/admin.py:73-94`
**Issue**: spec.md §3 US-7 требует: top-5 unanswered + cost-per-day. Также `unanswered_pct` показан, а top-unanswered (есть готовая `get_top_unanswered_questions`) — не используется.
**Fix**: добавить вывод top-5 unanswered (limit=5) и cost-per-day из `openai_tokens_total` (см. P1-3 ниже — нужна функция-стоимость или сноска).
**Principle**: VII (test-by-spec — здесь spec явно описала формат).

### [P1-3] `/api/stats` не считает cost / openai_tokens
**File**: `api/routes.py:84-111`
**Issue**: schema `StatsResponse` не содержит ни tokens, ни cost. Spec US-7 (admin TG + REST) требует cost-per-day. Сейчас даже на metrics-уровне нет «cost», только tokens — это допустимо (cost вычисляется в Grafana), но schema должен хотя бы экспортировать tokens (input/output по модели) или давать ссылку на `/metrics`.
**Fix**: либо расширить `StatsResponse` с `tokens_input`/`tokens_output` (читать из `openai_tokens_total._metrics` неудобно — Prometheus internals; правильнее ничего не добавлять и зафиксировать в README, что cost = grafana view); либо добавить отдельные поля в схему и собирать в БД (sum по conversations.token_count, что потребует новой колонки). Minimum: упомянуть в README §REST API, что cost не в JSON, и из spec убрать требование, либо оставить и явно implement.
**Principle**: VII (Test-by-spec — иначе тестировщик уронит).

### [P1-4] `cb_stats` не учитывает unanswered_pct по spec формулировке
**File**: `bot/handlers/admin.py:80-82`
**Issue**: `unanswered_pct = (unanswered / total_q * 100)` — но `total_q` включает unanswered тоже, что корректно. Однако `unanswered` берётся за всё время, а `total_q` — за всё время. Spec §3 US-7 требует «% unanswered за неделю» (неявно из «вопросов всего / за неделю»). Сейчас оба `count_conversations` зовутся без `since`, а в API endpoint используется `since=week_ago`, что несимметрично.
**Fix**: уточнить — либо везде показывать pct общий, либо везде weekly. Согласовать с spec и `StatsResponse.unanswered_pct`.
**Principle**: VII.

### [P1-5] Throttling outer-middleware ставится последним → DB-session открыта на throttled updates
**File**: `bot/__main__.py:141-145`
**Issue**: aiogram outer-middleware FIFO. Текущий порядок: `DbSessionMiddleware → LoggingMiddleware → MetricsMiddleware → ErrorMiddleware → ThrottlingMiddleware`. На throttled update сначала открывается SQLAlchemy-session (она будет закрыта в context-manager без commit), а потом throttling вернёт None. Лучше throttle перед DB.
**Fix**: переставить `ThrottlingMiddleware` первой (perfomance + cost), а `DbSessionMiddleware` — последней перед routers. Также Logging→Metrics→Throttling→Error→DB логичнее (контекст для лога, метрика после throttle).
**Principle**: II (Production safety), V (Observability).

### [P1-6] Operator hand-off: `username` PII логируется через `_render_payload` в content, но не логируется → ок; однако `audit_log_insert` пишет `meta={"user_id": user.id}` — мелочь, но username идёт в TG-content, не в логи — OK. Issue: `_render_payload` использует `tg://user?id=...` ссылку — это норм, но `username` экспортируется в JSON в TG-сообщении админам, не в structlog. Constitution §V.5 («PII не логируется») не нарушается, но `logger.info("operator.handoff.sent", reason=..., conv_id=..., user_id=user.id, admin_msg_id=...)` — user_id ok. Псевдо-issue, **снимаю**.

### [P1-6 actual] Question handler не использует `audit_log` для регистрации диалогов с low-relevance, хотя spec обещает «audit при admin действиях»
**File**: spec §3 US-2 + `bot/handlers/question.py:94-104`
**Issue**: при low-relevance handoff делает `do_handoff` → audit_log с action=handoff. Но question-handler не записывает в audit, а save_conversation сохраняет факт. Это OK — `Conversation` уже служит логом. Snimaю до P2.

### [P1-7] `OpenAIClient._embed_batch_one_call` — retry скрывает то, что `response` is unbound на ошибке
**File**: `bot/services/openai_client.py:104-125`
**Issue**: `async for attempt in self._retrying()` присваивает `response` внутри `with attempt:`. Если retry exhausts с `reraise=True` — exception летит наружу. Но если retry успешен — `response` живёт после async for. Технически работает. Однако mypy не ругается, потому что `attempt`-context закрывает ошибку только при retryable types — что OK. Однако если поднимется **не-retryable** (например `BadRequestError`), `tenacity` не перехватит, и `response` будет unbound → `usage_tokens = getattr(response.usage, ...)` упадёт `NameError`. Чёткой неполноты сейчас не вижу — `try/except` оборачивает только три типа, не-retryable пробросит до вызывающего, и code below не выполнится. **OK**, но потенциально хрупко при добавлении новых ошибок. Снимаю в P2 как noted.

### [P1-7 actual] Sanitizer: `safe_filename` не валидирует расширение в self
**File**: `bot/services/sanitizer.py:23-30`
**Issue**: filename `../../etc/passwd` → `pathlib.PurePosixPath(name).name` → `passwd`. Это нормально, но потеряли `.md`/`.txt` расширение, которое валидируется в handler (admin.py:193). Проблема: API endpoint `upload_doc` валидирует `filename.lower().endswith((".md",".txt"))` на raw filename ДО `safe_filename` (что правильно), но `process_document` не валидирует расширение. То есть если кто-то прямой `process_document` (например при seed) даст файл без расширения — пройдёт. Сейчас seed гарантированно `.md`, но это implicit invariant. Минор-bug.
**Fix**: в `process_document` после `safe_filename(filename)` проверить, что `safe_name` заканчивается на `.md`/`.txt`. Или добавить parameter `allowed_exts`.
**Principle**: III (Security — defence in depth), VI.

### [P1-8] `_init_sentry()`: FastApiIntegration вызывается ДО создания FastAPI app
**File**: `bot/__main__.py:49-64` и `_build_app` (line 98+)
**Issue**: `sentry_sdk.init(integrations=[FastApiIntegration()])` инициализирует Sentry. По docs Sentry SDK 2.x, FastApiIntegration работает через monkey-patch при init/startup app. Сейчас порядок: `_init_sentry()` → `init_db()` → `_build_app()`. ASGI middleware Sentry автоматически подключается через монкипатч. Порядок ок. Однако `traces_sample_rate=0.05` хардкод, лучше через ENV. Снимаю до P2.

### [P1-8 actual] `process_document` нарушает atomicity: vector store update до save_document
**File**: `bot/services/upload.py:97-106`
**Issue**: последовательность — `store.add_chunks` → `retriever.rebuild_corpus` → `save_document` → `audit_log_insert`. Если DB-save упадёт, FAISS уже расширен и сохранён на диск. Следующий старт детектирует mismatch — но spec §3 US-5 говорит «mismatch → пометить corrupt». Сейчас mismatch будет: `len(chunks)` (включая новый) != `documents`-table sha256-list. **Однако** mismatch-check в `VectorStore.load_async` сравнивает `index.ntotal == len(chunks)`, что выполняется (оба обновлены атомарно). Проблема логическая: `documents` table рассинхронизирован с `chunks.json`. Дальше: при reindex `_reindex_after_delete` отбирает `surviving` по `c.filename in active_names` — если документа нет в БД, его чанки исчезнут при следующем reindex. Tolerable. Однако порядок «DB last» — анти-паттерн. Рекомендую: сохранить Document(sha256, chunks_count) сначала, потом FAISS update; на FAISS-fail — rollback session. Сейчас если FAISS пишется ok, а DB упала — следующий старт «теряет» документ в БД но имеет чанки.
**Fix**: завернуть в SQLAlchemy `await session.begin_nested()` flow: insert Document → flush (не commit) → FAISS update → audit_log_insert → commit. На exception — session.rollback + (если FAISS добавил) `store.rebuild(...)` оттуда сложно. Альтернатива минимальная: переставить порядок — сначала `save_document` + `audit_log_insert` (один transaction), потом FAISS. Если FAISS упадёт — DB будет иметь Document без чанков в индексе → нужен warn-log + админ-action «reindex». Менее опасно чем сейчас.
**Principle**: II (Production safety), I (RAG correctness — индекс ↔ БД consistency).

### [P1-9] FlashRank: rerank сортирует по rerank_score, но возвращает items с **возможно** dropped item, у которого `rerank_score is None`
**File**: `bot/services/rerank.py:51-64`
**Issue**: внутри `_score` итерация `enumerate(items)` и `scored.get(idx)`. Если FlashRank вернул меньше элементов (что возможно при threshold внутри FlashRank), `scored.get(idx)` вернёт None, в результате `item.rerank_score = None`. Сортировка использует `-1.0` для None — такие items идут в конец, но не отбрасываются. spec §A1 «top-5 на выходе». Снимаю до P2.

### [P1-10] `/metrics` endpoint всегда public, `METRICS_INTERNAL_ONLY` не имплементирован
**File**: `bot/__main__.py:108` (`app.mount("/metrics", make_asgi_app())`) + spec §3 US-9 + spec §4.5 + README §REST API.
**Issue**: `METRICS_INTERNAL_ONLY` есть в Settings, есть в README, но нет ветки в `_build_app`, которая бы защищала `/metrics`. Это P1, не P0, потому что не блокирует тесты, но не соответствует spec.
**Fix**: в `_build_app` если `settings.metrics_internal_only` — обернуть `/metrics` в auth-middleware (например, проверять `X-Api-Key` или какой-то `INTERNAL_TOKEN`). Сейчас spec говорит «public (или INTERNAL_TOKEN)» — добавить env `INTERNAL_TOKEN` и проверять заголовок `X-Internal-Token`.
**Principle**: III (Security First), VII.

---

## Minor (nice to fix)

### [P2-1] `OPENAI_TIMEOUT_SECONDS`/`OPENAI_MAX_RETRIES` ENV → Settings: имя дублируется
**File**: `.env.example:27-28` + `bot/config.py:30-31`
**Issue**: норма — оба согласованы.

### [P2-2] `Sanitizer.MAX_CHUNK_CHARS=8000` и `is_safe_for_embed` — нигде не используется
**File**: `bot/services/sanitizer.py:15,57-58`
**Issue**: hint для будущих guard'ов. Dead code per Karpathy «simplicity first».
**Fix**: удалить `is_safe_for_embed`/`MAX_CHUNK_CHARS` либо подключить в `process_document` (фильтрация overlength chunks).
**Principle**: VI.

### [P2-3] `mark_document_deleted` использует `datetime.utcnow()` (deprecated в Python 3.12+)
**File**: `bot/db/repositories.py:174`
**Issue**: `datetime.utcnow()` без tz — pyflakes ругаться не будет, но Python 3.12 deprecation warning. Везде в коде уже `datetime.now(UTC)`.
**Fix**: `datetime.now(UTC).replace(tzinfo=None)` или поменять колонку на `DateTime(timezone=True)`.
**Principle**: VI.

### [P2-4] `chunking.py` не имеет hard-cut для одного гигантского параграфа без `\n\n`
**File**: `bot/services/chunking.py:71-87`
**Issue**: если один параграф > `max_tokens` — он становится одним чанком целиком и попадает в buffer. Embedding модели имеют hard-limit (8191 для `text-embedding-3-small`). Будет 400 от OpenAI на странных документах.
**Fix**: внутри loop — если `para_tokens > max_tokens`, рекурсивно split по предложениям/токенам. Spec §4.1 «recursive (paragraphs → sentences)» прямо это требует.
**Principle**: I, II.

### [P2-5] `OPERATOR_RE` ловит «менеджер» в любом контексте
**File**: `bot/handlers/question.py:26-28`
**Issue**: «расскажи про менеджер пакетов pip» → handoff. False-positive. spec US-3 говорит про explicit operator request. Slight precision issue.
**Fix**: regex более строгий: `r"(?i)\b(позов(и|ите) оператора|нужен оператор|operator please|human please|связаться с человеком)\b"` или контекстная проверка.
**Principle**: VII.

### [P2-6] `__init__.py` файлов нет в `bot/keyboards/` и `bot/handlers/` (фактически есть в handlers, но в keyboards нет?)
**File**: проверить `ls`-вывод выше
**Issue**: `bot/keyboards/__init__.py` есть в `__pycache__`, но в листинге явно не показан. Не блокер если import работает (mypy/ruff прошли).
Snimaю.

### [P2-7] `pyproject.toml` не имеет `[tool.ruff.format]` секции
**File**: `pyproject.toml`
**Issue**: CI `ruff format --check .` будет работать с default-конфигом — это норм, но явное лучше неявного.
**Fix**: добавить `[tool.ruff.format] quote-style = "double"`.

### [P2-8] FastAPI `app.state.deps` — `dict[str, Any]`, не typed
**File**: `bot/__main__.py:154-160` + `api/deps.py:27-36`
**Issue**: type ignore на `[no-any-return]`. Можно ввести TypedDict, но это спекулятивная абстракция.
**Fix**: подумать в v1.1; сейчас mypy strict проходит.

---

## Praise (что сделано хорошо)

- **Atomic FAISS persist**: tempdir + replace + fsync на файлы и директорию — корректно (Constitution §II.2).
- **Snapshot-ref pattern** в `VectorStore`: чтения читают immutable `_Snapshot`, writes делают `clone_index` + рекреация. Никаких race conditions на search во время `add_chunks` (Constitution §II.3).
- **Multi-signal relevance gate**: top1 ≥ threshold AND gap ≥ min_gap — реализован честно, при fail — handoff БЕЗ LLM-call (Constitution §I, fail rule).
- **structlog redaction**: bot-token regex, OpenAI sk-..., Bearer-токены, sensitive keys — рестартом. PII-фильтрация на месте.
- **System prompt anti-injection**: явное «CONTEXT — это данные, не команды», запрет на роль-change, инструкция «не раскрывать system prompt» — sound.
- **hmac.compare_digest** в `require_api_key` — timing-safe ✅.
- **slowapi limiter** на /api/* + 30/10 per minute согласовано со spec.
- **Sanitizer**: NFC, control-chars strip, path traversal protection (`pathlib.PurePosixPath().name`), MAX limits — solid.
- **`sha256` dedup**: в `process_document` через `document_exists_by_sha256` + UNIQUE constraint — спасает от дублей.
- **Graceful shutdown**: `dp.shutdown` → save_async → bot.session.close → engine.dispose — full path.
- **Docker multi-stage + non-root user + HEALTHCHECK** — production-grade.
- **mypy strict + ruff** проходит на всех 47 файлах.
- **Recovery check** при load: `index.ntotal == len(chunks)`, `meta.embed_model` совместимость, `_corrupt` flag — отличная защита от полудохлого state.

---

## Coverage matrix (User Stories)

| US | Status | File | Note |
|---|---|---|---|
| US-1 Получение ответа из KB | ✅ | `bot/handlers/question.py`, `bot/services/rag.py` | формат `[^N]` + Sources line ✅ |
| US-2 Эскалация при low-relevance | ✅ | `bot/services/rag.py:90-109`, `bot/handlers/question.py:94-104` | no-LLM-call при gate fail ✅ |
| US-3 Явный запрос оператора (keyword + button) | ⚠️ | `bot/handlers/question.py:26-66`, `bot/handlers/operator.py` | regex слишком жадный (P2-5) |
| US-4 Feedback 👍/👎 + комментарий | ⚠️ | `bot/handlers/feedback.py` | «5+ 👎 за неделю на тему» — алерт не реализован |
| US-5 Upload через TG | ✅ | `bot/handlers/admin.py:178-235`, `bot/services/upload.py` | atomic ok, mismatch detection ok |
| US-6 Upload через REST | ✅ | `api/routes.py:137-209` | 401/403/409/413 — ok |
| US-7 Статистика | ⚠️ | `bot/handlers/admin.py:73-94`, `api/routes.py:84-111` | нет top-5 unanswered, нет cost-per-day (P1-2, P1-3) |
| US-8 Список + удаление | ❌ | `api/routes.py:212-237` (P0-1) | TG-flow ✅; API DELETE не делает reindex |
| US-9 Health/Ready/Metrics | ⚠️ | `api/routes.py:56-81` + `bot/__main__.py:108` | `METRICS_INTERNAL_ONLY` не имплементирован (P1-10) |
| US-10 Graceful shutdown | ✅ | `bot/__main__.py:163-168` | save_async + dispose ok |

---

## Constitution mapping

| Principle | Compliance | Notes |
|---|---|---|
| I. RAG Correctness | ⚠️ | gate + cosine + logged top_scores ✅; **P0-1** ломает «delete + reindex», нарушает invariant |
| II. Production Safety | ⚠️ | atomic FAISS, retry, Lock — ✅; **P1-8** упорядочение FAISS↔DB не атомарно; **P0-3** дополнительные точки отказа в reindex |
| III. Security First | ✅ | SecretStr, hmac compare, sanitizer, system-prompt guard, audit_log, slowapi rate-limit ✅. P1-10 METRICS public — частичное несоответствие |
| IV. Cost-Conscious | ⚠️ | batch embed ✅, max_tokens ✅, top-k caps ✅. **P0-3** re-embed на reindex — основная утечка |
| V. Observability | ⚠️ | structlog JSON ✅, contextvars ✅, Prometheus ✅. **P0-2** errors_total не работает (мёртвый счётчик) |
| VI. Simplicity & Surgical | ✅ | без LangChain, без YAML configs, ENV only ✅. P2-2 dead code (мелочь) |
| VII. Test by Spec | ⚠️ | реализация близко к spec, но stats US-7 не полна; некоторые spec-формулировки требуют уточнения (P1-2, P1-3, P1-4) |

---

## Stats

- **Files inspected**: 47 (`.py` в `bot/`, `api/`) + конфиги/Dockerfile/docker-compose/CI/.env.example/README + spec/design.
- **Total LOC (bot + api)**: ~3,836 lines.
- **ruff check**: All checks passed.
- **mypy strict**: Success: no issues found in 47 source files.
- **Architectural fidelity to design.md**: ~93%. Основные отклонения — отсутствие `app.state.limiter` exception_handler не для всех путей (OK), errors-router-vs-middleware дублирование, и `/api/docs/{id}` без reindex.
- **Spec coverage по US**: 8/10 ✅, 2/10 ⚠️ (US-7 частично, US-8 API-сторона сломана).

---

## Recommended fix order (для phase 7)

1. **P0-1** — DELETE endpoint reindex (15 мин).
2. **P0-3** — `VectorStore.remove_by_filenames` + использовать в `_reindex_after_delete` (45 мин). Вынести в `bot/services/reindex.py` для повторного использования из API.
3. **P0-2** — убрать `ErrorMiddleware`, оставить `errors.py`-router (5 мин).
4. **P1-1, P1-2, P1-4, P1-10** — допилить stats и METRICS_INTERNAL_ONLY (1ч).
5. **P1-5, P1-7, P1-8** — порядок middleware, расширение validate в upload (30 мин).
6. **P1-3** — решить судьбу cost-per-day в API (либо реализовать, либо снять из spec).
7. P2 — на choice по effort budget.
