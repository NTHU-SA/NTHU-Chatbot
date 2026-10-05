"""
這個目錄包含整個 line bot 的主要程式碼。
- `routes`: 處理 API 請求，包含 `/callback`（LINE webhook）、`/api/*`（LIFF 對話）與 `/ping`
- `handlers`: 聊天室內的 `@` 指令路由
- `auth`: 登入 token 驗證（LIFF 用 LINE、一般瀏覽器用 Auth0）、外部身分 → 內部 user、限流
- `middleware`: 安全標頭
LIFF 前端在 repo 根目錄的 `frontend/`，由 Firebase Hosting 提供，透過 CORS 呼叫這裡的 API。
"""

import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from google.cloud import firestore
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration
from linebot.v3.webhook import WebhookParser
from loguru import logger

from src.app.auth.auth0 import Auth0Authenticator
from src.app.auth.line import LineLiffAuthenticator
from src.app.auth.rate_limit import RateLimiter
from src.app.auth.service import IdentityService
from src.application.services.chat_store import MemoryChatStore
from src.application.services.departments import DepartmentDirectory
from src.application.services.module_registry import StaticModuleRegistry
from src.application.services.user_store import MemoryUserStore
from src.core.config import Settings, parse_cors_origins
from src.infrastructure.ai.agent_runner import AgentRunner
from src.infrastructure.firebase.chat_store import FirestoreChatStore
from src.infrastructure.firebase.module_registry import FirestoreModuleRegistry
from src.infrastructure.firebase.user_store import FirestoreUserStore


async def _fetch_departments():
    from src.utils import nthuapi

    return await nthuapi.get("/directory")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    app.state.settings = settings

    database = None
    if settings.chat_store == "memory":
        logger.warning("Using in-memory chat store (data is NOT persisted)")
        store, user_store = MemoryChatStore(), MemoryUserStore()
        module_registry = StaticModuleRegistry()
    else:
        database = firestore.AsyncClient(
            project=settings.google_cloud_project, database=settings.firestore_database
        )
        store, user_store = FirestoreChatStore(database), FirestoreUserStore(database)
        module_registry = FirestoreModuleRegistry(database)

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
            app.state.user_store = user_store
            app.state.module_registry = module_registry
            app.state.identity_service = IdentityService(user_store)
            app.state.departments = DepartmentDirectory(_fetch_departments)
            # 新增登入方式時在這裡註冊；provider 名稱即前端 X-Auth-Provider 的值
            app.state.authenticators = {
                "line": LineLiffAuthenticator(settings.line_login_channel_id, http),
            }
            if settings.auth0_domain:
                app.state.authenticators["auth0"] = Auth0Authenticator(
                    settings.auth0_domain, settings.auth0_audience, settings.auth0_client_id, http
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
    from .middleware import add_cors, security_headers
    from .routes import account, base, callback, chat

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(security_headers)
    # middleware 必須在啟動前掛上，所以直接讀環境變數（與 Settings 用同一個解析函式）
    add_cors(app, parse_cors_origins(os.getenv("CORS_ALLOWED_ORIGINS")))
    app.include_router(base.router)
    app.include_router(callback.router)
    app.include_router(account.router)
    app.include_router(chat.router)
    return app
