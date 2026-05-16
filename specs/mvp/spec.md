---
status: approved
phase: specify
created: 2026-05-15
author: lead
constitution: v1.0.0
---

# Specification — Telegram Bot AI Assistant MVP

## 1. Контекст и цели

### 1.1 Проблема
Команды техподдержки тратят 70% времени на типовые вопросы, ответы на которые уже есть в документации/FAQ. Это дорого, медленно и плохо масштабируется. Клиент ждёт ответа в чате, а не лезет искать в KB.

### 1.2 Решение
Telegram-бот, использующий RAG (Retrieval-Augmented Generation):
- Загружает корпоративную KB (FAQ, документация) — поддерживает `.md` и `.txt`.
- На вопрос пользователя ищет релевантные фрагменты через **hybrid retrieval** (BM25 + dense + RRF).
- Передаёт контекст в LLM (`gpt-4o-mini`) и возвращает ответ **с цитированием источника**.
- Если релевантности недостаточно — передаёт диалог оператору с полным контекстом.
- Все взаимодействия логируются, метрики экспонируются для мониторинга качества.

### 1.3 Цели MVP
- **Деплой за <5 минут** после получения BOT_TOKEN, OPENAI_API_KEY, API_ADMIN_TOKEN: `cp .env.example .env && docker compose up -d`.
- **Готово к показу клиентам** на демо: реальные ответы, нет fail-режимов на стандартном flow.
- **Публикация на GitHub** с Apache 2.0, badges, README, CI, security policy.
- **Production-grade**: retry, atomic persistence, structlog, metrics, anti-injection.

### 1.4 Out of Scope (MVP)
PDF/DOCX парсинг, голосовые сообщения, изображения, multi-tenancy, fine-tuning, webhook mode, i18n инфраструктура, web admin UI, OAuth для админов, конкретные CRM-интеграции.

---

## 2. Целевая аудитория

- **Конечный пользователь** (TG-чат): клиент компании, спрашивает на любом языке (бот отвечает на языке вопроса).
- **Администратор** (TG + REST API): сотрудник техподдержки, загружает документы, читает статистику, эскалирует диалоги.
- **DevOps / клиентская команда** (Self-host): разворачивает у себя по `docker-compose`.
- **GitHub-аудитория**: разработчики ищут production-ready RAG-бот пример.

---

## 3. User Stories

### US-1 (User): Получение ответа из KB
**Дано**: бот развёрнут, KB загружена.
**Когда** пользователь пишет вопрос в свободной форме.
**Тогда** бот возвращает релевантный ответ из KB с цитированием источников `[^1]` и кнопками `[👍 Помог] [👎 Не помог] [👤 Оператор]`.

**Acceptance criteria**:
- Ответ показывает имена/секции source-документов.
- Время ответа ≤ 5 сек p95 при KB ≤ 100 документов.
- Бот не выдумывает информации, отсутствующей в KB.
- Ответ на языке вопроса.

### US-2 (User): Эскалация при отсутствии ответа
**Дано**: пользователь задаёт вопрос вне scope KB.
**Когда** релевантность top-1 < `RELEVANCE_THRESHOLD` (default 0.75).
**Тогда** бот отвечает: "Я не нашёл ответа в базе знаний. Передаю оператору." Сообщение с контекстом пересылается в `ADMIN_CHAT_ID`.

**Acceptance criteria**:
- Никакая часть ответа НЕ генерируется LLM при low-relevance (защита от галлюцинаций).
- Hand-off сообщение оператору включает: имя пользователя, username, user_id, текст вопроса, top-3 best chunks с low-score, timestamp.
- Inline-кнопка "Взять в работу" в hand-off сообщении.

### US-3 (User): Явный запрос оператора
**Дано**: пользователь не доволен ответом или сразу нажимает "Оператор".
**Когда** клик на кнопку `[👤 Оператор]` либо текст содержит keyword "оператор"/"человек"/"agent".
**Тогда** бот немедленно делает hand-off без RAG-pipeline.

### US-4 (User): Feedback
**Дано**: бот ответил.
**Когда** пользователь нажимает `[👍 Помог]` или `[👎 Не помог]`.
**Тогда** оценка сохраняется в БД, бот благодарит. Для `👎` опционально предлагает оставить комментарий (FSM).

**Acceptance criteria**:
- Feedback связан с `conversation_id`.
- На 5+ 👎 за неделю на ту же темa — admin получает уведомление.

