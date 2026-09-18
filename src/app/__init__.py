"""
這個目錄包含整個 line bot 的主要程式碼。
- `bot`: 建立 line bot instance 並處理事件
- `routes`: 處理 API 請求，包含 `/callback` 和 `/` 兩個路徑
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from google.cloud import firestore
from linebot.v3.messaging import AsyncApiClient, AsyncMessagingApi, Configuration
from linebot.v3.webhook import WebhookParser
from openai import AsyncOpenAI

from src.application.services.chat_service import ChatService
from src.infrastructure.ai.openai_agent import OpenAIAgent
from src.infrastructure.firebase.user_repository import UserRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    required = (
        "LINE_CHANNEL_SECRET",
        "LINE_CHANNEL_ACCESS_TOKEN",
        "OPENAI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    database = firestore.AsyncClient(project=os.environ["GOOGLE_CLOUD_PROJECT"])
    try:
        async with (
            AsyncOpenAI(timeout=40, max_retries=0) as ai_client,
            AsyncApiClient(
                Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
            ) as line_client,
        ):
            app.state.parser = WebhookParser(os.environ["LINE_CHANNEL_SECRET"])
            app.state.messaging_api = AsyncMessagingApi(line_client)
            app.state.users = UserRepository(database)
            app.state.chat = ChatService(
                OpenAIAgent(ai_client, os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
                app.state.users,
            )
            yield
    finally:
        database.close()


def create_app() -> FastAPI:
    from .routes import base, callback

    app = FastAPI(lifespan=lifespan)
    app.include_router(base.router)
    app.include_router(callback.router)
    return app
