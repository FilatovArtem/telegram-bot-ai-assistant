"""FAISS vector store. IndexFlatIP + normalize_L2 (cosine). Atomic persist via
tmp + Path.replace + fsync. asyncio.Lock guards writes; reads use a snapshot
ref of (index, chunks) so search never races a rebuild.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import faiss
import numpy as np
import structlog

from bot.config import settings
from bot.observability.metrics import (
    vector_store_op_duration_seconds,
    vector_store_size,
)
from bot.services.chunking import Chunk

EMBEDDING_DIM: Final[int] = settings.embedding_dim
SCHEMA_VERSION: Final[int] = 1
INDEX_FILE: Final[str] = "index.faiss"
CHUNKS_FILE: Final[str] = "chunks.json"
META_FILE: Final[str] = "meta.json"

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class _Snapshot:
    index: faiss.Index
    chunks: tuple[Chunk, ...]


@dataclass
class VectorStore:
    index_dir: Path = field(default_factory=lambda: Path("data/faiss_index"))
    _snapshot: _Snapshot = field(init=False)
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock)
    _corrupt: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self._snapshot = _Snapshot(index=_empty_index(), chunks=())

    @property
    def index(self) -> faiss.Index:
        return self._snapshot.index

    @property
    def chunks(self) -> list[Chunk]:
        return list(self._snapshot.chunks)

    @property
    def size(self) -> int:
        return int(self._snapshot.index.ntotal)

    @property
    def is_corrupt(self) -> bool:
        return self._corrupt

    @classmethod
    async def load_async(cls, index_dir: Path | None = None) -> VectorStore:
        store = cls(index_dir=index_dir or Path("data/faiss_index"))
        store.index_dir.mkdir(parents=True, exist_ok=True)
        index_path = store.index_dir / INDEX_FILE
        chunks_path = store.index_dir / CHUNKS_FILE
        meta_path = store.index_dir / META_FILE

        if not (index_path.exists() and chunks_path.exists() and meta_path.exists()):
            vector_store_size.set(0)
            return store

        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("vector_store.meta_unreadable", error=str(exc))
            store._corrupt = True
            return store

        if not _meta_compatible(meta):
            logger.warning(
                "vector_store.meta_incompatible",
                version=meta.get("version"),
                embed_model=meta.get("embed_model"),
                dim=meta.get("dim"),
            )
            store._corrupt = True
            return store

        try:
            index = await asyncio.to_thread(faiss.read_index, str(index_path))
            raw_chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            logger.warning("vector_store.load_failed", error=str(exc))
            store._corrupt = True
            return store

        chunks = tuple(_chunk_from_dict(item) for item in raw_chunks)
        if index.ntotal != len(chunks) or index.d != EMBEDDING_DIM:
            logger.error(
                "vector_store.corrupt",
                ntotal=index.ntotal,
                n_chunks=len(chunks),
                dim=index.d,
            )
            store._corrupt = True
            return store

        store._snapshot = _Snapshot(index=index, chunks=chunks)
        vector_store_size.set(index.ntotal)
        logger.info("vector_store.loaded", n_chunks=len(chunks))
        return store

    async def save_async(self) -> None:
        async with self._lock:
            await self._persist_locked()

    async def add_chunks(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
    ) -> None:
        if not chunks:
            return
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks/embeddings length mismatch: {len(chunks)} vs {len(embeddings)}",
            )
        async with self._lock:
            started = time.perf_counter()
            current = self._snapshot
            new_index = await asyncio.to_thread(_clone_index, current.index)
            arr = _to_normalized(embeddings)
            await asyncio.to_thread(new_index.add, arr)
            new_chunks = current.chunks + tuple(chunks)
            self._snapshot = _Snapshot(index=new_index, chunks=new_chunks)
            self._corrupt = False
            await self._persist_locked()
            vector_store_size.set(new_index.ntotal)
            vector_store_op_duration_seconds.labels(op="add").observe(
                time.perf_counter() - started,
            )

    async def remove_by_filename(self, filename: str) -> int:
        """Remove all chunks with given filename. Uses index.reconstruct() — no re-embedding.
        Returns number of removed chunks.
        """
        async with self._lock:
            started = time.perf_counter()
            current = self._snapshot
            removed_indices = [i for i, c in enumerate(current.chunks) if c.filename == filename]
            if not removed_indices:
                return 0

            removed_count = len(removed_indices)
            survivors = [(i, c) for i, c in enumerate(current.chunks) if c.filename != filename]
            new_index = _empty_index()
            new_chunks: tuple[Chunk, ...] = ()
            if survivors:
                survivor_vectors = await asyncio.to_thread(
                    _reconstruct_many,
                    current.index,
                    [i for i, _ in survivors],
                )
                await asyncio.to_thread(new_index.add, survivor_vectors)
                new_chunks = tuple(c for _, c in survivors)
            self._snapshot = _Snapshot(index=new_index, chunks=new_chunks)
            self._corrupt = False
            await self._persist_locked()
            vector_store_size.set(new_index.ntotal)
            vector_store_op_duration_seconds.labels(op="remove").observe(
                time.perf_counter() - started,
            )
            return removed_count

    async def rebuild(
        self,
        chunks_with_embeddings: list[tuple[Chunk, list[float]]],
    ) -> None:
        async with self._lock:
            started = time.perf_counter()
            new_index = _empty_index()
            new_chunks: tuple[Chunk, ...] = ()
            if chunks_with_embeddings:
                chunks_only = [item[0] for item in chunks_with_embeddings]
                vectors = [item[1] for item in chunks_with_embeddings]
                arr = _to_normalized(vectors)
                await asyncio.to_thread(new_index.add, arr)
                new_chunks = tuple(chunks_only)
            self._snapshot = _Snapshot(index=new_index, chunks=new_chunks)
            self._corrupt = False
            await self._persist_locked()
            vector_store_size.set(new_index.ntotal)
            vector_store_op_duration_seconds.labels(op="rebuild").observe(
                time.perf_counter() - started,
            )

    async def search(
        self,
        query_vec: list[float],
        top_k: int = 10,
    ) -> list[tuple[Chunk, float]]:
        snapshot = self._snapshot
        if snapshot.index.ntotal == 0:
            return []
        started = time.perf_counter()
        arr = _to_normalized([query_vec])
        k = min(top_k, snapshot.index.ntotal)
        distances, indices = await asyncio.to_thread(snapshot.index.search, arr, k)
        results: list[tuple[Chunk, float]] = []
        for score, idx in zip(distances[0], indices[0], strict=True):
            if idx < 0 or idx >= len(snapshot.chunks):
                continue
            results.append((snapshot.chunks[idx], float(score)))
        vector_store_op_duration_seconds.labels(op="search").observe(
            time.perf_counter() - started,
        )
        return results

    async def _persist_locked(self) -> None:
        self.index_dir.mkdir(parents=True, exist_ok=True)
        snapshot = self._snapshot
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(dir=self.index_dir) as td:
            tmp_dir = Path(td)
            tmp_index = tmp_dir / INDEX_FILE
            tmp_chunks = tmp_dir / CHUNKS_FILE
            tmp_meta = tmp_dir / META_FILE
            await asyncio.to_thread(faiss.write_index, snapshot.index, str(tmp_index))
            tmp_chunks.write_text(_serialize_chunks(snapshot.chunks), encoding="utf-8")
            tmp_meta.write_text(_serialize_meta(snapshot), encoding="utf-8")
            for path in (tmp_index, tmp_chunks, tmp_meta):
                await asyncio.to_thread(_fsync_file, path)
            for name in (INDEX_FILE, CHUNKS_FILE, META_FILE):
                await asyncio.to_thread(
                    (tmp_dir / name).replace,
                    self.index_dir / name,
                )
        await asyncio.to_thread(_fsync_dir, self.index_dir)
        vector_store_op_duration_seconds.labels(op="persist").observe(
            time.perf_counter() - started,
        )
        logger.info(
            "vector_store.persist",
            n_chunks=len(snapshot.chunks),
            ntotal=int(snapshot.index.ntotal),
        )


def _empty_index() -> faiss.IndexFlatIP:
    return faiss.IndexFlatIP(EMBEDDING_DIM)


def _clone_index(source: faiss.Index) -> faiss.Index:
    return faiss.clone_index(source)


def _reconstruct_many(
    source: faiss.Index,
    indices: list[int],
) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
    arr = np.empty((len(indices), EMBEDDING_DIM), dtype=np.float32)
    for row, idx in enumerate(indices):
        source.reconstruct(idx, arr[row])
    return arr


def _to_normalized(vectors: list[list[float]]) -> np.ndarray[tuple[int, int], np.dtype[np.float32]]:
    arr = np.asarray(vectors, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] != EMBEDDING_DIM:
        raise ValueError(
            f"expected (N, {EMBEDDING_DIM}) float32 array, got shape={arr.shape}",
        )
    faiss.normalize_L2(arr)
    return arr


def _serialize_chunks(chunks: tuple[Chunk, ...]) -> str:
    payload = [
        {
            "text": c.text,
            "filename": c.filename,
            "chunk_index": c.chunk_index,
            "char_start": c.char_start,
            "char_end": c.char_end,
        }
        for c in chunks
    ]
    return json.dumps(payload, ensure_ascii=False)


def _serialize_meta(snapshot: _Snapshot) -> str:
    return json.dumps(
        {
            "version": SCHEMA_VERSION,
            "embed_model": settings.embedding_model,
            "dim": EMBEDDING_DIM,
            "count": int(snapshot.index.ntotal),
            "saved_at": time.time(),
        },
        ensure_ascii=False,
    )


def _chunk_from_dict(item: dict[str, object]) -> Chunk:
    return Chunk(
        text=str(item["text"]),
        filename=str(item["filename"]),
        chunk_index=_as_int(item["chunk_index"]),
        char_start=_as_int(item.get("char_start", 0)),
        char_end=_as_int(item.get("char_end", 0)),
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"cannot coerce {type(value).__name__} to int")


def _meta_compatible(meta: dict[str, object]) -> bool:
    return (
        meta.get("version") == SCHEMA_VERSION
        and meta.get("embed_model") == settings.embedding_model
        and meta.get("dim") == EMBEDDING_DIM
    )


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        # Some platforms (Windows) disallow fsync on directories. Best-effort.
        pass
    finally:
        os.close(fd)


__all__ = ["EMBEDDING_DIM", "VectorStore"]
