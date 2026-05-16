# Human-Only Paths

Project-specific overrides поверх глобальной матрицы
[ai-human-split](~/.claude/memory/ai-human-split.md).

Файлы/директории из этого списка ВСЕГДА `owner: human` при классификации
через `/speckit.classify`, даже если по глобальным критериям получается `ai` или `pair`.

Читается агентом `task-classifier.md`.

## Human-only

- `.env` — реальные секреты (BOT_TOKEN, OPENAI_API_KEY). Только пользователь
- `data/bot.db` — production данные пользователей
- `data/faiss_index/` — production-индекс с клиентским контентом

## Force-AI (override в обратную сторону)

(нет)

## Правила

- Изменения — через коммит с обоснованием в сообщении
- Для human → ai переходов обязателен хотя бы один test-case
