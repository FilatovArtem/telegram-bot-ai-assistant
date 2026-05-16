"""Tests for the FastAPI surface — spec §4.3, A3 §8.

Uses an in-memory SQLite session pool by monkeypatching `bot.db.engine` and
real `VectorStore` + `HybridRetriever` fixtures. Mock OpenAI.
"""

from __future__ import annotations

import io
from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture()
def api_state(
    monkeypatch: pytest.MonkeyPatch,
    fresh_store: Any,
    retriever: Any,
    mock_openai: Any,
) -> Generator[dict[str, Any], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    import bot.db.engine as eng_mod  # noqa: PLC0415

    monkeypatch.setattr(eng_mod, "engine", engine)
    monkeypatch.setattr(eng_mod, "async_session", session_factory)

    import api.deps as deps_mod  # noqa: PLC0415

    monkeypatch.setattr(deps_mod, "async_session", session_factory)

    async def get_session_override() -> AsyncGenerator[Any, None]:
        async with session_factory() as s:
            yield s

    monkeypatch.setattr(deps_mod, "get_session", get_session_override)

    import asyncio  # noqa: PLC0415

    from bot.db.models import Base  # noqa: PLC0415

    async def _init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_init()) if False else None
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_init())
    finally:
        loop.close()

    yield {
        "engine": engine,
        "session_factory": session_factory,
        "store": fresh_store,
        "retriever": retriever,
        "openai": mock_openai,
    }


@pytest.fixture()
def client(api_state: dict[str, Any]) -> Generator[TestClient, None, None]:
    from api.routes import public_router, router  # noqa: PLC0415

    app = FastAPI()
    app.include_router(public_router)
    app.include_router(router)
    app.state.limiter = None
    app.state.deps = {
        "store": api_state["store"],
        "retriever": api_state["retriever"],
        "openai": api_state["openai"],
    }
    with TestClient(app) as c:
        yield c


GOOD_KEY = "dummy-admin"


class TestPublic:
    def test_health_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_returns_struct(self, client: TestClient) -> None:
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert "status" in body
        assert "db" in body
        assert "index_size" in body
        assert "openai_configured" in body


class TestStatsAuth:
    def test_no_key_returns_401(self, client: TestClient) -> None:
        response = client.get("/api/stats")
        assert response.status_code == 401

    def test_wrong_key_returns_403(self, client: TestClient) -> None:
        response = client.get("/api/stats", headers={"X-Api-Key": "wrong"})
        assert response.status_code == 403

    def test_good_key_returns_200(self, client: TestClient) -> None:
        response = client.get("/api/stats", headers={"X-Api-Key": GOOD_KEY})
        assert response.status_code == 200
        body = response.json()
        for field in ("users", "questions", "unanswered", "index_size"):
            assert field in body


class TestDocsList:
    def test_no_key_returns_401(self, client: TestClient) -> None:
        response = client.get("/api/docs")
        assert response.status_code == 401

    def test_empty_list_ok(self, client: TestClient) -> None:
        response = client.get("/api/docs", headers={"X-Api-Key": GOOD_KEY})
        assert response.status_code == 200
        assert response.json() == {"documents": []}


class TestDocsUpload:
    def test_no_auth_returns_401(self, client: TestClient) -> None:
        response = client.post(
            "/api/docs",
            files={"file": ("a.md", b"hello", "text/markdown")},
        )
        assert response.status_code == 401

    def test_wrong_ext_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/docs",
            files={"file": ("a.pdf", b"hello", "application/pdf")},
            headers={"X-Api-Key": GOOD_KEY},
        )
        assert response.status_code == 400

    def test_empty_file_returns_400(self, client: TestClient) -> None:
        response = client.post(
            "/api/docs",
            files={"file": ("a.md", b"", "text/markdown")},
            headers={"X-Api-Key": GOOD_KEY},
        )
        assert response.status_code == 400

    def test_oversize_returns_413(self, client: TestClient) -> None:
        big = io.BytesIO(b"x" * (11 * 1024 * 1024))
        response = client.post(
            "/api/docs",
            files={"file": ("big.md", big, "text/markdown")},
            headers={"X-Api-Key": GOOD_KEY},
        )
        assert response.status_code == 413

    def test_success_returns_201(self, client: TestClient) -> None:
        response = client.post(
            "/api/docs",
            files={"file": ("a.md", b"# Title\n\nBody paragraph.", "text/markdown")},
            headers={"X-Api-Key": GOOD_KEY},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["filename"] == "a.md"
        assert body["chunks_created"] >= 1
        assert body["total_index_size"] >= 1


class TestDocsDelete:
    def test_no_auth_returns_401(self, client: TestClient) -> None:
        response = client.delete("/api/docs/1")
        assert response.status_code == 401

    def test_missing_returns_404(self, client: TestClient) -> None:
        response = client.delete("/api/docs/9999", headers={"X-Api-Key": GOOD_KEY})
        assert response.status_code == 404
