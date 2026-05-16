# 04 — Self-host на сервере (CD через GitHub Actions)

Этот гайд описывает деплой бота на свой Linux-сервер с auto-deploy на каждый push в `main`. Пример сервера — NixOS homelab (admin user в `docker` группе, Caddy на 80/443). Подойдёт любой Linux + Docker.

## Что получится

1. Бот развёрнут как Docker-контейнер на твоём сервере.
2. Каждый push в `main` → GitHub Actions запускает CI → если CI зелёный → SSH-ит на сервер → `git pull` + `docker compose up -d --build` → health-check.
3. Секреты живут только в `.env` на сервере (chmod 600). В GitHub Secrets — только SSH-доступ.

## Один раз на сервере (bootstrap)

### Требования
- Docker + docker compose (plugin или standalone, оба ок)
- Текущий user в группе `docker` (`getent group docker` показывает твой login)
- Git
- curl
- Открытый исходящий доступ к Telegram + OpenAI (либо настроенный HTTP-proxy, см. ниже)

### Шаг 1 — Bootstrap

На сервере:

```bash
ssh nixos-home-server
bash <(curl -fsSL https://raw.githubusercontent.com/<OWNER>/telegram-bot-ai-assistant/main/deploy/scripts/bootstrap.sh)
```

Что делает скрипт:
- `git clone` в `$HOME/telegram-bot-ai-assistant`
- `cp .env.example .env` + `chmod 600`
- проверяет, что Docker daemon доступен

### Шаг 2 — Заполнить `.env`

```bash
nano ~/telegram-bot-ai-assistant/.env
```

Обязательные:
- `BOT_TOKEN=...` (см. `docs/setup/01-tokens-and-keys.md`)
- `ADMIN_IDS=...`
- `OPENAI_API_KEY=sk-proj-...`
- `API_ADMIN_TOKEN=...` (32+ байт случайных)

Опциональные на нашем NixOS-хосте (xray HTTP-proxy на `127.0.0.1:10808`):

```env
TELEGRAM_PROXY=http://host.docker.internal:10808
HTTPS_PROXY=http://host.docker.internal:10808
HTTP_PROXY=http://host.docker.internal:10808
NO_PROXY=localhost,127.0.0.1
```

`host.docker.internal` работает изнутри контейнера благодаря `extra_hosts: host-gateway` в `docker-compose.yml`.

### Шаг 3 — Первый деплой вручную

```bash
bash ~/telegram-bot-ai-assistant/deploy/scripts/deploy.sh
```

Скрипт:
- `git fetch + reset --hard origin/main`
- `docker compose up -d --build`
- ждёт `/health` до 60 секунд, прогрев показывает успех

Проверка:

```bash
curl -fsS http://127.0.0.1:8000/health
# {"status":"ok"}
docker compose -f ~/telegram-bot-ai-assistant/docker-compose.yml ps
docker compose -f ~/telegram-bot-ai-assistant/docker-compose.yml logs -f bot
```

В Telegram открой бот → `/start` → задай вопрос про курсы (sample FAQ загружается автоматически при первом запуске).

## Один раз на GitHub (для auto-deploy)

### Шаг 4 — Создать deploy SSH-ключ (на сервере)

Лучшая практика — отдельный ключ только для CI/CD, не твой личный.

```bash
# На сервере
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N "" -C "github-actions deploy"
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Содержимое приватного ключа (нужно для GitHub secret)
cat ~/.ssh/github_deploy

# Хост-ключи сервера (нужно для GitHub secret)
ssh-keyscan -t ed25519,rsa $(hostname -f) 2>/dev/null
# либо снаружи: ssh-keyscan -t ed25519,rsa <PUBLIC_IP_OR_DNS>
```

### Шаг 5 — Добавить GitHub repo secrets

`Settings → Secrets and variables → Actions → New repository secret`. Добавь:

