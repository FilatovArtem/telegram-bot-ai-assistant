# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Deployment through GitHub Actions runs manually from `main`. CI still runs automatically.

### Added
- Initial MVP release scaffolding
- RAG pipeline: BM25 + dense (FAISS) + RRF hybrid retrieval
- Cosine similarity with normalised embeddings (`IndexFlatIP`)
- Atomic FAISS persistence (tmp + rename)
- `asyncio.Lock` on vector store writes
- `asyncio.to_thread` for sync FAISS / file I/O
- Batch embeddings (one OpenAI call per document upload)
- Tenacity retry/backoff on OpenAI calls (rate limits, timeouts)
- Anti-prompt-injection system prompt
- Source citation `[^N]` in answers
- Feedback (👍/👎) with optional comment FSM
- Operator handoff with rich payload (user info + best matches)
- Admin REST API with `X-Api-Key` authentication (timing-safe compare)
- File sanitisation (filename + content)
- structlog JSON logging + contextvars binding (request_id, user_id, update_id)
- Prometheus `/metrics` endpoint
- Sentry integration (optional via `SENTRY_DSN`)
- Health (`/health`) and readiness (`/ready`) endpoints
- Audit log for admin actions
- Multi-stage Dockerfile (non-root, ≤500MB image)
- docker-compose with healthcheck + resource limits
- CI workflow (ruff + mypy + pytest)
- Apache 2.0 license
