# 03 — Гайд администратора

После заполнения `.env` и запуска `docker compose up -d` админ может:
- управлять KB через Telegram (`/admin` меню);
- управлять KB через REST API (`POST /api/docs`, и т.д.);
- читать статистику использования и feedback.

Если ваш user ID не в `ADMIN_IDS` — `/admin` молча проигнорируется (бот не покажет факт существования админ-меню).

---

## 1. Админ-меню в Telegram

Откройте чат с ботом, отправьте `/admin`. Появится клавиатура:

```
🔧 Админ-панель

[📊 Статистика]  [📄 Документы]
[📤 Загрузить]   [🔄 Reindex]
```

### Загрузка документа

1. Нажмите `📤 Загрузить`. Бот переключится в state `UploadStates.waiting_file` и попросит прислать файл.
2. Отправьте `.md` или `.txt` (≤ 10 MB, лимит из `MAX_UPLOAD_BYTES`).
3. Бот sanitize-ит имя, разбивает на чанки (`MAX_CHUNK_TOKENS=500`, `CHUNK_OVERLAP=100`), батч-эмбеддит, добавляет в FAISS атомарно, обновляет BM25-корпус, пишет в `documents` + `audit_log`.
4. Ответ:
   ```
   ✅ Документ faq.md загружен.
   Чанков: 12
   В индексе: 47
   ```
5. Дедуп: повторная загрузка того же содержания (SHA256-совпадение) — отказ "Документ уже загружен".

Если файл некорректный:
- неподдерживаемое расширение → "Только .md и .txt принимаются".
- больше лимита → "Файл слишком большой".
- пустой после sanitize → "Документ пустой".
- слишком много чанков (`MAX_CHUNKS_PER_DOC=5000`) → "Документ слишком большой для индексации".

### Список и удаление

`📄 Документы` показывает inline-список с filename, chunk_count, uploaded_at. Кнопка `🗑` рядом с каждым — soft-delete (записывает `deleted_at`). Reindex запускается отдельной кнопкой.

### Статистика

`📊 Статистика` показывает агрегаты:
- Пользователей всего
- Вопросов за всё время / за неделю
- % unanswered
- Размер индекса (chunks)
- Top-5 неотвеченных вопросов (источник для пополнения KB)
- Feedback ratio 👍/👎

---

## 2. REST API

Все `/api/*` требуют `X-Api-Key`. Rate limit — slowapi (in-memory, per IP).

### Загрузка документа

```bash
curl -X POST \
  -H "X-Api-Key: $API_ADMIN_TOKEN" \
  -F "file=@faq.md" \
  http://localhost:8000/api/docs
```

Успех (201):
```json
{
  "document_id": 7,
  "filename": "faq.md",
  "chunks_created": 12,
  "total_index_size": 47
}
```

Ошибки:
- 400 — пустой файл или sanitization вернул пустую строку.
- 400 — расширение не `.md` / `.txt`.
- 409 — документ с этим SHA256 уже есть.
- 413 — файл больше `MAX_UPLOAD_BYTES` или чанков больше `MAX_CHUNKS_PER_DOC`.
- 429 — превышен rate limit (10/min на POST).

### Список документов

```bash
curl -H "X-Api-Key: $API_ADMIN_TOKEN" http://localhost:8000/api/docs
```

```json
{
  "documents": [
    {"id": 7, "filename": "faq.md", "chunk_count": 12, "uploaded_at": "2026-05-15T12:34:56"},
    ...
  ]
}
```

### Удаление документа

```bash
curl -X DELETE \
  -H "X-Api-Key: $API_ADMIN_TOKEN" \
  http://localhost:8000/api/docs/7
```

Возвращает 204 No Content. Soft-delete — документ помечается `deleted_at`. Полный rebuild индекса (чтобы убрать чанки удалённых документов) — кнопка `🔄 Reindex` в админ-меню или ручной вызов соответствующего admin-handler.

### Статистика

