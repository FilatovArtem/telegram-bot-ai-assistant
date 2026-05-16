"""Shared fixtures: deterministic mock OpenAI, in-memory DB, tmp index dir,
FastAPI test client, MockedBot.

The mock OpenAI returns hash-derived embeddings so the same text always maps to
the same vector. Tests never hit network or disk-bound state outside tmp.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import pytest_asyncio


def _required_env() -> None:
    os.environ.setdefault("BOT_TOKEN", "dummy")
    os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
    os.environ.setdefault("API_ADMIN_TOKEN", "dummy-admin")
    os.environ.setdefault("ADMIN_IDS", "1")
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


_required_env()


EMBEDDING_DIM = 1536


def _hash_to_vec(text: str) -> list[float]:
    """Deterministic embedding: hash -> 8 first floats, rest zeros, L2-normalised."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    arr = np.zeros(EMBEDDING_DIM, dtype=np.float32)
    for i in range(min(8, len(digest))):
        arr[i] = digest[i] / 255.0
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr /= norm
    return arr.tolist()


class FakeChatResult:
    def __init__(self, text: str) -> None:
        from bot.services.openai_client import ChatResult  # noqa: PLC0415

        self.text = text
        self._chat_result = ChatResult(
            text=text,
            prompt_tokens=10,
            completion_tokens=20,
            model="gpt-4o-mini-mock",
        )


class MockOpenAI:
    """Drop-in replacement for OpenAIClient. Records every call."""

    def __init__(self) -> None:
        self.embed_calls: list[list[str]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.chat_response_text: str | None = None
        self.embed_side_effect: Exception | None = None
        self.chat_side_effect: Exception | None = None

    async def get_embedding(self, text: str) -> list[float]:
        result = await self.get_embeddings_batch([text])
        return result[0]

    async def get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        if self.embed_side_effect is not None:
            exc = self.embed_side_effect
            self.embed_side_effect = None
            raise exc
        self.embed_calls.append(list(texts))
        return [_hash_to_vec(t) for t in texts]

    async def chat(
        self,
        *,
        question: str,
        context_block: str,
        system_prompt: str | None = None,
        cache_key: str | None = None,
    ) -> Any:
        if self.chat_side_effect is not None:
            exc = self.chat_side_effect
            self.chat_side_effect = None
            raise exc
        self.chat_calls.append(
            {
                "question": question,
                "context_block": context_block,
                "system_prompt": system_prompt,
                "cache_key": cache_key,
            },
        )
        text = self.chat_response_text or (f"MOCK_ANSWER: {question[:20]} ctx_len={len(context_block)} [^1]")
        from bot.services.openai_client import ChatResult  # noqa: PLC0415

        return ChatResult(
            text=text,
            prompt_tokens=10,
            completion_tokens=20,
            model="gpt-4o-mini-mock",
        )


@pytest.fixture()
def mock_openai() -> MockOpenAI:
    return MockOpenAI()


@pytest.fixture()
def hash_to_vec() -> Any:
    return _hash_to_vec


@pytest.fixture()
def tmp_index_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "faiss_index"
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("INDEX_DIR", str(target))
    return target


@pytest_asyncio.fixture()
async def in_memory_db() -> AsyncGenerator[Any, None]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415

    from bot.db.models import Base  # noqa: PLC0415

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture()
async def fresh_store(tmp_index_dir: Path) -> Any:
    from bot.services.embeddings import VectorStore  # noqa: PLC0415

    store = await VectorStore.load_async(index_dir=tmp_index_dir)
    return store


@pytest_asyncio.fixture()
async def retriever(
    fresh_store: Any,
    mock_openai: MockOpenAI,
) -> Any:
    from bot.services.retrieval import HybridRetriever  # noqa: PLC0415

    return HybridRetriever(store=fresh_store, openai=mock_openai)  # type: ignore[arg-type]


@pytest.fixture()
def app_with_deps(
    mock_openai: MockOpenAI,
    fresh_store: Any,
    retriever: Any,
) -> Any:
    from fastapi import FastAPI  # noqa: PLC0415

    from api.routes import public_router, router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(public_router)
    app.include_router(router)
    app.state.deps = {
        "store": fresh_store,
        "retriever": retriever,
        "openai": mock_openai,
    }
    return app


@pytest.fixture()
def test_client(app_with_deps: Any) -> Generator[Any, None, None]:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app_with_deps) as client:
        yield client
