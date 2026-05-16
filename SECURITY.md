# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.x     | ✅ (pre-release; MVP)  |

## Reporting a Vulnerability

If you discover a security vulnerability, **please do not open a public issue**.

Email the maintainer privately: `filatov.o.artem@gmail.com`.

Include:
1. Description of the vulnerability.
2. Steps to reproduce or a proof-of-concept.
3. Impact assessment (what an attacker can achieve).
4. Suggested fix, if any.

You should receive an acknowledgement within 72 hours. We aim to release a fix within 14 days for high-severity issues.

## Scope

In-scope:
- The bot code (`bot/`)
- The REST API (`api/`)
- Default configuration
- Docker image

Out-of-scope:
- Third-party dependencies (report upstream)
- Misconfigured deployments (ensure you follow the setup guide)
- Issues that require physical access or admin credentials to exploit

## Known security considerations

- **API admin token** (`API_ADMIN_TOKEN`) protects upload/delete endpoints. Use a strong random value (32+ bytes).
- **Bot token** (`BOT_TOKEN`) gives full bot control if leaked. Rotate immediately via `@BotFather` if compromised.
- **OpenAI key** (`OPENAI_API_KEY`) — set a usage limit in the OpenAI dashboard.
- **Prompt injection**: uploaded documents are sanitised and the system prompt instructs the model to treat context as data. Still: only allow trusted admins to upload, and review uploaded content periodically.
- **Rate limiting**: API enforces per-key limits; the bot enforces per-user throttling. Place the bot behind a reverse proxy with HTTPS in production.
- **PII**: questions and answers are logged at DEBUG level only. Do not run with `LOG_LEVEL=DEBUG` in production unless logs are kept private.
