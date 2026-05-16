# 01 — Токены и ключи

Этот файл — пошаговая инструкция по получению всех секретов, которые требует `.env`. Источники для каждого шага актуальны на май 2026.

## Что нужно собрать

| Переменная | Назначение | Где получить |
|---|---|---|
| `BOT_TOKEN` | Telegram Bot API token | @BotFather |
| `ADMIN_IDS` | Telegram user ID администраторов | @userinfobot / getUpdates |
| `ADMIN_CHAT_ID` | Chat ID группы для hand-off | getUpdates |
| `OPENAI_API_KEY` | API ключ OpenAI | platform.openai.com |
| `API_ADMIN_TOKEN` | Secret для REST API | `secrets.token_urlsafe(32)` |
| `SENTRY_DSN` (опционально) | Error tracking | sentry.io |

---

## 1. BOT_TOKEN (Telegram Bot)

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram (синяя галочка верификации обязательна).
2. Отправьте команду `/newbot`.
3. BotFather попросит **отображаемое имя** (`Name`) — это видно в чатах. Пример: `TechLearn Support`.
4. Затем нужен **username** — уникальный, заканчивается на `bot` или `Bot`. Пример: `techlearn_support_bot`.
5. После успешной регистрации BotFather пришлёт сообщение с токеном:
   ```
   Use this token to access the HTTP API:
   123456789:AABBccdd1122EEffgg3344HHiijj5566KKllmm
   ```
6. Скопируйте токен в `.env`:
   ```
   BOT_TOKEN=123456789:AABBccdd1122EEffgg3344HHiijj5566KKllmm
   ```

**Безопасность**: токен — это пароль бота. Никогда не публикуйте, не коммитьте в git. Если утёк — `/revoke` в чате с BotFather немедленно сгенерирует новый.

**Дополнительно** (можно сделать сразу):
- `/setdescription` — короткое описание бота.
- `/setabouttext` — текст для `Info`.
- `/setuserpic` — аватарка.
- `/setprivacy` — поставьте `Disable`, если бот должен видеть все сообщения в группах (нужно для `ADMIN_CHAT_ID` ниже).

