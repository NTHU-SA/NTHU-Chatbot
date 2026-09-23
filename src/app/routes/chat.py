"""LIFF 對話 API。除了 `/api/config`，每個端點都需要驗證過的 LIFF id_token。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from loguru import logger

from src.app.security import LineUser, get_current_user
from src.application.models.chat import (
    CreateSessionRequest,
    MeResponse,
    Message,
    RenameSessionRequest,
    SendMessageRequest,
    Session,
    ToolCall,
)
from src.application.services.chat_store import ChatStore
from src.core.config import Settings
from src.infrastructure.ai.agent_runner import AgentRunner

router = APIRouter(prefix="/api", tags=["chat"])

DEFAULT_TITLE = "新對話"
AGENT_DEADLINE_SECONDS = 120


def _store(request: Request) -> ChatStore:
    return request.app.state.store


def _runner(request: Request) -> AgentRunner:
    return request.app.state.agent_runner


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def _owned_session(store: ChatStore, user: LineUser, session_id: str) -> Session:
    session = await store.get_session(user.user_id, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return session


@router.get("/config")
async def config(request: Request):
    """LIFF 頁面登入前需要的公開設定，不含任何機密。"""
    return {"liff_id": _settings(request).liff_id}


@router.get("/me", response_model=MeResponse)
async def me(request: Request, user: LineUser = Depends(get_current_user)):
    await _store(request).touch_user(user.user_id, display_name=user.display_name)
    return MeResponse(
        user_id=user.user_id,
        display_name=user.display_name,
        picture_url=user.picture_url,
        liff_id=_settings(request).liff_id,
    )


@router.get("/sessions", response_model=list[Session])
async def list_sessions(request: Request, user: LineUser = Depends(get_current_user)):
    return await _store(request).list_sessions(user.user_id)


@router.post("/sessions", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    response: Response,
    user: LineUser = Depends(get_current_user),
):
    """
    建立對話。

    帶 `origin`（LINE 泡泡的對話金鑰）時為 get-or-create：已存在回 200 與原對話，
    不存在（沒點過或已刪除）才建立並回 201。超過上限時 store 會自動刪除最舊的對話。
    """
    store = _store(request)
    title = (body.title or "").strip() or DEFAULT_TITLE
    session, created = await store.get_or_create_session(user.user_id, title, body.origin)
    if not created:
        response.status_code = status.HTTP_200_OK
    return session


@router.patch("/sessions/{session_id}", response_model=Session)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    request: Request,
    user: LineUser = Depends(get_current_user),
):
    store = _store(request)
    session = await _owned_session(store, user, session_id)
    title = body.title.strip()
    await store.rename_session(user.user_id, session_id, title)
    return session.model_copy(update={"title": title})


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, request: Request, user: LineUser = Depends(get_current_user)
):
    store = _store(request)
    await _owned_session(store, user, session_id)
    await store.delete_session(user.user_id, session_id)


@router.get("/sessions/{session_id}/messages", response_model=list[Message])
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = 50,
    user: LineUser = Depends(get_current_user),
):
    store = _store(request)
    await _owned_session(store, user, session_id)
    return await store.list_messages(user.user_id, session_id, min(max(limit, 1), 200))


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    request: Request,
    user: LineUser = Depends(get_current_user),
):
    settings = _settings(request)
    store = _store(request)
    runner = _runner(request)

    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty message")
    if len(text) > settings.max_message_chars:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"message too long (max {settings.max_message_chars} chars)",
        )

    session = await _owned_session(store, user, session_id)
    if not await store.consume_daily_quota(user.user_id, settings.daily_message_limit):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "今日對話額度已用完，明天再來吧。"
        )

    # 先讀歷史，再寫入這次的使用者訊息。
    history = await store.list_messages(user.user_id, session_id, settings.history_window)
    user_message = await store.add_message(user.user_id, session_id, "user", text)
    if session.message_count == 0 and session.title == DEFAULT_TITLE:
        await store.rename_session(user.user_id, session_id, text[:30])

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("user_message", {"id": user_message.id})
        try:
            async with asyncio.timeout(AGENT_DEADLINE_SECONDS):
                async for event in runner.stream(history, text):
                    if event.type == "done":
                        saved = await store.add_message(
                            user.user_id,
                            session_id,
                            "assistant",
                            event.data["content"][: settings.max_output_chars],
                            [ToolCall(**tc) for tc in event.data["tool_calls"]],
                        )
                        yield _sse("done", {"id": saved.id, "content": saved.content})
                    else:
                        yield _sse(event.type, event.data)
        except TimeoutError:
            yield _sse("error", {"message": "回應逾時，請再試一次。"})
        except Exception as error:
            logger.error("Chat stream failed: {}", type(error).__name__)
            yield _sse("error", {"message": "系統暫時無法回應，請稍後再試。"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
