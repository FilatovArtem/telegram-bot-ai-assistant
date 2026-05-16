"""Public REST API. Two routers: `public_router` (health/ready/metrics-friendly)
and `router` (`/api/*` admin endpoints, all behind `X-Api-Key`).

Track D mounts both onto a FastAPI app, registers the slowapi limiter, and
populates `app.state.deps` with the long-lived services consumed via DI here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from api.deps import get_openai, get_retriever, get_session, get_store
from api.limiter import limiter
from api.schemas import (
    DocumentItem,
    DocumentsResponse,
    HealthResponse,
    ReadyResponse,
    StatsResponse,
    UploadResponse,
)
from bot.config import settings
from bot.db.repositories import (
    audit_log_insert,
    count_conversations,
    count_unanswered,
    count_users,
    get_document,
    get_documents,
    get_feedback_ratio,
    get_top_unanswered_questions,
    mark_document_deleted,
)
from bot.services.reindex import reindex_after_document_delete

if TYPE_CHECKING:
    from bot.services.embeddings import VectorStore
    from bot.services.openai_client import OpenAIClient
    from bot.services.retrieval import HybridRetriever

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api")
public_router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]
StoreDep = Annotated["VectorStore", Depends(get_store)]
RetrieverDep = Annotated["HybridRetriever", Depends(get_retriever)]
OpenAIDep = Annotated["OpenAIClient", Depends(get_openai)]


@public_router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@public_router.get("/ready", response_model=ReadyResponse)
async def ready(
    store: StoreDep,
    session: SessionDep,
) -> ReadyResponse:
    db_ok = False
    try:
        await session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("api.ready.db_failed", error=str(exc))

    openai_configured = bool(settings.openai_api_key.get_secret_value())
    index_size = int(store.index.ntotal)
    overall = "ok" if (db_ok and index_size > 0) else "degraded"
    return ReadyResponse(
        status=overall,
        db=db_ok,
        openai_configured=openai_configured,
        index_size=index_size,
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("30/minute")
async def stats(
    request: Request,
    session: SessionDep,
    store: StoreDep,
) -> StatsResponse:
    week_ago = datetime.now(UTC) - timedelta(days=7)
    total = await count_conversations(session)
    last_week = await count_conversations(session, since=week_ago)
    unanswered = await count_unanswered(session)
    users = await count_users(session)
    up, down = await get_feedback_ratio(session)
    top_pairs = await get_top_unanswered_questions(session, limit=5)
    top_unanswered = [(q[:80] + "…" if len(q) > 80 else q) for q, _ in top_pairs]
    pct = (unanswered / total * 100) if total > 0 else 0.0
    return StatsResponse(
        users=users,
        questions=total,
        questions_last_week=last_week,
        unanswered=unanswered,
        unanswered_pct=round(pct, 1),
        index_size=int(store.index.ntotal),
        feedback_up=up,
        feedback_down=down,
        top_unanswered=top_unanswered,
    )


@router.get(
    "/docs",
    response_model=DocumentsResponse,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("30/minute")
async def list_docs(
    request: Request,
    session: SessionDep,
) -> DocumentsResponse:
    docs = await get_documents(session)
    items = [
        DocumentItem(
            id=d.id,
            filename=d.filename,
            chunk_count=d.chunk_count,
            uploaded_at=d.uploaded_at.isoformat(),
        )
        for d in docs
    ]
    return DocumentsResponse(documents=items)


@router.post(
    "/docs",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("10/minute")
async def upload_doc(
    request: Request,
    file: UploadFile,
    session: SessionDep,
    store: StoreDep,
    retriever: RetrieverDep,
    openai: OpenAIDep,
) -> UploadResponse:
    filename = file.filename or ""
    if not filename or not filename.lower().endswith((".md", ".txt")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only .md and .txt files are accepted",
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file",
        )
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File too large",
        )

    from bot.services.upload import (  # noqa: PLC0415
        DocumentAlreadyExists,
        EmptyDocument,
        TooManyChunks,
        process_document,
    )

    try:
        result = await process_document(
            content=content,
            filename=filename,
            uploaded_by=None,
            session=session,
            store=store,
            retriever=retriever,
            openai=openai,
        )
    except DocumentAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Document already uploaded: document_id={exc.document_id}",
        ) from exc
    except TooManyChunks as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Too many chunks: {exc.actual} > {exc.limit}",
        ) from exc
    except EmptyDocument as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty document content",
        ) from exc

    return UploadResponse(
        document_id=result.document_id,
        filename=result.filename,
        chunks_created=result.chunks_created,
        total_index_size=result.total_index_size,
    )


@router.delete(
    "/docs/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_api_key)],
)
@limiter.limit("10/minute")
async def delete_doc(
    request: Request,
    doc_id: int,
    session: SessionDep,
    store: StoreDep,
    retriever: RetrieverDep,
) -> None:
    doc = await get_document(session, doc_id)
    if doc is None or doc.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    filename = doc.filename
    deleted = await mark_document_deleted(session, doc_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )
    new_size = await reindex_after_document_delete(
        deleted_filename=filename,
        store=store,
        retriever=retriever,
    )
    await audit_log_insert(
        session,
        admin_id=0,
        action="delete",
        target=str(doc_id),
    )
    logger.info("api.docs.deleted", doc_id=doc_id, new_index_size=new_size)
