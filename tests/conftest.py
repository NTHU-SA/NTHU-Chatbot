"""pytest 共用 fixture。假物件放在 `tests/fakes.py`。"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from src.app import create_app
from src.app.auth.rate_limit import RateLimiter
from src.app.auth.service import IdentityService
from src.application.services.chat_store import MemoryChatStore
from src.application.services.module_registry import StaticModuleRegistry
from src.application.services.user_store import MemoryUserStore
from tests.fakes import FakeAuthenticator, FakeRunner, make_settings


def pytest_collection_modifyitems(config, items):
    """沒有 Firestore emulator 時跳過標記為 firestore 的測試。"""
    if os.getenv("FIRESTORE_EMULATOR_HOST"):
        return
    skip = pytest.mark.skip(reason="Firestore emulator required")
    for item in items:
        if "firestore" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def chat_app(runner):
    """
    不跑 lifespan 的 LIFF 對話 app。

    直接覆寫 `app.state`，測試可以再替換其中任何一個元件。
    """
    app = create_app()
    app.state.settings = make_settings(daily_message_limit=3)
    app.state.store = MemoryChatStore()
    app.state.user_store = MemoryUserStore()
    app.state.identity_service = IdentityService(app.state.user_store)
    app.state.module_registry = StaticModuleRegistry()
    app.state.authenticators = {"line": FakeAuthenticator()}
    app.state.rate_limiter = RateLimiter(rate_per_minute=600, burst=100)
    app.state.agent_runner = runner
    return app


@pytest.fixture
def client(chat_app) -> TestClient:
    return TestClient(chat_app)
