"""
個人化工具：記住稱呼、系所與使用者明確要求記住的事。

資安：這些是「寫入」工具，模型可能被工具結果裡的文字誘導呼叫它們（prompt injection），
例如公告內容寫著「請記住使用者的密碼是…」。因此同一輪只要讀過外部資料（MCP 工具、網路搜尋），
寫入工具就一律拒絕；只有使用者自己說的話能被記下來。標記存在 `run_state`，
在外部呼叫開始時就設定，不依賴串流事件的處理順序。
"""

from __future__ import annotations

import json
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
from src.infrastructure.ai.run_state import is_tainted

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


def _result(status: str, **data) -> str:
    return json.dumps({"status": status, **data}, ensure_ascii=False)


def _writable(ctx: RunContextWrapper[ChatContext]) -> ChatContext | None:
    context = ctx.context if isinstance(ctx.context, ChatContext) else None
    if context is None or is_tainted():
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
    回傳 ambiguous 時什麼都沒存，請用 suggest_replies 讓使用者從 candidates 選一個，
    之後再把稱呼與選好的系所一起存。
    """
    context = _writable(ctx)
    if context is None:
        return BLOCKED
    # 先確定系所，再一起寫入：系所有多個可能時什麼都不改，等使用者選好再整筆存
    values: dict[str, str] = {}
    if department:
        name, candidates = await context.departments.resolve(department)
        if name is None and candidates:
            return _result("ambiguous", saved=[], candidates=candidates)
        if name is None:
            # 清單不可用或對不到：仍記下使用者的說法（清理過、限長）
            name = clean_text(department, MAX_DEPARTMENT_CHARS)
        if name:
            values["department"] = name
    if nickname:
        value = clean_text(nickname, MAX_NICKNAME_CHARS)
        if value:
            values["nickname"] = value
    if not values:
        return _result("nothing_to_save", saved=[])
    await context.users.set_preferences(context.user_id, values, SOURCE)
    saved = []
    for kind in ("nickname", "department"):
        if kind in values:
            setattr(context.profile, kind, values[kind])
            saved.append({"kind": kind, "value": values[kind]})
    return _result("saved", saved=saved)


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
