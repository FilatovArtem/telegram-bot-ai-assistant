"""Tests for bot.services.embeddings VectorStore — spec A1 §3.4.

Uses deterministic mock embeddings via fixture `hash_to_vec`. No real OpenAI.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from bot.services.chunking import Chunk
from bot.services.embeddings import EMBEDDING_DIM, VectorStore


def _chunk(text: str, idx: int = 0, fn: str = "f.md") -> Chunk:
    return Chunk(text=text, filename=fn, chunk_index=idx, char_start=0, char_end=len(text))


class TestEmptyStore:
    async def test_fresh_size_zero(self, tmp_index_dir: Path) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        assert store.size == 0
        assert store.is_corrupt is False
        assert store.chunks == []

    async def test_search_empty_returns_empty(self, tmp_index_dir: Path) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        result = await store.search([0.1] * EMBEDDING_DIM, top_k=5)
        assert result == []


class TestAddChunks:
    async def test_add_increments_size(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"text {i}", idx=i) for i in range(3)]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)
        assert store.size == 3
        assert len(store.chunks) == 3

    async def test_add_normalises_l2(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk("only one", idx=0)]
        # Use raw (already normalised) vec
        emb = hash_to_vec("only one")
        await store.add_chunks(chunks, [emb])
        # Search same vec → cosine should be ~1
        result = await store.search(emb, top_k=1)
        assert len(result) == 1
        assert result[0][1] == pytest.approx(1.0, abs=1e-4)

    async def test_length_mismatch_raises(self, tmp_index_dir: Path) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        with pytest.raises(ValueError, match="length mismatch"):
            await store.add_chunks(
                [_chunk("a"), _chunk("b")],
                [[0.0] * EMBEDDING_DIM],
            )


class TestSearch:
    async def test_top_k_caps_results(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"t{i}", idx=i) for i in range(5)]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)
        result = await store.search(hash_to_vec("t0"), top_k=3)
        assert len(result) <= 3

    async def test_scores_in_unit_range(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"t{i}", idx=i) for i in range(5)]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)
        result = await store.search(hash_to_vec("t0"), top_k=5)
        for _, score in result:
            assert -1.0 - 1e-3 <= score <= 1.0 + 1e-3

    async def test_descending_order(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"t{i}", idx=i) for i in range(5)]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)
        result = await store.search(hash_to_vec("t2"), top_k=5)
        scores = [s for _, s in result]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1] + 1e-6


class TestPersistRoundtrip:
    async def test_save_load_preserves(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"t{i}", idx=i) for i in range(3)]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)
        original_size = store.size

        store2 = await VectorStore.load_async(index_dir=tmp_index_dir)
        assert store2.size == original_size
        assert len(store2.chunks) == len(chunks)
        for orig, loaded in zip(chunks, store2.chunks, strict=True):
            assert orig.text == loaded.text
            assert orig.filename == loaded.filename
            assert orig.chunk_index == loaded.chunk_index

    async def test_corrupt_meta_version(self, tmp_index_dir: Path) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk("a")]
        embs = [[1.0] + [0.0] * (EMBEDDING_DIM - 1)]
        await store.add_chunks(chunks, embs)

        meta_path = tmp_index_dir / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["version"] = 999
        meta_path.write_text(json.dumps(meta))

        store2 = await VectorStore.load_async(index_dir=tmp_index_dir)
        assert store2.is_corrupt is True

    async def test_corrupt_mismatch_chunks_index(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"t{i}", idx=i) for i in range(2)]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)

        chunks_path = tmp_index_dir / "chunks.json"
        existing = json.loads(chunks_path.read_text())
        existing.append(
            {
                "text": "phantom",
                "filename": "ghost.md",
                "chunk_index": 99,
                "char_start": 0,
                "char_end": 7,
            },
        )
        chunks_path.write_text(json.dumps(existing))

        store2 = await VectorStore.load_async(index_dir=tmp_index_dir)
        assert store2.is_corrupt is True


class TestConcurrentAdds:
    async def test_two_parallel_adds_no_loss(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        batch_a = [_chunk(f"a{i}", idx=i, fn="a.md") for i in range(4)]
        batch_b = [_chunk(f"b{i}", idx=i, fn="b.md") for i in range(4)]
        await asyncio.gather(
            store.add_chunks(batch_a, [hash_to_vec(c.text) for c in batch_a]),
            store.add_chunks(batch_b, [hash_to_vec(c.text) for c in batch_b]),
        )
        assert store.size == 8


class TestRemoveByFilename:
    async def test_removes_chunks_no_reembed(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk(f"a{i}", idx=i, fn="a.md") for i in range(3)] + [
            _chunk(f"b{i}", idx=i, fn="b.md") for i in range(2)
        ]
        embs = [hash_to_vec(c.text) for c in chunks]
        await store.add_chunks(chunks, embs)
        assert store.size == 5

        removed = await store.remove_by_filename("a.md")
        assert removed == 3
        assert store.size == 2
        names = {c.filename for c in store.chunks}
        assert names == {"b.md"}

    async def test_remove_unknown_returns_zero(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = [_chunk("solo", idx=0, fn="solo.md")]
        await store.add_chunks(chunks, [hash_to_vec("solo")])
        removed = await store.remove_by_filename("missing.md")
        assert removed == 0
        assert store.size == 1


class TestRebuild:
    async def test_rebuild_replaces_all(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        old = [_chunk(f"o{i}", idx=i) for i in range(5)]
        await store.add_chunks(old, [hash_to_vec(c.text) for c in old])
        assert store.size == 5

        new = [_chunk(f"n{i}", idx=i, fn="n.md") for i in range(3)]
        new_pairs = [(c, hash_to_vec(c.text)) for c in new]
        await store.rebuild(new_pairs)
        assert store.size == 3
        assert all(c.filename == "n.md" for c in store.chunks)

    async def test_rebuild_empty_clears(
        self,
        tmp_index_dir: Path,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        await store.add_chunks([_chunk("x")], [hash_to_vec("x")])
        await store.rebuild([])
        assert store.size == 0
        assert store.chunks == []


class TestNormalizeL2:
    async def test_added_vectors_normalised(
        self,
        tmp_index_dir: Path,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        raw = np.array([3.0] + [4.0] * (EMBEDDING_DIM - 1), dtype=np.float32)
        await store.add_chunks([_chunk("x")], [raw.tolist()])
        # Search with same direction → score ≈ 1
        result = await store.search(raw.tolist(), top_k=1)
        assert result[0][1] == pytest.approx(1.0, abs=1e-3)
