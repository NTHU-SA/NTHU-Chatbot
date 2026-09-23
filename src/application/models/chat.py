"""LIFF 對話 API 與儲存層共用的資料模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    result_preview: str | None = None
    duration_ms: int | None = None
    ok: bool = True


class Message(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    tool_calls: list[ToolCall] = Field(default_factory=list)


class Session(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    origin: str | None = None


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=80)
    # 由 LINE 泡泡帶入的對話金鑰；同一金鑰會回到同一個對話（get-or-create）
    origin: str | None = Field(default=None, min_length=1, max_length=64)


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class MeResponse(BaseModel):
    user_id: str
    display_name: str | None
    picture_url: str | None
    liff_id: str
