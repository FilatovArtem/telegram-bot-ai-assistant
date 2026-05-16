"""Smoke tests: importability checks and minimal app sanity."""

from __future__ import annotations


def test_import_bot_main() -> None:
    import bot  # noqa: PLC0415

    assert bot is not None


def test_import_api_router() -> None:
    from api.routes import public_router, router  # noqa: PLC0415

    assert public_router is not None
    assert router is not None


def test_import_handlers_setup() -> None:
    from bot.handlers import setup_routers  # noqa: PLC0415

    routers = setup_routers()
    assert routers is not None


def test_import_services() -> None:
    from bot.services.chunking import split_into_chunks  # noqa: PLC0415
    from bot.services.embeddings import VectorStore  # noqa: PLC0415
    from bot.services.openai_client import OpenAIClient  # noqa: PLC0415
    from bot.services.rag import ask  # noqa: PLC0415
    from bot.services.rerank import NoOpReranker  # noqa: PLC0415
    from bot.services.retrieval import HybridRetriever  # noqa: PLC0415

    assert all(
        [split_into_chunks, VectorStore, OpenAIClient, ask, NoOpReranker, HybridRetriever],
    )


def test_settings_loadable() -> None:
    from bot.config import settings  # noqa: PLC0415

    assert settings.bot_token is not None
    assert settings.openai_api_key is not None
