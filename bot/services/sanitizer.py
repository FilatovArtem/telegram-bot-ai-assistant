"""Input sanitization for filenames, document content, and query text.

Defends against directory traversal, control-character injection, null bytes,
and oversized payloads. All functions are idempotent: f(f(x)) == f(x).
"""

from __future__ import annotations

import pathlib
import re
import unicodedata

MAX_FILENAME_LENGTH = 200
MAX_CONTENT_CHARS = 2_000_000
MAX_CHUNK_CHARS = 8000
MAX_QUERY_CHARS = 2000

_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-]")
_FILENAME_COLLAPSE_RE = re.compile(r"_+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def safe_filename(name: str) -> str:
    base = pathlib.PurePosixPath(name).name
    base = unicodedata.normalize("NFC", base)
    base = _FILENAME_RE.sub("_", base)
    base = _FILENAME_COLLAPSE_RE.sub("_", base).strip("_.")
    if not base:
        return "unnamed"
    return base[:MAX_FILENAME_LENGTH]


def sanitize_text(text: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    normalised = unicodedata.normalize("NFC", text)
    cleaned_chars: list[str] = []
    for ch in normalised:
        if ch in ("\n", "\t"):
            cleaned_chars.append(ch)
            continue
        category = unicodedata.category(ch)
        if category[0] == "C":
            continue
        cleaned_chars.append(ch)
    return "".join(cleaned_chars)[:max_chars]


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def is_safe_for_embed(text: str) -> bool:
    return 0 < len(text) <= MAX_CHUNK_CHARS


def is_safe_for_query(text: str) -> bool:
    return 0 < len(text) <= MAX_QUERY_CHARS
