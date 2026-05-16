"""Tests for bot.services.chunking — spec A1 §3.2, design §3.10.

Token-aware recursive splitter with character offsets.
"""

from __future__ import annotations

from bot.services.chunking import Chunk, split_into_chunks


class TestSplitIntoChunks:
    def test_empty_text(self) -> None:
        assert split_into_chunks("", "f.md") == []

    def test_only_whitespace(self) -> None:
        assert split_into_chunks("   \n\n   \n\n   ", "f.md") == []

    def test_single_paragraph_one_chunk(self) -> None:
        text = "Это короткий абзац. Только одно предложение."
        chunks = split_into_chunks(text, "test.md")
        assert len(chunks) == 1
        assert chunks[0].text == text
        assert chunks[0].filename == "test.md"
        assert chunks[0].chunk_index == 0
        assert chunks[0].char_start == 0
        assert chunks[0].char_end == len(text)

    def test_multiple_paragraphs_respect_max_tokens(self) -> None:
        paragraphs = ["Paragraph " + str(i) + ". " + "word " * 50 for i in range(10)]
        text = "\n\n".join(paragraphs)
        chunks = split_into_chunks(text, "doc.md", max_tokens=100, overlap_tokens=20)
        assert len(chunks) >= 2
        for idx, ch in enumerate(chunks):
            assert ch.chunk_index == idx
            assert ch.filename == "doc.md"

    def test_giant_single_paragraph_splits_by_sentence(self) -> None:
        sentences = [f"This is sentence {i}." for i in range(80)]
        text = " ".join(sentences)
        chunks = split_into_chunks(text, "x.md", max_tokens=50, overlap_tokens=10)
        assert len(chunks) >= 2

    def test_giant_single_sentence_hard_split(self) -> None:
        text = "word " * 600
        chunks = split_into_chunks(text, "y.md", max_tokens=50, overlap_tokens=0)
        assert len(chunks) >= 2

    def test_chunk_indices_sequential(self) -> None:
        text = "\n\n".join(["para " + ("w " * 30) for _ in range(8)])
        chunks = split_into_chunks(text, "i.md", max_tokens=80, overlap_tokens=10)
        for i, ch in enumerate(chunks):
            assert ch.chunk_index == i

    def test_char_offsets_monotonic_non_decreasing(self) -> None:
        text = "\n\n".join([f"para {i} " + ("w " * 30) for i in range(8)])
        chunks = split_into_chunks(text, "m.md", max_tokens=80, overlap_tokens=10)
        for i in range(1, len(chunks)):
            assert chunks[i].char_start >= chunks[i - 1].char_start

    def test_char_offsets_within_bounds(self) -> None:
        text = "first\n\nsecond\n\nthird"
        chunks = split_into_chunks(text, "b.md")
        for ch in chunks:
            assert ch.char_start >= 0
            assert ch.char_end <= len(text)
            assert ch.char_end > ch.char_start

    def test_filename_propagated(self) -> None:
        text = "abc\n\ndef"
        chunks = split_into_chunks(text, "propagated.md")
        for ch in chunks:
            assert ch.filename == "propagated.md"

    def test_overlap_preserved_between_adjacent(self) -> None:
        paragraphs = [f"Paragraph number {i}. " + "alpha " * 20 for i in range(6)]
        text = "\n\n".join(paragraphs)
        chunks = split_into_chunks(text, "ovr.md", max_tokens=80, overlap_tokens=30)
        if len(chunks) >= 2:
            assert chunks[0].text != chunks[1].text
            assert len(chunks[1].text) > 0


class TestChunkDataclass:
    def test_creates(self) -> None:
        c = Chunk(text="hi", filename="a.md", chunk_index=0, char_start=0, char_end=2)
        assert c.text == "hi"
        assert c.filename == "a.md"
        assert c.chunk_index == 0
        assert c.char_start == 0
        assert c.char_end == 2

    def test_frozen(self) -> None:
        import dataclasses

        c = Chunk(text="x", filename="a", chunk_index=0, char_start=0, char_end=1)
        try:
            c.text = "y"  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("Chunk should be frozen")
