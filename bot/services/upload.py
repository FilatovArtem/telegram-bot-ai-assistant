"""Shared document upload service. Sanitises, dedups by sha256, chunks,
batch-embeds, atomically updates the vector store, rebuilds BM25, and records
audit/document rows. Reused by Telegram admin handler and REST API."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.db.repositories import (
    audit_log_insert,
    document_exists_by_sha256,
    save_document,
)
from bot.services.chunking import split_into_chunks
from bot.services.embeddings import VectorStore
from bot.services.openai_client import OpenAIClient
from bot.services.retrieval import HybridRetriever
from bot.services.sanitizer import safe_filename, sanitize_text

logger = structlog.get_logger(__name__)


@dataclass(slots=True, frozen=True)
class ProcessResult:
    document_id: int
    filename: str
    chunks_created: int
    total_index_size: int


class UploadError(Exception):
    """Base class for upload failures."""


class EmptyDocument(UploadError):  # noqa: N818 — task spec dictates the name
    def __init__(self) -> None:
        super().__init__("Document content is empty after sanitisation.")


class DocumentAlreadyExists(UploadError):  # noqa: N818 — task spec dictates the name
    def __init__(self, document_id: int, filename: str) -> None:
        super().__init__(f"Document already exists: id={document_id} filename={filename}")
        self.document_id = document_id
        self.filename = filename


class TooManyChunks(UploadError):  # noqa: N818 — task spec dictates the name
    def __init__(self, actual: int, limit: int) -> None:
        super().__init__(f"Document produced {actual} chunks, limit is {limit}.")
        self.actual = actual
        self.limit = limit


async def process_document(
    *,
    content: bytes,
    filename: str,
    uploaded_by: int | None,
    session: AsyncSession,
    store: VectorStore,
    retriever: HybridRetriever,
    openai: OpenAIClient,
) -> ProcessResult:
    safe_name = safe_filename(filename)
    if not safe_name.lower().endswith((".md", ".txt")):
        raise UploadError("Unsupported extension")
    raw_text = content.decode("utf-8", errors="replace")
    text = sanitize_text(raw_text).strip()
    if not text:
        raise EmptyDocument

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = await document_exists_by_sha256(session, sha)
    if existing is not None:
        raise DocumentAlreadyExists(existing.id, existing.filename)

    chunks = split_into_chunks(
        text,
        safe_name,
        max_tokens=settings.max_chunk_tokens,
        overlap_tokens=settings.chunk_overlap,
    )
    if not chunks:
        raise EmptyDocument
    if len(chunks) > settings.max_chunks_per_doc:
        raise TooManyChunks(len(chunks), settings.max_chunks_per_doc)

    try:
        doc = await save_document(
            session,
            filename=safe_name,
            chunk_count=len(chunks),
            sha256=sha,
            uploaded_by=uploaded_by,
        )
        await audit_log_insert(
            session,
            admin_id=uploaded_by or 0,
            action="upload",
            target=safe_name,
            meta={"document_id": doc.id, "chunks": len(chunks)},
        )

        embeddings = await openai.get_embeddings_batch([c.text for c in chunks])
        if len(embeddings) != len(chunks):
            raise UploadError(
                f"Embedding count mismatch: chunks={len(chunks)} embeddings={len(embeddings)}",
            )

        await store.add_chunks(chunks, embeddings)
        await retriever.rebuild_corpus(store.chunks)
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "upload.done",
        document_id=doc.id,
        filename=safe_name,
        chunks=len(chunks),
        ntotal=store.size,
    )

    return ProcessResult(
        document_id=doc.id,
        filename=safe_name,
        chunks_created=len(chunks),
        total_index_size=store.size,
    )


__all__ = [
    "DocumentAlreadyExists",
    "EmptyDocument",
    "ProcessResult",
    "TooManyChunks",
    "UploadError",
    "process_document",
]
