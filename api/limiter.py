"""slowapi limiter. App wires `app.state.limiter` and exception handler in Track D."""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