### US-5 (Admin): Загрузка документа через TG
**Дано**: пользователь в `ADMIN_IDS`, вызвал `/admin → Загрузить документ`.
**Когда** отправляет файл `.md` или `.txt` (≤ 10MB).
**Тогда** бот:
1. Санитизирует имя.
2. Chunking (recursive, 500 tokens, 100 overlap).
3. Batch-embedding (один OpenAI-запрос на все чанки документа).
4. Atomic запись в FAISS (tmp + rename).
5. Сохранение записи в `documents` + `audit_log`.
6. Ответ: "Документ X загружен. Чанков: N. В индексе: M."

**Acceptance criteria**:
- Файлы > 10MB или не `.md|.txt` отклоняются с понятной ошибкой.
- При прерывании процесса между шагами 4 и 5 — следующий старт детектирует mismatch (`len(chunks) != index.ntotal`) и помечает индекс как corrupt.

### US-6 (Admin): Загрузка через REST API
**Дано**: клиент API имеет валидный `X-Api-Key`.
**Когда** `POST /api/docs` multipart с файлом.
**Тогда** аналогично US-5 через REST. Без auth → 401.

**Acceptance criteria**:
- Rate limit 10/min на ключ.
- 401 без header, 403 с invalid key.

### US-7 (Admin): Просмотр статистики
**Дано**: admin вызвал `/admin → Статистика` или `GET /api/stats`.
**Тогда** возвращается:
- Количество пользователей.
- Вопросов всего / за неделю.
- % unanswered.
- Размер индекса (chunks).
- Top-5 неотвеченных вопросов (для backlog KB).
- Cost-per-day по OpenAI (по `openai_tokens_total`).
- Feedback ratio 👍/👎.

### US-8 (Admin): Список и удаление документов
- `/admin → Документы` или `GET /api/docs` — список с filename, chunk_count, uploaded_at.
- `DELETE /api/docs/{id}` — удаление + reindex (полный rebuild индекса исключая deleted chunks).
- Удаление через TG-кнопку с подтверждением.

### US-9 (DevOps): Health checks
- `GET /health` → 200 `{status:"ok"}` если процесс жив.
- `GET /ready` → 200 если DB pingable, last successful OpenAI ping <30s, index existence ok. Иначе 503.
- `GET /metrics` → Prometheus text format.

### US-10 (DevOps): Безопасный shutdown
**Когда** процесс получает SIGTERM/SIGINT.
**Тогда** бот завершает pending updates, vector_store сохраняет state атомарно, БД закрывает соединения, FastAPI drain in-flight. Полный shutdown ≤ 10s.

---

## 4. Функциональные требования

### 4.1 RAG Pipeline

- **Chunking**: recursive (paragraphs → sentences), max 500 tokens, overlap 100 tokens (default, configurable).
- **Embeddings**: OpenAI `text-embedding-3-small` (default), batch до 100 chunks per call.
- **Vector store**: FAISS `IndexFlatIP` (cosine). Persistent на диск (`data/faiss_index/index.faiss` + `chunks.json`).
- **BM25**: in-memory через `rank_bm25` (BM25Okapi). Корпус — те же chunks. Rebuild при upload.
- **Fusion**: RRF (k=60) на rank-ах от BM25 и dense.
- **Re-ranking**: FlashRank (опц., при `RERANK_ENABLED=true`), cross-encoder, top-5 на выходе.
- **Relevance gate**: top-1 cosine ≥ `RELEVANCE_THRESHOLD` (default 0.75).
- **LLM**: `gpt-4o-mini` (default), system prompt включает anti-injection guard + citation instructions.
- **Output**: текст ответа + список source-имён + `[^N]` markers в тексте.

### 4.2 Bot Features

- `/start` — welcome с inline-keyboard (Задать вопрос, О боте, Оператор, Примеры).
- `/help` — справка.
- `/admin` — admin-меню (доступно только `ADMIN_IDS`).
- Catch-all message → RAG.
- `Document` upload в admin-флоу через FSM.
- 👍/👎 callbacks после ответа.
- Operator keyword detection (regex `(?i)operator|оператор|человек|agent`).

### 4.3 API Features

| Endpoint | Auth | Action |
|---|---|---|
| GET /health | — | liveness |
| GET /ready | — | readiness |
| GET /metrics | — (или INTERNAL_TOKEN) | Prometheus |
| GET /api/stats | X-Api-Key | aggregated stats |
| GET /api/docs | X-Api-Key | list documents |
| POST /api/docs | X-Api-Key | upload (.md, .txt, ≤10MB) |
| DELETE /api/docs/{id} | X-Api-Key | delete + reindex |

