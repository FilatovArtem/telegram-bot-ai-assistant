"""X-Api-Key dependency. Constant-time comparison via hmac.compare_digest."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from bot.config import settings


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-Api-Key"),
) -> None:
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Api-Key",
        )
    expected = settings.api_admin_token.get_secret_value()
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Api-Key",
        )
