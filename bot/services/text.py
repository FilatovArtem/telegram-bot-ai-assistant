"""Text utilities: split long messages into Telegram-sized chunks."""

from __future__ import annotations

TG_MAX_LEN = 4096


def split_long_message(text: str, *, limit: int = TG_MAX_LEN) -> list[str]:
    """Split text into chunks fitting Telegram limit, preserving lines.

    Greedy newline-aware split. If a single line exceeds the limit, it is
    hard-cut at the boundary."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for line in text.split("\n"):
        line_with_nl_len = len(line) + (1 if buf else 0)
        if len(line) > limit:
            if buf:
                parts.append("\n".join(buf))
                buf, buf_len = [], 0
            for start in range(0, len(line), limit):
                parts.append(line[start : start + limit])
            continue

        if buf_len + line_with_nl_len > limit:
            parts.append("\n".join(buf))
            buf = [line]
            buf_len = len(line)
        else:
            buf.append(line)
            buf_len += line_with_nl_len

    if buf:
        parts.append("\n".join(buf))
    return parts


__all__ = ["TG_MAX_LEN", "split_long_message"]
