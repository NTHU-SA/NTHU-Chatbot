"""
個人化工具：記住稱呼、系所與使用者明確要求記住的事。

資安：這些是「寫入」工具，模型可能被工具結果裡的文字誘導呼叫它們（prompt injection），
例如公告內容寫著「請記住使用者的密碼是…」。因此同一輪只要讀過外部資料（MCP 工具），
寫入工具就一律拒絕；只有使用者自己說的話能被記下來。這個標記用 ContextVar 傳遞，
在 MCP 呼叫開始時就設定，不依賴串流事件的處理順序。
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass

from agents import RunContextWrapper, function_tool

from src.application.models.profile import (
    MAX_DEPARTMENT_CHARS,
    MAX_MEMORY_CHARS,
    MAX_NICKNAME_CHARS,
    MemoryLimitError,
    Profile,
    clean_text,
)
from src.application.services.departments import DepartmentDirectory
from src.application.services.user_store import UserStore

SAVE_PROFILE = "save_profile"
REMEMBER = "remember"
FORGET = "forget"
PERSONAL_TOOLS = (SAVE_PROFILE, REMEMBER, FORGET)
SOURCE = "assistant"

BLOCKED = (
    "blocked: 這一輪已經讀取過外部資料，為了安全不能寫入記憶。"
    "請使用者在下一則訊息直接再說一次。"
)


@dataclass
class ChatContext:
    """一次 LIFF 對話請求的執行環境；交給 Runner 作為 run context。"""

    user_id: str
    session_id: str
    users: UserStore
    departments: DepartmentDirectory
    profile: Profile
    # 這一輪要在回答最後主動問一次稱呼與系所
    onboarding: bool = False
    # 這一輪已讀取外部資料（MCP 工具結果）
    tainted: bool = False


CURRENT: ContextVar[ChatContext | None] = ContextVar("chat_context", default=None)


def mark_external_data() -> None:
    """MCP 工具被呼叫時標記：之後的寫入工具都會被拒絕。"""
    context = CURRENT.get()
    if context is not None:
        context.tainted = True


def _result(status: str, **data) -> str:
    return json.dumps({"status": status, **data}, ensure_ascii=False)


def _writable(ctx: RunContextWrapper[ChatContext]) -> ChatContext | None:
    context = ctx.context if isinstance(ctx.context, ChatContext) else None
    if context is None or context.tainted:
        return None
    return context


@function_tool(name_override=SAVE_PROFILE)
async def save_profile(
    ctx: RunContextWrapper[ChatContext],
    nickname: str | None = None,
    department: str | None = None,
) -> str:
    """
    使用者明確告訴你想被怎麼稱呼、或自己讀哪個系所時呼叫（例如「叫我小明」「我是資工系」）。

    只填使用者這次說出口的那一項，不要猜。department 會比對清大正式系所名稱；
    回傳 ambiguous 時請用 suggest_replies 讓使用者從 candidates 選一個。
    """
    context = _writable(ctx)
    if context is None:
        return BLOCKED
    saved = []
    if nickname:
        value = clean_text(nickname, MAX_NICKNAME_CHARS)
        if value:
            await context.users.set_preference(context.user_id, "nickname", value, SOURCE)
            context.profile.nickname = value
            saved.append({"kind": "nickname", "value": value})
    if department:
        name, candidates = await context.departments.resolve(department)
        if name is None and candidates:
            return _result("ambiguous", saved=saved, candidates=candidates)
        if name is None:
            # 清單不可用或對不到：仍記下使用者的說法（清理過、限長）
            name = clean_text(department, MAX_DEPARTMENT_CHARS)
        if name:
            await context.users.set_preference(context.user_id, "department", name, SOURCE)
            context.profile.department = name
            saved.append({"kind": "department", "value": name})
    return _result("saved" if saved else "nothing_to_save", saved=saved)


@function_tool(name_override=REMEMBER)
async def remember(ctx: RunContextWrapper[ChatContext], fact: str) -> str:
    """
    使用者明確要求你記住一件和自己有關、之後回答會用到的事時呼叫（例如「記得我住清齋」）。

    一句話、100 字以內。不要記敏感資料（證件號碼、密碼、帳號、健康狀況）或一般聊天內容。
    """
    context = _writable(ctx)
    if context is None:
        return BLOCKED
    value = clean_text(fact, MAX_MEMORY_CHARS)
    if not value:
        return _result("nothing_to_save")
    try:
        item = await context.users.add_memory(context.user_id, value, context.session_id)
    except MemoryLimitError:
        return _result("full", message="記憶已滿（20 則），請使用者到設定頁刪除舊的再試。")
    context.profile.memories.append(item)
    return _result("saved", saved=[{"kind": "memory", "value": value}])


@function_tool(name_override=FORGET)
async def forget(ctx: RunContextWrapper[ChatContext], number: int) -> str:
    """使用者要求你忘記某件記住的事時呼叫；number 是使用者資料裡「記住的事」的編號。"""
    context = _writable(ctx)
    if context is None:
        return BLOCKED
    memories = context.profile.memories
    if not 1 <= number <= len(memories):
        return _result("not_found")
    item = memories[number - 1]
    await context.users.delete_memory(context.user_id, item.id)
    memories.remove(item)
    return _result("forgotten", forgotten=item.value)


PERSONAL_TOOL_OBJECTS = [save_profile, remember, forget]
