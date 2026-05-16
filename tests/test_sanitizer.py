"""Tests for bot.services.sanitizer — covers filename, content, html escape,
safety predicates. Spec: A1 §3.1, A3 §6.
"""

from __future__ import annotations

import unicodedata

from bot.services.sanitizer import (
    MAX_CHUNK_CHARS,
    MAX_FILENAME_LENGTH,
    MAX_QUERY_CHARS,
    escape_html,
    is_safe_for_embed,
    is_safe_for_query,
    safe_filename,
    sanitize_text,
)


class TestSafeFilename:
    def test_basic_passthrough(self) -> None:
        assert safe_filename("file.md") == "file.md"

    def test_path_traversal_neutralized(self) -> None:
        result = safe_filename("../../etc/passwd")
        assert "/" not in result
        assert ".." not in result
        assert not result.startswith(".")

    def test_unicode_replaced(self) -> None:
        result = safe_filename("файл.md")
        for ch in result:
            assert ch.isascii()

    def test_length_truncated(self) -> None:
        result = safe_filename("a" * 500 + ".md")
        assert len(result) <= MAX_FILENAME_LENGTH

    def test_empty_returns_unnamed(self) -> None:
        assert safe_filename("") == "unnamed"

    def test_leading_dot_stripped(self) -> None:
        result = safe_filename(".env")
        assert not result.startswith(".")

    def test_control_chars_replaced(self) -> None:
        result = safe_filename("file\x00name.md")
        assert "\x00" not in result

    def test_nfc_normalisation(self) -> None:
        decomposed = "e" + "́" + ".md"
        result = safe_filename(decomposed)
        normalised = unicodedata.normalize("NFC", result)
        assert result == normalised

    def test_idempotent(self) -> None:
        names = ["file.md", "../../passwd", "", ".env", "long" * 200]
        for n in names:
            once = safe_filename(n)
            assert safe_filename(once) == once


class TestSanitizeText:
    def test_strip_null_byte(self) -> None:
        assert sanitize_text("hello\x00world") == "helloworld"

    def test_strip_control_chars(self) -> None:
        text = "abc\x01def\x07ghi"
        result = sanitize_text(text)
        assert "\x01" not in result
        assert "\x07" not in result

    def test_preserves_newline_tab(self) -> None:
        text = "line1\nline2\tcol"
        result = sanitize_text(text)
        assert "\n" in result
        assert "\t" in result

    def test_nfc_normalisation(self) -> None:
        decomposed = "é"
        composed = sanitize_text(decomposed)
        assert composed == unicodedata.normalize("NFC", composed)

    def test_length_cap(self) -> None:
        result = sanitize_text("a" * 5_000_000, max_chars=1000)
        assert len(result) <= 1000

    def test_idempotent(self) -> None:
        for src in ["hello", "a\x00b\nc", "café", ""]:
            once = sanitize_text(src)
            assert sanitize_text(once) == once

    def test_empty_returns_empty(self) -> None:
        assert sanitize_text("") == ""


class TestEscapeHtml:
    def test_basic(self) -> None:
        assert escape_html("<script>") == "&lt;script&gt;"

    def test_ampersand(self) -> None:
        assert escape_html("Tom & Jerry") == "Tom &amp; Jerry"

    def test_quotes(self) -> None:
        result = escape_html("\"hello' world")
        assert '"' not in result
        assert "'" not in result
        assert "&quot;" in result
        assert "&#x27;" in result

    def test_safe_text_unchanged(self) -> None:
        assert escape_html("hello world") == "hello world"


class TestSafetyPredicates:
    def test_is_safe_for_embed_empty(self) -> None:
        assert is_safe_for_embed("") is False

    def test_is_safe_for_embed_normal(self) -> None:
        assert is_safe_for_embed("hi") is True

    def test_is_safe_for_embed_too_long(self) -> None:
        assert is_safe_for_embed("a" * (MAX_CHUNK_CHARS + 1)) is False

    def test_is_safe_for_embed_boundary(self) -> None:
        assert is_safe_for_embed("a" * MAX_CHUNK_CHARS) is True

    def test_is_safe_for_query_empty(self) -> None:
        assert is_safe_for_query("") is False

    def test_is_safe_for_query_normal(self) -> None:
        assert is_safe_for_query("Какие у вас курсы?") is True

    def test_is_safe_for_query_too_long(self) -> None:
        assert is_safe_for_query("a" * (MAX_QUERY_CHARS + 1)) is False
