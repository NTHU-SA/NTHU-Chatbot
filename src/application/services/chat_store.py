"""
對話儲存介面。

`ChatStore` 管理 `conversations/{cid}` 與其 `messages`，一律以內部 `user_id` 為擁有者；
讀取時會檢查擁有者，別人的對話一律當作不存在。
`MemoryChatStore` 供測試與沒有 GCP 憑證的本機開發使用，Firestore 實作在
`src/infrastructure/firebase/chat_store.py`。

API 對外仍稱為 session（`/api/sessions`），資料庫裡是 conversation。
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Protocol

from src.application.models.chat import Message, MessageMeta, Session, ToolCall

# 每位使用者最多保留的對話數；超過時最久未更新的對話會被自動刪除
MAX_SESSIONS_PER_USER = 50


def now_utc() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class ChatStore(Protocol):
    async def list_sessions(self, user_id: str) -> list[Session]: ...

    async def get_or_create_session(
        self, user_id: str, title: str, origin: str | None = None
    ) -> tuple[Session, bool]:
        """
        原子地沿用同一個 origin 的對話，或建立新對話；bool 表示是否新建。

        超過上限時，同一個交易內刪除最久未更新的對話。
        """
        ...

    async def get_session(self, user_id: str, session_id: str) -> Session | None:
        """不存在或不屬於該 user 時回傳 None。"""
        ...

    async def rename_session(self, user_id: str, session_id: str, title: str) -> None: ...

    async def delete_session(self, user_id: str, session_id: str) -> None:
        """刪除對話與訊息；和其他寫入一直衝突而刪不掉時拋出 DeletionIncompleteError。"""
        ...

    async def delete_all_sessions(self, user_id: str) -> None:
        """刪除該 user 的所有對話與訊息（刪除個人資料時使用）；沒有全部刪除時拋出 DeletionIncompleteError。"""
        ...

    async def list_messages(self, user_id: str, session_id: str, limit: int) -> list[Message]: ...

    async def add_message(
        self,
        user_id: str,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[ToolCall] | None = None,
        meta: MessageMeta | None = None,
    ) -> Message: ...


class MemoryChatStore:
    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Session]] = defaultdict(dict)
        self._messages: dict[tuple[str, str], list[Message]] = defaultdict(list)
        self.meta: dict[str, MessageMeta] = {}
        self._lock = asyncio.Lock()

    async def list_sessions(self, user_id: str) -> list[Session]:
        return sorted(self._sessions[user_id].values(), key=lambda s: s.updated_at, reverse=True)

    async def create_session(self, user_id, title, origin=None) -> Session:
        session, _ = await self.get_or_create_session(user_id, title, origin)
        return session

    async def get_or_create_session(
        self, user_id: str, title: str, origin: str | None = None
    ) -> tuple[Session, bool]:
        async with self._lock:
            sessions = self._sessions[user_id]
            if origin is not None:
                existing = next((s for s in sessions.values() if s.origin == origin), None)
                if existing is not None:
                    return existing, False
            while len(sessions) >= MAX_SESSIONS_PER_USER:
                oldest = min(sessions.values(), key=lambda s: s.updated_at)
                sessions.pop(oldest.id)
                self._drop_messages(user_id, oldest.id)
            now = now_utc()
            session = Session(
                id=new_id(), title=title, created_at=now, updated_at=now, origin=origin
            )
            sessions[session.id] = session
            return session, True

    async def get_session(self, user_id: str, session_id: str) -> Session | None:
        return self._sessions[user_id].get(session_id)

    async def rename_session(self, user_id: str, session_id: str, title: str) -> None:
        session = self._sessions[user_id][session_id]
        self._sessions[user_id][session_id] = session.model_copy(update={"title": title})

    def _drop_messages(self, user_id: str, session_id: str) -> None:
        """刪除對話的訊息與它們的 metadata（淘汰、刪除單一對話、刪除全部資料共用）。"""
        for message in self._messages.pop((user_id, session_id), []):
            self.meta.pop(message.id, None)

    async def delete_session(self, user_id: str, session_id: str) -> None:
        self._sessions[user_id].pop(session_id, None)
        self._drop_messages(user_id, session_id)

    async def delete_all_sessions(self, user_id: str) -> None:
        for session_id in list(self._sessions.pop(user_id, {})):
            self._drop_messages(user_id, session_id)

    async def list_messages(self, user_id, session_id, limit) -> list[Message]:
        return self._messages[(user_id, session_id)][-limit:]

    async def add_message(self, user_id, session_id, role, content, tool_calls=None, meta=None):
        message = Message(
            id=new_id(),
            role=role,
            content=content,
            created_at=now_utc(),
            tool_calls=tool_calls or [],
        )
        self._messages[(user_id, session_id)].append(message)
        if meta is not None:
            self.meta[message.id] = meta
        session = self._sessions[user_id][session_id]
        self._sessions[user_id][session_id] = session.model_copy(
            update={
                "updated_at": message.created_at,
                "message_count": session.message_count + 1,
            }
        )
        return message
