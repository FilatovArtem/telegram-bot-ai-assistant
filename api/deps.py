"""FastAPI DI factories. Per-request session via async generator; long-lived
services (vector store, retriever, OpenAI client) come from `app.state.deps`
populated by Track D in `bot/__main__.py`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.engine import async_session

if TYPE_CHECKING:
    from bot.services.embeddings import VectorStore
    from bot.services.openai_client import OpenAIClient
    from bot.services.retrieval import HybridRetriever


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


def get_store(request: Request) -> VectorStore:
    return request.app.state.deps["store"]  # type: ignore[no-any-return]


def get_retriever(request: Request) -> HybridRetriever:
    return request.app.state.deps["retriever"]  # type: ignore[no-any-return]


def get_openai(request: Request) -> OpenAIClient:
    return request.app.state.deps["openai"]  # type: ignore[no-any-return]
