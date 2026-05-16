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
