"""Tests for bot.services.text.split_long_message — spec A3 §10.2."""

from __future__ import annotations

import pytest

from bot.services.text import TG_MAX_LEN, split_long_message


class TestSplitLong:
    def test_empty(self) -> None:
        assert split_long_message("") == []

    def test_short_one_part(self) -> None:
        assert split_long_message("hello") == ["hello"]

    def test_at_limit_one_part(self) -> None:
        text = "a" * TG_MAX_LEN
        result = split_long_message(text)
        assert len(result) == 1
        assert result[0] == text

    def test_just_over_limit_splits(self) -> None:
        text = ("line\n" * 1500)[: TG_MAX_LEN + 10]
        result = split_long_message(text)
        assert len(result) >= 2
        for part in result:
            assert len(part) <= TG_MAX_LEN

    def test_single_long_line_hard_split(self) -> None:
        long_line = "x" * (TG_MAX_LEN * 2 + 5)
        result = split_long_message(long_line)
        assert len(result) >= 2
        for part in result:
            assert len(part) <= TG_MAX_LEN

    def test_custom_limit(self) -> None:
        result = split_long_message("aaaa\nbbbb\ncccc", limit=5)
        assert all(len(p) <= 5 for p in result)

    def test_invalid_limit_raises(self) -> None:
        with pytest.raises(ValueError):
            split_long_message("hi", limit=0)
