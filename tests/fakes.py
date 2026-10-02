"""測試共用的假物件。所有值都是明顯的假資料，不含任何真實憑證。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from src.application.models.identity import VerifiedIdentity
from src.core.config import Settings
from src.infrastructure.ai.agent_runner import AgentEvent

TEST_LIFF_ID = "1234567890-abcdefgh"
TEST_IDENTITY = VerifiedIdentity(
    provider="line", provider_user_id="U0123456789abcdef", display_name="測試者"
)
OTHER_IDENTITY = VerifiedIdentity(provider="line", provider_user_id="U0000000000other")
AUTH = {"Authorization": "Bearer good-token"}


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "line_channel_secret": "test-secret",
        "line_channel_access_token": "test-token",
        "line_login_channel_id": "1234567890",
        "liff_id": TEST_LIFF_ID,
        "openai_api_key": "test-key",
        "chat_store": "memory",
    }
    values.update(overrides)
    return Settings(**values)


class FakeAuthenticator:
    """假的 LINE authenticator：只認得 `valid` 裡的 token。"""

    provider = "line"

    def __init__(self):
        self.valid = {"good-token": TEST_IDENTITY, "other-token": OTHER_IDENTITY}

    async def verify(self, token: str) -> VerifiedIdentity:
        if token in self.valid:
            return self.valid[token]
        raise HTTPException(401, "invalid id_token")


# NTHU API 單位目錄的一小部分（假資料的形狀與正式 API 相同；只取名稱，不含人員）
FAKE_DEPARTMENTS = [
    {"index": "6907", "name": "資訊工程學系", "parent_name": "電機資訊學院"},
    {"index": "6905", "name": "電機工程學系", "parent_name": "電機資訊學院"},
    {"index": "6913", "name": "資訊系統與應用研究所", "parent_name": "電機資訊學院"},
    {"index": "6917", "name": "資訊安全研究所", "parent_name": "電機資訊學院"},
    {"index": "5301", "name": "數學系", "parent_name": "理學院"},
    {"index": "0101", "name": "教務處", "parent_name": None},
]


async def fake_departments():
    return FAKE_DEPARTMENTS


class FakeRunner:
    """固定送出一串事件；記錄輸入供斷言。"""

    def __init__(self):
        self.streams: list[tuple[list, str]] = []
        self.contexts: list = []
        self.fail = False

    async def stream(self, history, user_text, context=None):
        self.streams.append((list(history), user_text))
        self.contexts.append(context)
        if self.fail:
            yield AgentEvent("error", {"message": "boom"})
            return
        yield AgentEvent("thinking", {"delta": "先查南大方向的下一班車"})
        yield AgentEvent(
            "tool_call_start",
            {"call_id": "c1", "name": "get_next_buses", "args": {"route": "nanda"}},
        )
        yield AgentEvent(
            "tool_call_end",
            {
                "call_id": "c1",
                "name": "get_next_buses",
                "ok": True,
                "duration_ms": 12,
                "result_preview": "{}",
            },
        )
        yield AgentEvent("token", {"delta": "下一班 "})
        yield AgentEvent("token", {"delta": "17:00"})
        yield AgentEvent(
            "done",
            {
                "content": "下一班 17:00",
                "tool_calls": [
                    {
                        "name": "get_next_buses",
                        "args": {"route": "nanda"},
                        "result_preview": "{}",
                        "duration_ms": 12,
                        "ok": True,
                    }
                ],
            },
        )


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in text.strip().split("\n\n"):
        name, data = "message", ""
        for line in chunk.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        events.append((name, json.loads(data)))
    return events
