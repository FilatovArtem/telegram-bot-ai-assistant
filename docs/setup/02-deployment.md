# 02 — Деплой

Базовый сценарий — `docker compose up -d` на хосте (Linux VPS, ноутбук, локалка). Бот в long-polling, поэтому публичный HTTPS не нужен для самого Telegram. Reverse proxy нужен только если хотите выставить REST API наружу.

## Предварительные требования

- Docker 24+ и Docker Compose plugin (`docker compose ...`, не legacy `docker-compose`).
- Файл `.env` заполнен по [01-tokens-and-keys.md](01-tokens-and-keys.md).
- 1 vCPU, 1 GB RAM минимум. `data/` volume для индекса и БД (~100 MB при стандартной KB).

## Локальный запуск

```bash
git clone https://github.com/FilatovArtem/telegram-bot-ai-assistant.git
cd telegram-bot-ai-assistant
cp .env.example .env
# Откройте .env в редакторе, заполните секреты

docker compose up -d --build
docker compose logs -f bot
```

При первом старте если `SEED_SAMPLE_ON_FIRST_RUN=true` бот индексирует `data/knowledge/sample_faq.md` и пишет в логи:
```
{"event": "bot.seeded", "filename": "sample_faq.md", "chunks": 8, ...}
{"event": "bot.starting", "model": "gpt-4o-mini", "port": 8000, "index_size": 8}
```

## Verify

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl http://localhost:8000/ready
# {"status":"ok","db":true,"openai_configured":true,"index_size":8}

curl -H "X-Api-Key: $API_ADMIN_TOKEN" http://localhost:8000/api/stats
# {"users":0,"questions":0,"questions_last_week":0,"unanswered":0,...}

curl http://localhost:8000/metrics | head -20
# # HELP bot_messages_total Telegram events processed by type
# # TYPE bot_messages_total counter
# ...
```

Откройте бота в Telegram, нажмите `/start`. Если ответил — pipeline жив.

## Логи и мониторинг

```bash
docker compose logs -f bot           # live tail
docker compose logs --tail=200 bot   # последние 200 строк
docker compose ps                    # статус, healthcheck
docker stats tg-ai-bot               # CPU/RAM
```

`/metrics` отдаёт Prometheus в text-формате. Если есть Prometheus + Grafana — scrape `http://<host>:8000/metrics`, импортируйте dashboard с метриками `bot_messages_total`, `rag_pipeline_duration_seconds`, `rag_relevance_score`, `openai_tokens_total`.

## VPS deployment с Caddy

Caddy умеет автоматически выписывать Let's Encrypt-сертификаты. Минимальный `Caddyfile` рядом с `docker-compose.yml`:

```caddyfile
api.example.com {
    reverse_proxy localhost:8000
}
```

Запуск:
```bash
docker run -d --name caddy --network host \
  -v $PWD/Caddyfile:/etc/caddy/Caddyfile \
  -v caddy_data:/data \
  caddy:2
```

После запуска Caddy сертификат выпишется автоматически. Проверьте:
```bash
curl https://api.example.com/health
```

Альтернативы: Traefik, nginx + certbot. Для Telegram polling это не критично — TG ходит к OpenAI и Telegram API сам, наружу слушается только REST API на `:8000`.

## Обновление

```bash
git pull
docker compose up -d --build
```

`data/` volume переживает rebuild — индекс и БД сохраняются. Если меняли `EMBEDDING_MODEL` или схему `meta.json` индекса — стартовый recovery пометит индекс corrupt, нужно будет перезагрузить документы.

## Troubleshooting

### `/ready` отвечает `{"status":"degraded","index_size":0}`
Индекс пуст. Загрузите хотя бы один документ через `/admin → Загрузить документ` или REST API:
```bash
curl -X POST -H "X-Api-Key: $API_ADMIN_TOKEN" \
  -F "file=@faq.md" http://localhost:8000/api/docs
```

### `401 Unauthorized` / `403 Forbidden` от REST API
- 401 — заголовок `X-Api-Key` отсутствует.
- 403 — токен не совпадает с `API_ADMIN_TOKEN` из `.env`.
Проверьте `docker compose exec bot env | grep API_ADMIN_TOKEN`.

### `429 Too Many Requests` в логах от OpenAI
Tenacity retry (6 попыток с экспоненциальным backoff до 60s) уже сработал. Это значит ваш OpenAI-tier (вероятно Tier 1) уперся в RPM. Решения:
- Подождать минуту (rate limit window).
- Поднять tier (`platform.openai.com → Settings → Limits`, требует депозита).
- Снизить нагрузку: понизить `TOP_K_FINAL` или временно отключить bm25 (даёт меньше параллельных embed-вызовов).

### Бот не отвечает в Telegram
1. `docker compose logs bot | grep -i error` — есть исключения?
2. Проверьте `BOT_TOKEN` — корректный ли:
   ```bash
   curl https://api.telegram.org/bot$BOT_TOKEN/getMe
   ```
   Должен вернуть `{"ok":true,"result":{...}}`.
3. Если бот только что добавлен в группу — `/setprivacy → Disable` в BotFather, переподключите бота.
4. `dp.start_polling` падает на network errors → проверьте egress до `api.telegram.org`.

### Контейнер падает с `pydantic.ValidationError` при старте
Не заполнен один из обязательных секретов в `.env`. Сообщение покажет какое поле:
```
1 validation error for Settings
bot_token
  Field required [type=missing, input_value={'admin_ids':...}, input_type=dict]
```
Заполните и `docker compose up -d`.

### Vector index corrupt после грязного shutdown
Бот при загрузке проверяет `meta.json` + `len(chunks) == index.ntotal`. Mismatch → флаг `_corrupt=True`, новые запросы вернут "база знаний пуста". Чините через `/admin → Reindex` (если есть Source-документы в БД) или перезагрузите файлы заново.

### Healthcheck Docker фейлит
```bash
docker compose ps   # увидите unhealthy
docker compose exec bot python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').status)"
```
Если уходит в timeout — uvicorn не стартанул. Проверьте `docker compose logs bot`.

## Резервные копии

В контейнере под `/app/data` лежат:
- `bot.db` — SQLite (Conversation/User/Document/Feedback/AuditLog)
- `faiss_index/` — FAISS index + chunks.json + meta.json

Volume `bot-data` сохраняется между рестартами. Для backup:
```bash
docker run --rm -v bot-data:/data -v $PWD:/backup alpine \
  tar czf /backup/bot-data-$(date +%F).tar.gz -C /data .
```

Restore:
```bash
docker compose down
docker run --rm -v bot-data:/data -v $PWD:/backup alpine \
  tar xzf /backup/bot-data-2026-05-15.tar.gz -C /data
docker compose up -d
```
