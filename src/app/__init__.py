"""
這個目錄包含整個 line bot 的主要程式碼。
- `routes`: 處理 API 請求，包含 `/callback`（LINE webhook）、`/api/*`（LIFF 對話）與 `/ping`
- `handlers`: 聊天室內的 `@` 指令路由
- `security` / `middleware`: LIFF id_token 驗證、限流與安全標頭
- `static/liff`: LIFF 前端靜態頁面
"""

from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from google.cloud import firestore
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration
from linebot.v3.webhook import WebhookParser
from loguru import logger

from src.app.security import LiffTokenVerifier, RateLimiter
from src.application.services.chat_store import MemoryChatStore
from src.core.config import Settings
from src.infrastructure.ai.agent_runner import AgentRunner
from src.infrastructure.firebase.chat_store import FirestoreChatStore

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings

    database = None
    if settings.chat_store == "memory":
        logger.warning("Using in-memory chat store (data is NOT persisted)")
        store = MemoryChatStore()
    else:
        database = firestore.AsyncClient(project=settings.google_cloud_project)
        store = FirestoreChatStore(database)

    runner = AgentRunner(settings)
    try:
        async with (
            httpx.AsyncClient() as http,
            AsyncApiClient(
                Configuration(access_token=settings.line_channel_access_token)
            ) as line_client,
        ):
            app.state.parser = WebhookParser(settings.line_channel_secret)
            app.state.messaging_api = AsyncMessagingApi(line_client)
            app.state.store = store
            app.state.token_verifier = LiffTokenVerifier(
                settings.line_login_channel_id, http
            )
            app.state.rate_limiter = RateLimiter()
            app.state.agent_runner = runner
            await runner.start()
            try:
                yield
            finally:
                await runner.stop()
    finally:
        if database is not None:
            database.close()


def create_app() -> FastAPI:
    from .middleware import security_headers
    from .routes import base, callback, chat

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(security_headers)
    app.include_router(base.router)
    app.include_router(callback.router)
    app.include_router(chat.router)
    app.mount("/liff", StaticFiles(directory=STATIC_DIR / "liff", html=True), name="liff")
    return app
