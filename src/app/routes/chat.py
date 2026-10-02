"""LIFF 對話 API。每個端點都需要驗證過的登入 token；前端設定改由 Hosting 的 config.json 提供。"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from loguru import logger

from src.app.auth.dependencies import get_principal
from src.app.background import BackgroundWrites
from src.app.routes.account import require_consent
from src.application.models.chat import (
    CreateSessionRequest,
    Message,
    MessageMeta,
    RenameSessionRequest,
    SendMessageRequest,
    Session,
    TokenUsage,
    ToolCall,
)
from src.application.models.identity import Principal
from src.application.services.chat_store import ChatStore
from src.application.services.user_store import UserStore
from src.core.config import Settings
from src.infrastructure.ai.agent_runner import AgentRunner
from src.infrastructure.ai.personal_tools import ChatContext
from src.infrastructure.ai.prompts import PROMPT_VERSION

router = APIRouter(prefix="/api", tags=["chat"])

DEFAULT_TITLE = "新對話"
AGENT_DEADLINE_SECONDS = 120


def _store(request: Request) -> ChatStore:
    return request.app.state.store


def _users(request: Request) -> UserStore:
    return request.app.state.user_store


def _runner(request: Request) -> AgentRunner:
    return request.app.state.agent_runner


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


async def _owned_session(store: ChatStore, user: Principal, session_id: str) -> Session:
    session = await store.get_session(user.user_id, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    return session


@router.get("/sessions", response_model=list[Session])
async def list_sessions(request: Request, user: Principal = Depends(get_principal)):
    return await _store(request).list_sessions(user.user_id)


@router.post("/sessions", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    response: Response,
    user: Principal = Depends(get_principal),
):
    """
    建立對話。

    帶 `origin`（LINE 泡泡的對話金鑰）時為 get-or-create：已存在回 200 與原對話，
    不存在（沒點過或已刪除）才建立並回 201。超過上限時 store 會自動刪除最舊的對話。
    """
    title = (body.title or "").strip() or DEFAULT_TITLE
    session, created = await _store(request).get_or_create_session(
        user.user_id, title, body.origin
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return session


@router.patch("/sessions/{session_id}", response_model=Session)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    request: Request,
    user: Principal = Depends(get_principal),
):
    store = _store(request)
    session = await _owned_session(store, user, session_id)
    title = body.title.strip()
    await store.rename_session(user.user_id, session_id, title)
    return session.model_copy(update={"title": title})


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str, request: Request, user: Principal = Depends(get_principal)
):
    store = _store(request)
    await _owned_session(store, user, session_id)
    await store.delete_session(user.user_id, session_id)


@router.get("/sessions/{session_id}/messages", response_model=list[Message])
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = 50,
    user: Principal = Depends(get_principal),
):
    store = _store(request)
    await _owned_session(store, user, session_id)
    return await store.list_messages(user.user_id, session_id, min(max(limit, 1), 200))


def _retried_message(history: list[Message], retry_of: str | None, text: str) -> Message | None:
    """
    重試時沿用已存的使用者訊息，不再存一次（否則歷史與模型輸入都會重複）。

    只有它仍是這個對話的最後一則、而且內容相同時才沿用；之後已經有回覆（或別的訊息）就回 409，
    前端應重新載入對話。每次重試仍會呼叫模型，所以照常計入每日額度。
    """
    if retry_of is None:
        return None
    last = history[-1] if history else None
    if last is None or last.id != retry_of or last.role != "user" or last.content != text:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "retry_stale", "message": "對話已經更新，請重新載入。"},
        )
    return last


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    request: Request,
    user: Principal = Depends(get_principal),
):
    started = time.monotonic()
    settings = _settings(request)
    store = _store(request)
    users = _users(request)
    runner = _runner(request)

    text = body.text.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "empty message")
    if len(text) > settings.max_message_chars:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"message too long (max {settings.max_message_chars} chars)",
        )

    session = await _owned_session(store, user, session_id)
    # 訊息會交給 LLM 處理：必須先同意目前版本的隱私權政策
    await require_consent(request, user)

    # 先讀歷史與使用者資料，再寫入這次的使用者訊息。
    history, profile = await asyncio.gather(
        store.list_messages(user.user_id, session_id, settings.history_window + 1),
        users.get_profile(user.user_id),
    )
    retried = _retried_message(history, body.retry_of, text)
    history = history[:-1] if retried else history[-settings.history_window :]

    if not await users.consume_daily_quota(user.user_id, settings.daily_message_limit):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "今日對話額度已用完，明天再來吧。"
        )

    # 最近活動時間與 LLM 回覆並行寫入，串流結束前收尾
    writes = BackgroundWrites()
    writes.spawn(users.touch_activity(user.user_id), "touch_activity")
    # 第一次聊天、也還沒提供稱呼與系所：這一輪回答最後主動問一次，之後不再問
    onboarding = profile.onboarding is None and not (profile.nickname or profile.department)
    if onboarding:
        writes.spawn(users.set_onboarding(user.user_id, "asked"), "onboarding")
    context = ChatContext(
        user_id=user.user_id,
        session_id=session_id,
        users=users,
        departments=request.app.state.departments,
        profile=profile,
        onboarding=onboarding,
    )
    if retried:
        user_message = retried
    else:
        user_message = await store.add_message(user.user_id, session_id, "user", text)
    if session.message_count == 0 and session.title == DEFAULT_TITLE:
        await store.rename_session(user.user_id, session_id, text[:30])

    async def event_stream() -> AsyncIterator[str]:
        yield _sse("user_message", {"id": user_message.id})
        try:
            async with asyncio.timeout(AGENT_DEADLINE_SECONDS):
                async for event in runner.stream(history, text, context):
                    if event.type == "done":
                        usage = event.data.get("usage")
                        meta = MessageMeta(
                            model=settings.openai_model,
                            prompt_version=PROMPT_VERSION,
                            token_usage=TokenUsage(**usage) if usage else None,
                            latency_ms=int((time.monotonic() - started) * 1000),
                        )
                        saved = await store.add_message(
                            user.user_id,
                            session_id,
                            "assistant",
                            event.data["content"][: settings.max_output_chars],
                            [ToolCall(**tc) for tc in event.data["tool_calls"]],
                            meta,
                        )
                        # usage 只存進資料庫，不送前端
                        yield _sse("done", {"id": saved.id, "content": saved.content})
                    else:
                        yield _sse(event.type, event.data)
        except TimeoutError:
            yield _sse("error", {"message": "回應逾時，請再試一次。"})
        except Exception as error:
            logger.error("Chat stream failed: {}", type(error).__name__)
            yield _sse("error", {"message": "系統暫時無法回應，請稍後再試。"})
        finally:
            await writes.drain()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