Источник: [Telegram Bot tutorial — core.telegram.org](https://core.telegram.org/bots/tutorial), [BotFather guide 2026 — bot-market.com](https://bot-market.com/blog/creating-bots/botfather--create-a-bot-in-telegram-and-receive-a-token-command-analysis-and-management/en).

---

## 2. ADMIN_IDS (ваш Telegram user ID)

Бот определяет администраторов по числовому user ID. Username игнорируется намеренно — его можно сменить.

**Способ 1 (быстрый)**: откройте [@userinfobot](https://t.me/userinfobot) и отправьте любое сообщение. Бот ответит:
```
@your_username
Id: 123456789
First: Артём
```
Число после `Id:` — это ваш user ID.

**Способ 2 (через своего бота)**: после регистрации `BOT_TOKEN` отправьте боту `/start` и откройте:
```
https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
```
В JSON ищите `result[0].message.from.id`.

Запишите в `.env` — CSV, без пробелов:
```
ADMIN_IDS=123456789
# несколько админов:
ADMIN_IDS=123456789,987654321
```

---

## 3. ADMIN_CHAT_ID (группа для hand-off)

Этот ID нужен, чтобы бот пересылал нерешённые вопросы в общий чат поддержки. Если оставить `0` — hand-off в группу отключён, но бот всё равно ответит пользователю "не нашёл".

1. Создайте **приватную группу** в Telegram (или используйте существующую).
2. Добавьте вашего бота как участника, дайте права админа (минимум — отправлять сообщения).
3. Если в шаге 1 вы НЕ выключили privacy через `/setprivacy → Disable`, сделайте это сейчас (иначе бот не увидит сообщений группы).
4. Удалите бота из группы и добавьте снова (privacy-настройка кэшируется).
5. Напишите в группе любое сообщение, упомянув бота: `@your_bot ping`.
6. Откройте в браузере:
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
7. Найдите в ответе `chat.id`:
   ```json
   {
     "message": {
       "chat": {
         "id": -1001234567890,
         "type": "supergroup",
         "title": "TechLearn Support"
       }
     }
   }
   ```
8. **Включите минус**. Для супергрупп ID начинается с `-100...`:
   ```
   ADMIN_CHAT_ID=-1001234567890
   ```

Если в ответе пусто (`"result": []`) — privacy-настройка ещё не применилась. Удалите бота из группы, перезайдите в BotFather, выполните `/setprivacy → Disable`, добавьте бота обратно, отправьте сообщение в группе, повторите шаг 6.

Альтернатива: [@getidsbot](https://t.me/getidsbot) или [@myidbot](https://t.me/myidbot) — добавьте в группу, отправьте `/getgroupid`.

Источник: [Telegram Bot API — core.telegram.org/bots/api](https://core.telegram.org/bots/api), [How to retrieve Group IDs — github.com/dazeb](https://gist.github.com/dazeb/5d6587e9f778ca37345a4f4956031ec1).

---

## 4. OPENAI_API_KEY

1. Зарегистрируйтесь / войдите на [platform.openai.com](https://platform.openai.com/).
2. **Пополните баланс перед созданием ключа** — без денег на счету ключи не выпускаются. Перейдите в [Settings → Billing](https://platform.openai.com/settings/organization/billing/overview), нажмите `Add credit`, минимум $5.
3. Откройте [API Keys](https://platform.openai.com/api-keys).
4. Нажмите `Create new secret key`. Заполните:
   - **Name**: `tg-ai-assistant-prod` (произвольное, для удобства).
   - **Permissions**: достаточно `Restricted → Model capabilities: Write` для `gpt-4o-mini` и `text-embedding-3-small`. Для MVP можно `All`.
5. Скопируйте ключ **сразу** — после закрытия диалога он больше не отобразится. Формат:
   ```
   sk-proj-AbCdEfGh123456...
   ```
6. Запишите в `.env`:
   ```
   OPENAI_API_KEY=sk-proj-AbCdEfGh123456...
   ```
7. **Защита от овердрафта**: в [Settings → Limits](https://platform.openai.com/settings/organization/limits) поставьте `Monthly budget` (например, $20) и notification threshold.

При типовых ~100 запросах/день бот потратит ~$0.55/месяц на `gpt-4o-mini` + embeddings. Подробнее — таблица в [README → Стоимость](../../README.md#стоимость-openai).

Источник: [OpenAI API Quick Start 2026 — dev.to](https://dev.to/abdul_qadir/openai-api-quick-start-2026-account-api-key-and-billing-setup-9b8), [OpenAI API keys page](https://platform.openai.com/settings/organization/api-keys).

---

## 5. API_ADMIN_TOKEN (REST API secret)

Этот токен защищает `POST /api/docs`, `DELETE /api/docs/{id}`, `GET /api/stats`, `GET /api/docs` через заголовок `X-Api-Key`. Сравнение через `hmac.compare_digest` — устойчиво к timing-атакам.

Сгенерируйте локально:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Результат вида `Vx9KqM_zUjQ3J4P5LdwYi2-aHK0sR8eFm-tyOpAbCdE` запишите в `.env`:
```
API_ADMIN_TOKEN=Vx9KqM_zUjQ3J4P5LdwYi2-aHK0sR8eFm-tyOpAbCdE
```

Использование:
```bash
curl -H "X-Api-Key: $API_ADMIN_TOKEN" http://localhost:8000/api/stats
```

---

## 6. SENTRY_DSN (опционально)

Без DSN error reporting просто выключен — бот работает как обычно.

1. Создайте аккаунт на [sentry.io](https://sentry.io/) (free tier хватит).
2. `New Project` → платформа `Python` → framework `FastAPI` → имя `tg-ai-assistant`.
3. На странице setup-инструкций скопируйте DSN:
   ```
   https://abc123def456@o123456.ingest.us.sentry.io/7654321
   ```
4. Запишите в `.env`:
   ```
   SENTRY_DSN=https://abc123def456@o123456.ingest.us.sentry.io/7654321
   ```

Бот при старте инициализирует `sentry_sdk` с `FastApiIntegration` и `traces_sample_rate=0.05`. Все необработанные исключения handlers и API-роутов попадут в Sentry.

---

## 6.5 Бесплатные альтернативы OpenAI

Все эти провайдеры **OpenAI-compatible** — меняешь только `OPENAI_API_KEY`, `OPENAI_BASE_URL` и `OPENAI_MODEL` в `.env`. Код не трогается.

### Сравнение (на май 2026)

| Провайдер | Chat | Embeddings | Бесплатно | Best for |
|---|---|---|---|---|
| **Groq** | Llama 3.3 70B, gpt-oss-120b | ❌ | 30 RPM, no card | Быстрый chat |
| **Cerebras** | Llama 3.3 70B | ❌ | 1M tok/день, no card | Speed-critical |
| **OpenRouter free** | Llama 4, DeepSeek R1, Qwen3 | ❌ | 20 RPM, 50/день (1000/день с $10 balance) | Variety |
| **DeepSeek** | V4, R1 | 1024d | $5 credit стартовый, потом $0.14/1M | Цена/качество |
| **Together** | Llama, Mistral | bge-large 1024d | $5 credit | Один ключ chat+embed |
| **Ollama (self-host)** | Llama, Qwen, Mistral | nomic, bge | Бесплатно forever | Privacy, zero cost |
| **OpenAI** | gpt-4o-mini | text-embedding-3-small | $0.55/мес @ 1k req/d | Качество (reference) |

### Рекомендованная комбинация для homelab

**Groq (chat) + Ollama (embed)** — оба бесплатные навсегда:
- Chat быстрый через Groq cloud (Llama 3.3 70B), бесплатные 30 запросов/мин.
- Embeddings локально через Ollama на сервере (модель `nomic-embed-text`, 274MB, по бенчмаркам обходит `text-embedding-3-small`).
- Хватит для MVP / демо / личных проектов. Лимит Groq 30 RPM хватает на 1 пользователя бота со spam-throttle.

#### Шаги — Groq

1. [console.groq.com](https://console.groq.com) → войти через GitHub/Google (free, no card).
2. `API Keys` → `Create API Key` → имя `tg-ai-assistant`.
3. Скопировать ключ `gsk_...`.

#### Шаги — Ollama на сервере

```bash
ssh nixos-home-server

# 1. Установка (через docker — проще на NixOS без sudo)
docker volume create ollama-models
docker run -d --name ollama \
  --restart unless-stopped \
  -v ollama-models:/root/.ollama \
  -p 127.0.0.1:11434:11434 \
  ollama/ollama:latest

# 2. Скачать модель embeddings (274MB, ~10 сек)
docker exec ollama ollama pull nomic-embed-text

# 3. Проверить
curl http://127.0.0.1:11434/v1/embeddings \
  -d '{"model":"nomic-embed-text","input":"hello"}' | head -c 200
```

#### `.env` для Groq + Ollama

```env
# Chat через Groq
OPENAI_API_KEY=gsk_твой_groq_ключ
OPENAI_BASE_URL=https://api.groq.com/openai/v1
OPENAI_MODEL=llama-3.3-70b-versatile

# Embeddings через Ollama (host.docker.internal → 127.0.0.1 хоста)
EMBEDDING_API_KEY=ollama
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768
```

### Альтернатива A — Только DeepSeek ($5 credit на старте)

DeepSeek даёт **5M tokens бесплатных credit-ов** новому аккаунту. На ~30k запросов хватит. Потом $0.14/1M input — дешевле OpenAI в 50 раз. Embeddings нет публичных — придётся либо OpenAI, либо Ollama.

```env
OPENAI_API_KEY=sk-твой_deepseek_ключ
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-chat

# Embeddings — Ollama локально, как выше
EMBEDDING_API_KEY=ollama
EMBEDDING_BASE_URL=http://host.docker.internal:11434/v1
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768
```

[platform.deepseek.com](https://platform.deepseek.com/) → Sign up → API Keys → Create.

### Альтернатива B — Всё через Ollama (zero cost forever)

Запустить chat-модель локально. **Минус**: 8B-модель на CPU ~1-3 tok/sec — слишком медленно для интерактивного бота. Подходит только если есть GPU или согласен на 15+ сек ответа.

```bash
docker exec ollama ollama pull llama3.1:8b-instruct-q4_K_M
docker exec ollama ollama pull nomic-embed-text
```

```env
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://host.docker.internal:11434/v1
OPENAI_MODEL=llama3.1:8b-instruct-q4_K_M
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIM=768
```

### Что нужно учесть при смене провайдера

1. **При смене embedding model меняется `EMBEDDING_DIM`** — пересобрать индекс с нуля. Безопаснее всего удалить volume FAISS:
   ```bash
   docker compose down
   docker volume rm telegram-bot-ai-assistant_bot-data
   docker compose up -d --build
   # Документы придётся загрузить заново (TG → /admin → upload).
   ```
2. **Anti-injection system prompt** написан для GPT-семейства. На Llama 3.3 70B и DeepSeek работает не хуже. На малых моделях (7B и меньше) — может ломаться, требуется тест.
3. **RAG threshold 0.75** калиброван на cosine от OpenAI embeddings. Для nomic-embed/BGE распределение может быть другим — может понадобиться подкрутить `RELEVANCE_THRESHOLD` после первых тестов.
4. **Groq + xray proxy**: если `HTTPS_PROXY=http://host.docker.internal:10808` стоит, Groq пойдёт через него — норм (Groq доступен глобально). Если xray ломает соединение к `api.groq.com` — добавь `NO_PROXY=...,.groq.com` в `.env`.

Источники: [Groq Console](https://console.groq.com), [Ollama embedding models](https://ollama.com/blog/embedding-models), [Free AI APIs 2026 — TokenMix](https://tokenmix.ai/blog/free-llm-api), [DeepSeek API Docs](https://api-docs.deepseek.com/).

---

## Пример заполненного `.env`

```dotenv
BOT_TOKEN=123456789:AABBccdd1122EEffgg3344HHiijj5566KKllmm
ADMIN_IDS=123456789
ADMIN_CHAT_ID=-1001234567890

API_ADMIN_TOKEN=Vx9KqM_zUjQ3J4P5LdwYi2-aHK0sR8eFm-tyOpAbCdE
API_PORT=8000

OPENAI_API_KEY=sk-proj-AbCdEfGh123456789xyz
OPENAI_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small

DATABASE_URL=sqlite+aiosqlite:///data/bot.db

RELEVANCE_THRESHOLD=0.75
BM25_ENABLED=true
RERANK_ENABLED=false

LOG_LEVEL=INFO
LOG_JSON=true
SENTRY_DSN=

SEED_SAMPLE_ON_FIRST_RUN=true
```

Готово. Дальше — [02-deployment.md](02-deployment.md).
