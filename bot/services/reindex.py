"""Reindex helpers. Drops chunks of a deleted document from the vector store
(without re-embedding) and rebuilds the BM25 corpus."""

from __future__ import annotations

import structlog

from bot.services.embeddings import VectorStore
from bot.services.retrieval import HybridRetriever

logger = structlog.get_logger(__name__)


async def reindex_after_document_delete(
    *,
    deleted_filename: str,
    store: VectorStore,
    retriever: HybridRetriever,
) -> int:
    """Drop chunks belonging to deleted_filename from store + rebuild BM25.

    Returns new index size.
    """
    removed = await store.remove_by_filename(deleted_filename)
    await retriever.rebuild_corpus(store.chunks)
    new_size = store.size
    logger.info(
        "reindex.after_delete",
        filename=deleted_filename,
        removed_chunks=removed,
        new_index_size=new_size,
    )
    return new_size


__all__ = ["reindex_after_document_delete"]