Rate limit: 30/min/key на GET, 10/min/key на POST/DELETE.

### 4.4 Data Model

```python
# bot/db/models.py

class User:
    id: BigInteger PK
    username: str | None
    full_name: str
    created_at: datetime

class Conversation:
    id: int PK
    user_id: FK User
    question: Text
    answer: Text
    sources: JSON  # ["file.md", ...]
    top_scores: JSON  # [0.81, 0.72, 0.65]
    answered: bool
    latency_ms: int
    created_at: datetime

class Document:
    id: int PK
    filename: str
    chunk_count: int
    sha256: str  # для dedup
    uploaded_by: int (admin user_id)
    uploaded_at: datetime

class Feedback:
    id: int PK
    conversation_id: FK Conversation
    vote: enum('up','down')
    comment: Text | null
    created_at: datetime

class AuditLog:
    id: int PK
    admin_id: BigInteger
    action: str  # 'upload', 'delete', 'reindex'
    target: str  # filename / doc_id
    created_at: datetime
```

### 4.5 Configuration (ENV)

Обязательные:
- `BOT_TOKEN` — токен от BotFather.
- `OPENAI_API_KEY` — API ключ.
- `ADMIN_IDS` — CSV user ID (e.g. `123,456`).
- `API_ADMIN_TOKEN` — secret для X-Api-Key.

Опциональные (с дефолтами):
- `ADMIN_CHAT_ID=0` — chat для hand-off (0 = disabled).
- `DATABASE_URL=sqlite+aiosqlite:///data/bot.db`.
- `OPENAI_MODEL=gpt-4o-mini`.
- `EMBEDDING_MODEL=text-embedding-3-small`.
- `RELEVANCE_THRESHOLD=0.75`.
- `RERANK_ENABLED=false`.
- `BM25_ENABLED=true`.
- `TOP_K_DENSE=50`.
- `TOP_K_BM25=50`.
- `TOP_K_FINAL=5`.
- `MAX_CHUNK_TOKENS=500`.
- `CHUNK_OVERLAP=100`.
- `API_PORT=8000`.
- `LOG_LEVEL=INFO`.
- `SENTRY_DSN=` (опц.).
- `METRICS_INTERNAL_ONLY=false`.

---

## 5. Нефункциональные требования

### 5.1 Performance
- p95 latency end-to-end ≤ 5s на вопрос (KB ≤ 100 документов, ≤ 5000 chunks).
- Embedding batch до 100 docs ≤ 10s.
- Search в FAISS ≤ 100ms при ntotal ≤ 100k.

### 5.2 Reliability
- OpenAI retry: до 6 попыток с `wait_random_exponential(min=1, max=60)`.
- Atomic FAISS persistence: tmp-файл + `replace()` (POSIX atomic rename).
- Graceful shutdown ≤ 10s.
- Recovery: при загрузке индекса проверка `len(chunks) == index.ntotal`. Mismatch → пометить corrupt, требовать re-index admin-командой.

### 5.3 Security
- Все секреты — `SecretStr`, redact в logger processors.
- `.dockerignore` исключает `.env`, `data/`, `__pycache__`.
- Anti-prompt-injection — система prompt + length-limit на chunk text.
- HMAC-safe comparison X-Api-Key через `hmac.compare_digest`.
- `slowapi` rate limiter на FastAPI.

### 5.4 Observability
- structlog JSON + contextvars.
- Prometheus metrics (см. constitution §Architecture).
- Sentry (опционально).
- request_id во всех логах per update.

### 5.5 Maintainability
- type hints + mypy strict.
- ruff clean.
- pytest coverage `bot/services/` ≥ 80%, `api/` ≥ 75%, handlers ≥ 60%.
- CHANGELOG.md по Keep-a-Changelog.

### 5.6 Cost
- ≤ $10/мес на 1000 запросов/день (фактически ~$5.50 по research §4.2).

---

## 6. UI / UX

### 6.1 Welcome (start)
```
🤖 AI-ассистент техподдержки

Я отвечаю на вопросы по базе знаний.
Просто напишите ваш вопрос.

[❓ Задать вопрос]  [💡 Примеры]
[ℹ️ О боте]        [👤 Оператор]
```

