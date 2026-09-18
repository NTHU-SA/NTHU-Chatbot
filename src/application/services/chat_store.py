"""
對話儲存介面。

`ChatStore` 是應用層對外的介面；`MemoryChatStore` 供測試與沒有 GCP 憑證的本機開發使用，
正式環境的 Firestore 實作在 `src/infrastructure/firebase/chat_store.py`。
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol

from src.application.models.chat import Message, Session, ToolCall

# 每位使用者最多保留的對話數；超過時最久未更新的對話會被自動刪除
MAX_SESSIONS_PER_USER = 50


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class ChatStore(Protocol):
    async def touch_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        followed: bool | None = None,
    ) -> None: ...

    async def list_sessions(self, user_id: str) -> list[Session]: ...

    async def create_session(
        self, user_id: str, title: str, origin: str | None = None
    ) -> Session: ...

    async def get_session(self, user_id: str, session_id: str) -> Session | None: ...

    async def find_session_by_origin(self, user_id: str, origin: str) -> Session | None: ...

    async def rename_session(self, user_id: str, session_id: str, title: str) -> None: ...

    async def delete_session(self, user_id: str, session_id: str) -> None: ...

    async def list_messages(
        self, user_id: str, session_id: str, limit: int
    ) -> list[Message]: ...

    async def add_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[ToolCall] | None = None,
    ) -> Message: ...

    async def consume_daily_quota(self, user_id: str, limit: int) -> bool:
        """計入今日一則 LLM 訊息；超過上限時回傳 False。"""
        ...


class MemoryChatStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Session]] = defaultdict(dict)
        self._messages: dict[tuple[str, str], list[Message]] = defaultdict(list)
        self._usage: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def touch_user(self, user_id, *, display_name=None, followed=None) -> None:
        return None

    async def list_sessions(self, user_id: str) -> list[Session]:
        return sorted(
            self._sessions[user_id].values(), key=lambda s: s.updated_at, reverse=True
        )

    async def create_session(self, user_id, title, origin=None) -> Session:
        sessions = self._sessions[user_id]
        while len(sessions) >= MAX_SESSIONS_PER_USER:
            oldest = min(sessions.values(), key=lambda s: s.updated_at)
            await self.delete_session(user_id, oldest.id)
        now = now_utc()
        session = Session(
            id=new_id(), title=title, created_at=now, updated_at=now, origin=origin
        )
        self._sessions[user_id][session.id] = session
        return session

    async def get_session(self, user_id: str, session_id: str) -> Session | None:
        return self._sessions[user_id].get(session_id)

    async def find_session_by_origin(self, user_id, origin) -> Session | None:
        return next(
            (s for s in self._sessions[user_id].values() if s.origin == origin), None
        )

    async def rename_session(self, user_id: str, session_id: str, title: str) -> None:
        session = self._sessions[user_id][session_id]
        self._sessions[user_id][session_id] = session.model_copy(update={"title": title})

    async def delete_session(self, user_id: str, session_id: str) -> None:
        self._sessions[user_id].pop(session_id, None)
        self._messages.pop((user_id, session_id), None)

    async def list_messages(self, user_id, session_id, limit) -> list[Message]:
        return self._messages[(user_id, session_id)][-limit:]

    async def add_message(self, user_id, session_id, role, content, tool_calls=None):
        message = Message(
            id=new_id(),
            role=role,
            content=content,
            created_at=now_utc(),
            tool_calls=tool_calls or [],
        )
        self._messages[(user_id, session_id)].append(message)
        session = self._sessions[user_id][session_id]
        self._sessions[user_id][session_id] = session.model_copy(
            update={
                "updated_at": message.created_at,
                "message_count": session.message_count + 1,
            }
        )
        return message

    async def consume_daily_quota(self, user_id: str, limit: int) -> bool:
        key = (user_id, now_utc().strftime("%Y-%m-%d"))
        async with self._lock:
            if self._usage[key] >= limit:
                return False
            self._usage[key] += 1
            return True
