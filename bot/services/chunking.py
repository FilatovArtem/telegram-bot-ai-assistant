"""Recursive token-aware text splitter with character offsets per chunk.

tiktoken is a hard dependency — there is no fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import tiktoken

_ENC = tiktoken.get_encoding("cl100k_base")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(slots=True, frozen=True)
class Chunk:
    text: str
    filename: str
    chunk_index: int
    char_start: int
    char_end: int


def _token_count(text: str) -> int:
    return len(_ENC.encode(text))


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    length = len(text)
    while start < length:
        end = text.find("\n\n", start)
        if end == -1:
            stripped_start = _skip_leading_ws(text, start, length)
            if stripped_start < length:
                spans.append((stripped_start, length))
            break
        stripped_start = _skip_leading_ws(text, start, end)
        if stripped_start < end:
            spans.append((stripped_start, end))
        start = end + 2
    return spans


def _skip_leading_ws(text: str, start: int, end: int) -> int:
    while start < end and text[start] in (" ", "\t", "\n", "\r"):
        start += 1
    return start


def split_into_chunks(
    text: str,
    filename: str,
    *,
    max_tokens: int = 500,
    overlap_tokens: int = 100,
) -> list[Chunk]:
    if not text:
        return []

    paragraphs = _paragraph_spans(text)
    if not paragraphs:
        return []

    chunks: list[Chunk] = []
    buffer: list[tuple[int, int, int]] = []
    buffer_tokens = 0
    chunk_index = 0

    for para_start, para_end in paragraphs:
        para_text = text[para_start:para_end]
        para_tokens = _token_count(para_text)

        units: list[tuple[int, int, int]]
        if para_tokens > max_tokens:
            units = _split_oversized(text, para_start, para_end, max_tokens)
        else:
            units = [(para_start, para_end, para_tokens)]

        for unit_start, unit_end, unit_tokens in units:
            if buffer and buffer_tokens + unit_tokens > max_tokens:
                chunk = _build_chunk(text, buffer, filename, chunk_index)
                chunks.append(chunk)
                chunk_index += 1
                buffer, buffer_tokens = _carry_overlap(buffer, overlap_tokens)

            buffer.append((unit_start, unit_end, unit_tokens))
            buffer_tokens += unit_tokens

    if buffer:
        chunks.append(_build_chunk(text, buffer, filename, chunk_index))

    return chunks


def _split_oversized(
    text: str,
    start: int,
    end: int,
    max_tokens: int,
) -> list[tuple[int, int, int]]:
    """Split a paragraph exceeding max_tokens into sentence- or token-bounded slices."""
    segments: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_SPLIT_RE.finditer(text, start, end):
        seg_end = match.start()
        if seg_end > cursor:
            segments.append((cursor, seg_end))
        cursor = match.end()
    if cursor < end:
        segments.append((cursor, end))

    result: list[tuple[int, int, int]] = []
    for seg_start, seg_end in segments:
        seg_text = text[seg_start:seg_end]
        seg_tokens = _token_count(seg_text)
        if seg_tokens <= max_tokens:
            result.append((seg_start, seg_end, seg_tokens))
            continue
        result.extend(_hard_token_split(text, seg_start, seg_end, max_tokens))
    return result


def _hard_token_split(
    text: str,
    start: int,
    end: int,
    max_tokens: int,
) -> list[tuple[int, int, int]]:
    """Force-split text by tokens when sentence splitting still leaves an oversized slice."""
    slice_text = text[start:end]
    token_ids = _ENC.encode(slice_text)
    if not token_ids:
        return []
    out: list[tuple[int, int, int]] = []
    cursor_chars = 0
    for chunk_start in range(0, len(token_ids), max_tokens):
        piece_ids = token_ids[chunk_start : chunk_start + max_tokens]
        piece_text = _ENC.decode(piece_ids)
        piece_len = len(piece_text)
        piece_char_start = start + cursor_chars
        piece_char_end = min(piece_char_start + piece_len, end)
        out.append((piece_char_start, piece_char_end, len(piece_ids)))
        cursor_chars += piece_len
    return out


def _build_chunk(
    text: str,
    buffer: list[tuple[int, int, int]],
    filename: str,
    chunk_index: int,
) -> Chunk:
    char_start = buffer[0][0]
    char_end = buffer[-1][1]
    chunk_text = "\n\n".join(text[s:e] for s, e, _ in buffer)
    return Chunk(
        text=chunk_text,
        filename=filename,
        chunk_index=chunk_index,
        char_start=char_start,
        char_end=char_end,
    )


def _carry_overlap(
    buffer: list[tuple[int, int, int]],
    overlap_tokens: int,
) -> tuple[list[tuple[int, int, int]], int]:
    if overlap_tokens <= 0:
        return [], 0
    kept: list[tuple[int, int, int]] = []
    kept_tokens = 0
    for item in reversed(buffer):
        tokens = item[2]
        if kept_tokens + tokens > overlap_tokens and kept:
            break
        kept.insert(0, item)
        kept_tokens += tokens
        if kept_tokens >= overlap_tokens:
            break
    return kept, kept_tokens