### 6.2 Ответ с источниками
```
Мы предлагаем три курса: Python для начинающих (12 недель, 25 000 руб),
Веб-разработка с React (16 недель, 35 000 руб),
Data Science с нуля (20 недель, 45 000 руб). [^1]

📎 Источник: [^1] sample_faq.md

[👍 Помог] [👎 Не помог] [👤 Оператор]
```

### 6.3 Не нашёл
```
🤔 Я не нашёл точного ответа в базе знаний.

Передаю ваш вопрос оператору. Среднее время ответа: 30 минут.

[👤 Связаться с оператором сейчас]
```

### 6.4 Admin меню
```
🔧 Админ-панель

[📊 Статистика]  [📄 Документы]
[📤 Загрузить]   [🔄 Reindex]
```

### 6.5 Hand-off в админ-чат
```
🆘 Hand-off

User: Артём (@username, ID 123456789)
Question: Какой курс по Rust?
Best matches (low relevance):
- [0.42] sample_faq.md: ...
- [0.38] courses.md: ...

[Взять в работу]  [Игнорировать]
```

---

## 7. Edge Cases и Error Handling

| Case | Поведение |
|---|---|
| Пустая KB (index.ntotal == 0) | "База знаний пуста. Свяжитесь с администратором." |
| OpenAI 429 после 6 retry | "Сервис временно недоступен. Попробуйте через минуту." + лог ERROR |
| OpenAI timeout | retry, после fail — graceful error |
| File > 10MB | "Файл слишком большой. Максимум 10MB." |
| File wrong type | "Поддерживаются только .md и .txt." |
| Filename injection (`../../etc/passwd`) | sanitize, save as `___etc_passwd` |
| Duplicate filename (same sha256) | "Документ уже загружен." (по sha256) |
| Concurrent uploads | asyncio.Lock — sequential persistence |
| Bot/API crash mid-write | atomic rename защищает; recovery check на старте |
| Telegram API rate limit | aiogram default retry; для send_message graceful skip |
| Длинное сообщение TG (>4096) | split на части, либо обрезать с "..." |
| Prompt injection в документе | system prompt guard, output content scan (опц.) |
| User отправляет команду в catch-all | игнорировать (начинается с `/`) |
| User отправляет фото/стикер | "Я понимаю только текст. Напишите вопрос." |

---

## 8. Метрики успеха

- **Time-to-deploy** ≤ 5 мин (after credentials).
- **Coverage seed FAQ**: на 30 golden questions — ≥ 80% answered (RAGAS faithfulness ≥ 0.7).
- **p95 latency** ≤ 5s.
- **Cost per query** ≤ $0.0003.
- **% unanswered** ≤ 30% (на golden+random mix).
- **Mean feedback score** (👍 / (👍+👎)) ≥ 0.75 на демо-KB.
- **Tests coverage** соответствует Constitution §VII.

---

## 9. Документация (deliverables)

- `README.md` — quickstart, features, architecture mermaid, cost table, badges.
- `LICENSE` — Apache 2.0.
- `CHANGELOG.md` — Keep a Changelog.
- `CONTRIBUTING.md` — dev setup, commit convention.
- `SECURITY.md` — vuln reporting.
- `docs/setup/01-tokens-and-keys.md` — пошаговая инструкция получения BOT_TOKEN, ADMIN_IDS, OPENAI_API_KEY, API_ADMIN_TOKEN; настройка ADMIN_CHAT_ID, ENV-флаги.
- `docs/setup/02-deployment.md` — docker compose, environment, troubleshooting.
- `docs/setup/03-admin-guide.md` — как админу загружать доки, читать stats.
- `.github/workflows/ci.yml` — ruff + mypy + pytest.
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.md`.
- `.github/PULL_REQUEST_TEMPLATE.md`.
- `data/knowledge/sample_faq.md` — demo seed (TechLearn school).

---

## 10. Definition of Done (MVP release)

- [ ] Все user stories US-1..US-10 покрыты кодом.
- [ ] Constitution §I–VII соблюдены (PR-checklist).
- [ ] Тесты проходят, coverage ≥ targets.
- [ ] `docker compose up --build` стартует с чистого .env (с заполненными секретами) и `/health` 200.
- [ ] Sample FAQ работает: 5 ручных Q-A демонстрирует корректные ответы и citation.
- [ ] Hand-off оператору демонстрируется в чате (`ADMIN_CHAT_ID`).
- [ ] Документация полная (README + LICENSE + setup-инструкция).
- [ ] CI настроен и зелёный.
- [ ] Локальные коммиты сделаны логически. Без push.
