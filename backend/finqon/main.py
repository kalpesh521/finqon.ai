"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from finqon import __version__
from finqon.api import auth, chat, conversations, files, health, statement
from finqon.api.middleware import RequestContextMiddleware
from finqon.api.openapi import install_chat_event_schema
from finqon.core.config import Settings, get_settings
from finqon.core.errors import install_error_handlers
from finqon.core.logging import configure_logging
from finqon.llm.client import LLMProvider
from finqon.resources import Resources

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMProvider | None = None,
    require_llm_key: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.require_runtime_settings(require_llm=require_llm_key and llm is None)
        app.state.resources = await Resources.create(settings, llm=llm)
        logger.info("startup_complete", extra={"version": __version__})
        try:
            yield
        finally:
            ingestion = getattr(app.state, "ingestion", None)
            if ingestion is not None:
                await ingestion.close()
            await app.state.resources.close()

    app = FastAPI(
        title="FinQon.ai",
        version=__version__,
        description="Ask questions about your bank transactions in plain language.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-Conversation-Id"],
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(conversations.router, prefix="/api/v1")
    app.include_router(files.router, prefix="/api/v1")
    app.include_router(statement.router, prefix="/api/v1")
    install_chat_event_schema(app)
    return app


def app_factory() -> FastAPI:
    """Uvicorn entry point: ``uvicorn finqon.main:app_factory --factory``."""
    return create_app()
