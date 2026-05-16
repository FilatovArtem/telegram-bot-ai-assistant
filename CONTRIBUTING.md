# Contributing

Thanks for your interest in contributing!

## Development setup

```bash
git clone https://github.com/YOUR_USERNAME/telegram-bot-ai-assistant.git
cd telegram-bot-ai-assistant
cp .env.example .env
# Fill in BOT_TOKEN, OPENAI_API_KEY, ADMIN_IDS, API_ADMIN_TOKEN
uv pip install -e ".[dev]"
```

## Workflow

1. Open an issue describing the change before starting work on anything larger than a typo fix.
2. Fork the repo, create a feature branch off `main`:
   ```bash
   git checkout -b feat/short-description
   ```
3. Make your changes (see Code Style below).
4. Run the quality gates locally — all must pass:
   ```bash
   uv run ruff check . && uv run ruff format --check .
   uv run mypy bot api
   uv run pytest -v
   ```
5. Commit using [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat(scope): add hybrid retrieval`
   - `fix(rag): preserve citation order`
   - `docs: update setup guide`
   - `test`, `refactor`, `chore`, `ci` accepted.
6. Open a Pull Request against `main`. Fill in the PR template.

## Code Style

- Python 3.12, type hints required (`mypy --strict` clean).
- `ruff check` and `ruff format` clean.
- Async-first: no blocking I/O inside `async def` — wrap with `asyncio.to_thread`.
- Boundary validation only (user input, external APIs, file uploads).
- No defensive code for impossible internal states.
- Comments only when the **why** is non-obvious (hidden constraints, workarounds).
- No comments for **what** — the code already says what.
- No emoji in source code except in user-facing string constants.

## Testing

- Tests live in `tests/`.
- Use the `mock_openai` fixture from `tests/conftest.py` for any test touching OpenAI.
- Coverage targets: `bot/services/` ≥80%, `api/` ≥75%, handlers ≥60%.
- Tests are written against the spec, not the implementation.

## Architecture & decisions

This project follows a spec-driven workflow. Before substantive changes, read:

- [`.specify/memory/constitution.md`](.specify/memory/constitution.md) — project principles (non-negotiable).
- [`specs/mvp/spec.md`](specs/mvp/spec.md) — current MVP specification.
- [`specs/research/01-research-report.md`](specs/research/01-research-report.md) — background research.

If your change conflicts with the Constitution, propose an amendment (see Amendment Process in the Constitution itself).

## Reporting bugs

Open an issue using the bug report template. Include:
- Steps to reproduce
- Expected vs actual behaviour
- Logs (with secrets redacted)
- Versions: Python, OS, Docker

## Security

See [SECURITY.md](SECURITY.md) — do not file public issues for vulnerabilities.

## License

By contributing, you agree your contributions are licensed under the [Apache License 2.0](LICENSE).