| Secret | Значение | Пример |
|---|---|---|
| `DEPLOY_HOST` | публичный IP или DNS сервера | `95.165.71.0` |
| `DEPLOY_USER` | имя пользователя | `admin` |
| `DEPLOY_PORT` | (опционально) | `22` |
| `DEPLOY_SSH_KEY` | весь приватный ключ из шага 4 (включая `-----BEGIN/END-----`) | многострочный текст |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan` из шага 4 | многострочный текст |
| `DEPLOY_PATH` | (опционально) полный путь | `/home/admin/telegram-bot-ai-assistant` |

### Шаг 6 — Тригер первого auto-deploy

Тривиальный push в `main`:

```bash
git commit --allow-empty -m "ci: trigger first deploy"
git push origin main
```

Открой `Actions` на GitHub → дождись `CI` (lint + typecheck + test + docker build) → автоматически запустится `Deploy` workflow:
- SSH к серверу
- `bash $REPO_DIR/deploy/scripts/deploy.sh`
- smoke-check `/health`

Деплой можно запускать вручную: `Actions → Deploy → Run workflow → main`.

## Публикация API наружу (опционально)

Бот сам работает через **long-polling** — никакой inbound не нужен для работы Telegram. Но REST API (`/api/stats`, `/api/docs`, `/metrics`) по умолчанию слушает только `127.0.0.1:8000`. Чтобы он был доступен извне через HTTPS — прокинь через Caddy.

### NixOS pattern (для нашего сервера)

Открыты только `80` и `443` снаружи. nip.io DNS-wildcard уже маппит `*.95.165.71.0.nip.io → 95.165.71.0`.

1. Скопируй `deploy/caddy/tg-ai-bot.caddyfile` в `/etc/caddy/caddy_config` (либо вставь блоком в существующий config через `configuration.nix`).
2. `sudo systemctl reload caddy` (требует пароль).
3. Открой `https://tg-ai-bot.95.165.71.0.nip.io/health` — Let's Encrypt выдаст cert автоматом через HTTP-01.

```bash
curl -fsS https://tg-ai-bot.95.165.71.0.nip.io/health
curl -fsS -H "X-Api-Key: $API_ADMIN_TOKEN" https://tg-ai-bot.95.165.71.0.nip.io/api/stats
```

### Generic Caddy / nginx / Traefik

Любой reverse-proxy на `127.0.0.1:8000` подойдёт. Для metrics безопаснее ставить IP-whitelist или basic auth (см. snippet в `deploy/caddy/tg-ai-bot.caddyfile`).

## Обновления KB на проде

Если ты в `ADMIN_IDS` — просто загружай документы в TG-бот (`/admin → 📤 Загрузить`).

Альтернатива — REST API:

```bash
curl -X POST \
  -H "X-Api-Key: $API_ADMIN_TOKEN" \
  -F "file=@new-faq.md" \
  http://127.0.0.1:8000/api/docs
```

## Rollback

```bash
# На сервере
cd ~/telegram-bot-ai-assistant
git log --oneline -10
git reset --hard <previous-sha>
docker compose up -d --build
```

## Troubleshooting

| Симптом | Причина | Фикс |
|---|---|---|
| `docker compose: command not found` | старая версия Docker | `apt install docker-compose-plugin` или используй `docker-compose` |
| `permission denied while trying to connect to the Docker daemon` | user не в группе `docker` | `sudo usermod -aG docker $USER && newgrp docker` |
| Bot молчит, в логах `Connection timed out` к `api.telegram.org` | блок исходящего | прописать `TELEGRAM_PROXY=` (см. шаг 2) |
| OpenAI 403/timeout | блок region | `HTTPS_PROXY=` + `HTTP_PROXY=` |
| `/health` 200, но в TG бот не отвечает | webhook-конфликт | бот настроен на polling — убедись что у тебя НЕ установлен webhook: `curl https://api.telegram.org/bot<TOKEN>/deleteWebhook` |
| Deploy workflow падает на SSH | `SSH_KNOWN_HOSTS` устарел / неверный хост | пере-`ssh-keyscan` |
| `docker compose up` падает с `no such file: .env` | первый деплой запустился до bootstrap | выполни `deploy/scripts/bootstrap.sh` на сервере |

## Безопасность чек-лист

- [ ] `.env` chmod 600 (только владелец читает)
- [ ] Отдельный SSH-key для CI (не твой личный)
- [ ] `SSH_KNOWN_HOSTS` зафиксирован — защита от MITM при первом подключении CI runner-а
- [ ] `API_ADMIN_TOKEN` ≥32 байт случайных, не короче
- [ ] OpenAI usage limit выставлен (`$10-20/мес` на старте)
- [ ] BotFather privacy disabled только если бот ДОЛЖЕН видеть сообщения групп
- [ ] Backups: периодически копировать docker volume `bot-data` (FAISS index + SQLite)
