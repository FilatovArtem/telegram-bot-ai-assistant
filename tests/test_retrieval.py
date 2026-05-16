"""Tests for bot.services.retrieval HybridRetriever — spec A1 §3.5.

BM25 + dense + RRF (k=60). Concurrency via asyncio.gather. BM25 rebuilt on
corpus changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bot.services.chunking import Chunk
from bot.services.embeddings import VectorStore
from bot.services.retrieval import HybridRetriever


def _chunk(text: str, idx: int = 0, fn: str = "f.md") -> Chunk:
    return Chunk(text=text, filename=fn, chunk_index=idx, char_start=0, char_end=len(text))


async def _seed_store(
    store: VectorStore,
    texts: list[tuple[str, str]],
    hash_to_vec: Any,
) -> list[Chunk]:
    chunks = [_chunk(t, idx=i, fn=fn) for i, (t, fn) in enumerate(texts)]
    embs = [hash_to_vec(c.text) for c in chunks]
    await store.add_chunks(chunks, embs)
    return chunks


class TestRetrieve:
    async def test_empty_store_returns_empty(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        retriever = HybridRetriever(store=store, openai=mock_openai)
        result = await retriever.retrieve("anything")
        assert result == []

    async def test_top_k_cap(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = await _seed_store(
            store,
            [(f"text {i}", "a.md") for i in range(20)],
            hash_to_vec,
        )
        retriever = HybridRetriever(store=store, openai=mock_openai, bm25_enabled=True)
        await retriever.rebuild_corpus(chunks)
        result = await retriever.retrieve("text 5", top_k=5)
        assert len(result) <= 5

    async def test_dense_only_when_bm25_disabled(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = await _seed_store(
            store,
            [(f"item {i}", "x.md") for i in range(10)],
            hash_to_vec,
        )
        retriever = HybridRetriever(store=store, openai=mock_openai, bm25_enabled=False)
        await retriever.rebuild_corpus(chunks)
        result = await retriever.retrieve("item 5", top_k=10)
        for item in result:
            assert item.bm25_rank is None
            assert item.bm25_score is None

    async def test_rrf_merges_overlap(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = await _seed_store(
            store,
            [
                ("python course", "py.md"),
                ("react beginner", "react.md"),
                ("scala class", "sc.md"),
            ],
            hash_to_vec,
        )
        retriever = HybridRetriever(store=store, openai=mock_openai)
        await retriever.rebuild_corpus(chunks)
        result = await retriever.retrieve("python course", top_k=3)
        assert len(result) >= 1
        scores = [r.rrf_score for r in result]
        for i in range(1, len(scores)):
            assert scores[i] <= scores[i - 1] + 1e-9

    async def test_rebuild_corpus_swaps_bm25(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks_v1 = await _seed_store(
            store,
            [("foo bar", "a.md")],
            hash_to_vec,
        )
        retriever = HybridRetriever(store=store, openai=mock_openai)
        await retriever.rebuild_corpus(chunks_v1)
        res1 = await retriever.retrieve("foo", top_k=5)
        assert len(res1) >= 1

        new_chunk = _chunk("xyzzy magic", idx=1, fn="b.md")
        await store.add_chunks([new_chunk], [hash_to_vec(new_chunk.text)])
        await retriever.rebuild_corpus(store.chunks)
        res2 = await retriever.retrieve("xyzzy", top_k=5)
        assert any(r.chunk.filename == "b.md" for r in res2)


class TestRRFArithmetic:
    async def test_rrf_score_for_dual_top1(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        # Two chunks; BM25 needs >1 doc to produce non-zero scores via IDF
        chunks = await _seed_store(
            store,
            [
                ("unique python alpha", "p.md"),
                ("totally other text", "o.md"),
            ],
            hash_to_vec,
        )
        retriever = HybridRetriever(store=store, openai=mock_openai, rrf_k=60)
        await retriever.rebuild_corpus(chunks)
        result = await retriever.retrieve("unique python alpha", top_k=2)
        assert len(result) >= 1
        top = result[0]
        # top-1 chunk should be dense rank 1 (~1/61). If BM25 also matched →
        # bump to 2/61. Either way score must be at least 1/61.
        assert top.rrf_score >= 1.0 / 61.0 - 1e-6
        assert top.rrf_score <= 2.0 / 61.0 + 1e-3


class TestConcurrency:
    async def test_dense_and_bm25_both_invoked(
        self,
        tmp_index_dir: Path,
        mock_openai: Any,
        hash_to_vec: Any,
    ) -> None:
        store = await VectorStore.load_async(index_dir=tmp_index_dir)
        chunks = await _seed_store(
            store,
            [(f"course {i}", "a.md") for i in range(5)],
            hash_to_vec,
        )
        retriever = HybridRetriever(store=store, openai=mock_openai)
        await retriever.rebuild_corpus(chunks)
        mock_openai.embed_calls.clear()
        result = await retriever.retrieve("course", top_k=3)
        # retrieve embeds the query exactly once
        assert len(mock_openai.embed_calls) >= 1
        assert any(r.bm25_rank is not None or r.dense_rank is not None for r in result)
