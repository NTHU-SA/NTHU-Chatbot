"""
Firestore 對話儲存。

後端透過 Admin SDK / ADC 存取，瀏覽器永遠不直接碰 Firestore（`firestore.rules` 全部拒絕）。

資料結構：
  users/{uid}                                  last_seen_at / followed / display_name / created_at / usage
  users/{uid}/sessions/{sid}                   title / created_at / updated_at / message_count / origin
  users/{uid}/sessions/{sid}/messages/{mid}    role / content / created_at / tool_calls
"""

from __future__ import annotations

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from src.application.models.chat import Message, Session, ToolCall
from src.application.services.chat_store import MAX_SESSIONS_PER_USER, new_id, now_utc


class FirestoreChatStore:
    def __init__(self, db: firestore.AsyncClient):
        self._db = db

    # -- refs --
    def _user(self, user_id: str):
        return self._db.collection("users").document(user_id)

    def _sessions(self, user_id: str):
        return self._user(user_id).collection("sessions")

    def _messages(self, user_id: str, session_id: str):
        return self._sessions(user_id).document(session_id).collection("messages")

    # -- users --
    async def touch_user(
        self,
        user_id: str,
        *,
        display_name: str | None = None,
        followed: bool | None = None,
    ) -> None:
        """
        更新使用者最近互動時間。

        webhook 與 LIFF 共用同一份文件，只寫有提供的欄位，避免互相覆蓋。
        """
        reference = self._user(user_id)
        payload: dict = {"last_seen_at": firestore.SERVER_TIMESTAMP}
        if display_name is not None:
            payload["display_name"] = display_name
        if followed is not None:
            payload["followed"] = followed
        snapshot = await reference.get(field_paths=["created_at"])
        if not snapshot.exists or "created_at" not in (snapshot.to_dict() or {}):
            payload["created_at"] = firestore.SERVER_TIMESTAMP
        await reference.set(payload, merge=True)

    # -- sessions --
    @staticmethod
    def _session_from(doc) -> Session:
        data = doc.to_dict() or {}
        return Session(
            id=doc.id,
            title=data.get("title", ""),
            created_at=data.get("created_at") or now_utc(),
            updated_at=data.get("updated_at") or now_utc(),
            message_count=data.get("message_count", 0),
            origin=data.get("origin"),
        )

    async def list_sessions(self, user_id: str) -> list[Session]:
        query = (
            self._sessions(user_id)
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(MAX_SESSIONS_PER_USER)
        )
        return [self._session_from(doc) async for doc in query.stream()]

    async def _evict_oldest_sessions(self, user_id: str) -> None:
        """超過上限時刪除最久未更新的對話，讓新對話一定建得起來。"""
        aggregate = await self._sessions(user_id).count().get()
        count = aggregate[0][0].value if aggregate else 0
        excess = count - MAX_SESSIONS_PER_USER + 1
        if excess <= 0:
            return
        query = (
            self._sessions(user_id)
            .order_by("updated_at", direction=firestore.Query.ASCENDING)
            .limit(excess)
        )
        async for doc in query.stream():
            await self._db.recursive_delete(doc.reference)

    async def create_session(self, user_id, title, origin=None) -> Session:
        await self._evict_oldest_sessions(user_id)
        now = now_utc()
        reference = self._sessions(user_id).document(new_id())
        payload = {
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
        }
        if origin:
            payload["origin"] = origin
        await reference.set(payload)
        return Session(
            id=reference.id, title=title, created_at=now, updated_at=now, origin=origin
        )

    async def get_session(self, user_id: str, session_id: str) -> Session | None:
        doc = await self._sessions(user_id).document(session_id).get()
        return self._session_from(doc) if doc.exists else None

    async def find_session_by_origin(self, user_id, origin) -> Session | None:
        query = (
            self._sessions(user_id)
            .where(filter=FieldFilter("origin", "==", origin))
            .limit(1)
        )
        async for doc in query.stream():
            return self._session_from(doc)
        return None

    async def rename_session(self, user_id: str, session_id: str, title: str) -> None:
        await self._sessions(user_id).document(session_id).update({"title": title})

    async def delete_session(self, user_id: str, session_id: str) -> None:
        await self._db.recursive_delete(self._sessions(user_id).document(session_id))

    # -- messages --
    @staticmethod
    def _message_from(doc) -> Message:
        data = doc.to_dict() or {}
        return Message(
            id=doc.id,
            role=data.get("role", "assistant"),
            content=data.get("content", ""),
            created_at=data.get("created_at") or now_utc(),
            tool_calls=[ToolCall(**tc) for tc in data.get("tool_calls", [])],
        )

    async def list_messages(self, user_id, session_id, limit) -> list[Message]:
        query = (
            self._messages(user_id, session_id)
            .order_by("created_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        messages = [self._message_from(doc) async for doc in query.stream()]
        messages.reverse()
        return messages

    async def add_message(self, user_id, session_id, role, content, tool_calls=None):
        now = now_utc()
        reference = self._messages(user_id, session_id).document(new_id())
        payload = {
            "role": role,
            "content": content,
            "created_at": now,
            "tool_calls": [tc.model_dump() for tc in (tool_calls or [])],
        }
        batch = self._db.batch()
        batch.set(reference, payload)
        batch.update(
            self._sessions(user_id).document(session_id),
            {"updated_at": now, "message_count": firestore.Increment(1)},
        )
        await batch.commit()
        return Message(
            id=reference.id,
            role=role,
            content=content,
            created_at=now,
            tool_calls=tool_calls or [],
        )

    # -- quota --
    async def consume_daily_quota(self, user_id: str, limit: int) -> bool:
        """
        伺服器端原子遞增後讀回比較：不用交易鎖，尖峰時不會因競爭失敗。

        在邊界上同時到達的兩個請求可能都被拒絕，但絕不會都被允許。
        """
        day = now_utc().strftime("%Y-%m-%d")
        reference = self._user(user_id)
        await reference.set({"usage": {day: firestore.Increment(1)}}, merge=True)
        snapshot = await reference.get(field_paths=[f"usage.`{day}`"])
        used = ((snapshot.to_dict() or {}).get("usage") or {}).get(day, 0)
        return used <= limit