```bash
curl -H "X-Api-Key: $API_ADMIN_TOKEN" http://localhost:8000/api/stats
```

```json
{
  "users": 12,
  "questions": 87,
  "questions_last_week": 23,
  "unanswered": 14,
  "unanswered_pct": 16.1,
  "index_size": 47,
  "feedback_up": 18,
  "feedback_down": 5
}
```

### Health / Ready

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/ready
# {"status":"ok","db":true,"openai_configured":true,"index_size":47}
```

`/ready` возвращает `degraded` если БД недоступна или индекс пуст.

---

## 3. Интерпретация статистики

| Метрика | Что значит | Норма |
|---|---|---|
| `unanswered_pct` | Процент вопросов, на которые бот не ответил | 15-30% на старте, должна снижаться по мере пополнения KB |
| `feedback_up / (up+down)` | Mean feedback score | ≥ 0.75 на демо-KB |
| Top-5 unanswered | Часто задаваемые вопросы без ответа | Источник для расширения KB |
| `index_size` | Кол-во chunks в FAISS | Растёт с каждой загрузкой |

В Prometheus `/metrics` доступны time-series:
- `bot_messages_total{type=...}` — поток сообщений по типам (command / message / callback).
- `rag_pipeline_duration_seconds{stage=...}` — latency распределение.
- `rag_relevance_score` — гистограмма top-1 cosine. Если левый край распределения растёт — пора добавлять документы.
- `openai_tokens_total{model=...,type=...}` — мониторинг стоимости.
- `feedback_total{vote=up|down}` — счётчик оценок.

---

## 4. Мониторинг feedback

`Feedback` в БД связан с `Conversation` через `conversation_id`. SQL для разбора:

```sql
-- Топ-10 вопросов с 👎 за неделю
SELECT c.question, COUNT(*) as down_count, GROUP_CONCAT(c.sources) as sources
FROM feedbacks f
JOIN conversations c ON f.conversation_id = c.id
WHERE f.vote = 'down'
  AND f.created_at >= datetime('now', '-7 days')
GROUP BY c.question
ORDER BY down_count DESC
LIMIT 10;
```

Если на один и тот же вопрос пришло ≥ 5 👎 за неделю — стоит:
1. Открыть `sources` и проверить релевантность чанков.
2. Дополнить KB более точным документом.
3. Если бот ушёл в hand-off (`answered=False`) — реальный gap в KB.

---

## 5. Recovery после corruption

Если на старте видите:
```
{"event": "vector_store.corrupt", "ntotal": 100, "n_chunks": 87, "level": "error"}
```

Индекс рассинхронизирован с chunks.json (вероятно kill процесса в момент записи). Атомарный pattern (tmp + rename) защищает от этого, но если случилось:

1. `/ready` вернёт `index_size: 0`, бот отдаст "База знаний пуста".
2. Через `/admin → Reindex` запустится полный rebuild по `documents`-таблице (если соответствующий handler реализован).
3. Если reindex недоступен — загрузите исходные документы заново через `POST /api/docs`.

Документы в БД (`documents` table) — авторитетный источник. SHA256 защитит от двойной загрузки.

---

## 6. Hand-off оператору

Когда top-1 relevance < `RELEVANCE_THRESHOLD` или зазор `top1 - avg(2..5) < RELEVANCE_MIN_GAP`, бот:
1. Отвечает пользователю: "Я не нашёл точного ответа в базе знаний. Передаю ваш вопрос оператору."
2. Если `ADMIN_CHAT_ID ≠ 0` — пересылает hand-off-сообщение туда:
   ```
   🆘 Hand-off
   User: Артём (@username, ID 123456789)
   Question: Какой курс по Rust?
   Best matches (low relevance):
   - [0.42] courses.md: ...
   [Взять в работу]
   ```
3. Нажатие "Взять в работу" — пишет `audit_log`, можно ответить из админ-чата.

Чтобы временно отключить hand-off — поставить `ADMIN_CHAT_ID=0` в `.env` и `docker compose up -d`.
