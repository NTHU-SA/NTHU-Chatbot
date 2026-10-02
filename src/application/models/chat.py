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


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    requests: int = 0


class MessageMeta(BaseModel):
    """
    assistant 訊息的執行資訊，只存進資料庫供成本與品質分析，不回傳給前端。
    """

    model: str | None = None
    prompt_version: str | None = None
    token_usage: TokenUsage | None = None
    latency_ms: int | None = None


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
    """不回傳任何 ID（內部或外部）；前端只需要顯示用資料。"""

    display_name: str | None
    picture_url: str | None
    liff_id: str
