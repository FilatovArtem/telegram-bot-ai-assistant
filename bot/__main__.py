"""Entry point. Wires logging, DB, vector store, retriever, reranker,
Telegram dispatcher, and the FastAPI app. Polling + uvicorn run concurrently.
"""

from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest, make_asgi_app
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.limiter import limiter
from api.routes import public_router
from api.routes import router as api_router
from bot.config import settings
from bot.db.engine import async_session, dispose_engine, init_db
from bot.handlers import setup_routers
from bot.middlewares.db import DbSessionMiddleware
from bot.middlewares.logging import LoggingMiddleware
from bot.middlewares.metrics import MetricsMiddleware
from bot.middlewares.throttling import ThrottlingMiddleware
from bot.observability.logging_config import configure_logging
from bot.observability.metrics import vector_store_size
from bot.services.embeddings import VectorStore
from bot.services.openai_client import make_openai_client
from bot.services.rerank import make_reranker
from bot.services.retrieval import HybridRetriever
from bot.services.upload import process_document

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = structlog.get_logger(__name__)

SAMPLE_FAQ_PATH = Path("data/knowledge/sample_faq.md")


def _init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk  # noqa: PLC0415
        from sentry_sdk.integrations.fastapi import FastApiIntegration  # noqa: PLC0415
    except ImportError:
        logger.warning("sentry.sdk_missing")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        integrations=[FastApiIntegration()],
        traces_sample_rate=0.05,
        environment=settings.environment,
    )
    logger.info("sentry.initialised")


async def _maybe_seed_sample(
    *,
    store: VectorStore,
    retriever: HybridRetriever,
    openai: Any,
) -> None:
    if not settings.seed_sample_on_first_run:
        return
    if store.size > 0:
        return
    if not SAMPLE_FAQ_PATH.exists():
        return
    content = await asyncio.to_thread(SAMPLE_FAQ_PATH.read_bytes)
    async with async_session() as session:
        result = await process_document(
            content=content,
            filename=SAMPLE_FAQ_PATH.name,
            uploaded_by=None,
            session=session,
            store=store,
            retriever=retriever,
            openai=openai,
        )
    logger.info(
        "bot.seeded",
        filename=result.filename,
        chunks=result.chunks_created,
        ntotal=result.total_index_size,
    )


def _build_app(deps: dict[str, Any]) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield

    app = FastAPI(title="AI Assistant Bot API", version="0.1.0", lifespan=lifespan)
    app.state.deps = deps
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    if settings.metrics_internal_only:
        expected_token = settings.api_admin_token.get_secret_value()

        @app.get("/metrics", include_in_schema=False)
        async def metrics_protected(request: Request) -> Response:
            token = request.headers.get("X-Internal-Token", "")
            if not hmac.compare_digest(token, expected_token):
                raise HTTPException(status_code=401, detail="Unauthorized")
            return Response(
                content=generate_latest(),
                media_type=CONTENT_TYPE_LATEST,
            )
    else:
        app.mount("/metrics", make_asgi_app())
    app.include_router(public_router)
    app.include_router(api_router)
    return app


async def main() -> None:
    configure_logging()
    _init_sentry()

    await init_db()

    store = await VectorStore.load_async()
    openai = make_openai_client()
    retriever = HybridRetriever(
        store=store,
        openai=openai,
        bm25_enabled=settings.bm25_enabled,
        rrf_k=settings.rrf_k,
        top_k_dense=settings.top_k_dense,
        top_k_bm25=settings.top_k_bm25,
    )

    await _maybe_seed_sample(store=store, retriever=retriever, openai=openai)
    await retriever.rebuild_corpus(store.chunks)
    reranker = make_reranker(settings)
    vector_store_size.set(store.size)

    bot_session: AiohttpSession | None = None
    if settings.telegram_proxy:
        bot_session = AiohttpSession(proxy=settings.telegram_proxy.get_secret_value())
        logger.info("telegram.proxy.enabled")
    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        session=bot_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.update.outer_middleware(ThrottlingMiddleware(per_sec=settings.throttle_per_user_per_sec))
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(MetricsMiddleware())
    dp.update.outer_middleware(DbSessionMiddleware(async_session))

    dp["store"] = store
    dp["retriever"] = retriever
    dp["openai"] = openai
    dp["reranker"] = reranker

    dp.include_router(setup_routers())

    app = _build_app(
        {
            "store": store,
            "retriever": retriever,
            "openai": openai,
            "session_pool": async_session,
        }
    )

    @dp.shutdown()
    async def _on_shutdown() -> None:
        await store.save_async()
        await bot.session.close()
        await dispose_engine()
        logger.info("bot.stopped")

    uv_config = uvicorn.Config(
        app,
        host="0.0.0.0",  # noqa: S104 — container binding
        port=settings.api_port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)

    logger.info(
        "bot.starting",
        model=settings.openai_model,
        port=settings.api_port,
        index_size=store.size,
    )

    polling = asyncio.create_task(dp.start_polling(bot, handle_signals=True))
    serving = asyncio.create_task(server.serve())

    _, pending = await asyncio.wait(
        {polling, serving},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
