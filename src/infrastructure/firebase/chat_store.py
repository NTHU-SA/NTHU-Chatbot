"""
Firestore 對話儲存。

資料結構（欄位名稱依 firebase.spec）：
  conversations/{cid}                     userId / channel / status / title / startedAt /
                                          lastMessageAt / messageCount / currentModuleId / metadata{origin}
  conversations/{cid}/messages/{mid}      role / contentType / content / moduleId / model /
                                          promptVersion / toolCalls / tokenUsage / latencyMs / createdAt
  users/{uid}.conversationCount           該 user 的對話數（建立 / 刪除時以 Increment 維護）
  users/{uid}/conversationOrigins/{h}     LINE 泡泡 origin → conversationId（h = sha256(origin)）
  users/{uid}/conversationCleanup/{cid}   被淘汰或刪除、訊息尚未清完的對話（durable marker）

對話是 top-level collection（需要跨 user 查詢、持續增長），以 `userId` 欄位標示擁有者；
所有讀取都會比對 `userId`，別人的對話一律視為不存在。
"""

from __future__ import annotations

import hashlib

from google.api_core.exceptions import (
    AlreadyExists,
    Conflict,
    FailedPrecondition,
    NotFound,
)
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from loguru import logger

from src.application.models.chat import Message, MessageMeta, Session, ToolCall
from src.application.models.identity import (
    DELETED,
    AccountDisabledError,
    DeletionIncompleteError,
)
from src.application.services.chat_store import MAX_SESSIONS_PER_USER, new_id, now_utc

TRANSACTION_ATTEMPTS = 10
CHANNEL = "liff"
# 刪除後重新查詢確認；BulkWriter 個別刪除的重試用盡時不會拋出例外
DELETE_ATTEMPTS = 3


class FirestoreChatStore:
    def __init__(self, db: firestore.AsyncClient):
        self._db = db

    # -- refs --
    def _user(self, user_id: str):
        return self._db.collection("users").document(user_id)

    def _conversations(self):
        return self._db.collection("conversations")

    def _owned(self, user_id: str):
        return self._conversations().where(filter=FieldFilter("userId", "==", user_id))

    def _messages(self, session_id: str):
        return self._conversations().document(session_id).collection("messages")

    def _cleanup(self, user_id: str):
        return self._user(user_id).collection("conversationCleanup")

    def _origin(self, user_id: str, origin: str):
        key = hashlib.sha256(origin.encode("utf-8")).hexdigest()
        return self._user(user_id).collection("conversationOrigins").document(key)

    # -- conversations --
    @staticmethod
    def _session_from(doc) -> Session:
        data = doc.to_dict() or {}
        return Session(
            id=doc.id,
            title=data.get("title", ""),
            created_at=data.get("startedAt") or now_utc(),
            updated_at=data.get("lastMessageAt") or now_utc(),
            message_count=data.get("messageCount", 0),
            origin=(data.get("metadata") or {}).get("origin"),
        )

    async def list_sessions(self, user_id: str) -> list[Session]:
        query = (
            self._owned(user_id)
            .order_by("lastMessageAt", direction=firestore.Query.DESCENDING)
            .limit(MAX_SESSIONS_PER_USER)
        )
        return [self._session_from(doc) async for doc in query.stream()]

    async def create_session(self, user_id, title, origin=None) -> Session:
        session, _ = await self.get_or_create_session(user_id, title, origin)
        return session

    async def get_or_create_session(
        self, user_id: str, title: str, origin: str | None = None
    ) -> tuple[Session, bool]:
        """
        沿用同一個 origin 的對話，或建立新對話；超過上限時再淘汰最舊的。

        建立時刻意不讀 user 文件：多個交易都先讀再寫同一份文件，會互相卡住讀鎖升級
        （emulator 上直接 lock timeout）。所以
        - 沒有 origin：純寫入（對話數用 Increment），不需要交易；
        - 有 origin：以 `create()` 搶 `conversationOrigins/{sha256(origin)}`，同樣不需要交易；
        - 淘汰在建立之後逐則進行（見 `_enforce_limit`），並行時對話數可能短暫超過上限，下一次建立會修正。
        """
        reference = self._conversations().document(new_id())
        now = now_utc()
        session = Session(
            id=reference.id, title=title, created_at=now, updated_at=now, origin=origin
        )
        payload = {
            "userId": user_id,
            "channel": CHANNEL,
            "status": "active",
            "title": title,
            "startedAt": now,
            "lastMessageAt": now,
            "messageCount": 0,
            "currentModuleId": None,
            "metadata": {"origin": origin} if origin else {},
        }
        counter = {
            "conversationCount": firestore.Increment(1),
            "lastConversationId": reference.id,
        }

        if origin is None:
            batch = self._db.batch()
            batch.create(reference, payload)
            batch.set(self._user(user_id), counter, merge=True)
            await batch.commit()
            created = True
        else:
            session, created = await self._create_for_origin(
                user_id, origin, reference, payload, counter, session
            )

        if created:
            await self._undo_if_deleted(user_id, session)
            await self._enforce_limit(user_id)
        await self._drain_cleanup(user_id)
        return session, created

    async def _undo_if_deleted(self, user_id: str, session: Session) -> None:
        """
        建立後確認 user 沒有被刪除（只剩墓碑）；已刪除時撤銷這個對話並拋出 AccountDisabledError。

        和 FirestoreUserStore 一樣：墓碑之前建立的對話由刪除流程的最後一輪清掉，之後建立的在這裡撤銷。
        """
        snapshot = await self._user(user_id).get(field_paths=["status"])
        if (snapshot.to_dict() or {}).get("status") != DELETED:
            return
        await self._db.recursive_delete(self._conversations().document(session.id))
        if session.origin:
            await self._origin(user_id, session.origin).delete()
        await self._user(user_id).update(
            {
                "conversationCount": firestore.DELETE_FIELD,
                "lastConversationId": firestore.DELETE_FIELD,
            }
        )
        raise AccountDisabledError(user_id, DELETED)

    async def _create_for_origin(self, user_id, origin, reference, payload, counter, session):
        """
        同一顆泡泡只建立一個對話。

        先用 batch `create()` 搶 origin 文件（不讀、不鎖，並行請求只有一個會成功）；
        搶輸就讀回既有的對話。只有 origin 指向的對話已不存在（刪除中途失敗）時，
        才用交易把 origin 改指向新對話。
        """
        origin_ref = self._origin(user_id, origin)
        origin_payload = {"conversationId": reference.id, "createdAt": firestore.SERVER_TIMESTAMP}
        batch = self._db.batch()
        batch.create(origin_ref, origin_payload)
        batch.create(reference, payload)
        batch.set(self._user(user_id), counter, merge=True)
        try:
            await batch.commit()
            return session, True
        except AlreadyExists, Conflict:
            pass

        @firestore.async_transactional
        async def reuse_or_replace(transaction):
            snapshot = await origin_ref.get(transaction=transaction)
            if snapshot.exists:
                existing = (
                    await self._conversations()
                    .document(snapshot.get("conversationId"))
                    .get(transaction=transaction)
                )
                if existing.exists and (existing.to_dict() or {}).get("userId") == user_id:
                    return self._session_from(existing), False
            transaction.set(origin_ref, origin_payload)
            transaction.create(reference, payload)
            transaction.set(self._user(user_id), counter, merge=True)
            return session, True

        return await reuse_or_replace(self._db.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    async def _enforce_limit(self, user_id: str) -> None:
        """對話數超過上限時，刪除最久未更新的對話。可以重複或並行執行。"""
        snapshot = await self._user(user_id).get(field_paths=["conversationCount"])
        count = (snapshot.to_dict() or {}).get("conversationCount", 0) or 0
        excess = count - MAX_SESSIONS_PER_USER
        if excess <= 0:
            return
        query = (
            self._owned(user_id)
            .order_by("lastMessageAt", direction=firestore.Query.ASCENDING)
            .limit(excess)
        )
        async for doc in query.stream():
            await self._delete(user_id, doc.id)

    async def get_session(self, user_id: str, session_id: str) -> Session | None:
        doc = await self._conversations().document(session_id).get()
        if not doc.exists or (doc.to_dict() or {}).get("userId") != user_id:
            return None
        return self._session_from(doc)

    async def rename_session(self, user_id: str, session_id: str, title: str) -> None:
        if await self.get_session(user_id, session_id) is None:
            return
        await self._conversations().document(session_id).update({"title": title})

    async def delete_session(self, user_id: str, session_id: str) -> None:
        """
        刪除對話；`_delete` 的前置條件失敗（例如串流中的回覆剛好寫入）時重讀再試。

        對話已不存在就算完成；重試用盡仍刪不掉時拋出 DeletionIncompleteError，
        不讓 API 回 204 卻留下對話。
        """
        for _ in range(DELETE_ATTEMPTS):
            if await self._delete(user_id, session_id):
                break
            if await self.get_session(user_id, session_id) is None:
                break
        else:
            raise DeletionIncompleteError(user_id)
        await self._drain_cleanup(user_id)

    async def _delete(self, user_id: str, session_id: str) -> bool:
        """
        刪除對話文件、它的 origin 對應與計數，並留下清除訊息用的 marker。

        不用交易：先讀，再以 batch 搭配前置條件（`last_update_time` 必須和讀到的一致）寫入。
        對話在這之間被別人刪除或修改時整批都不會生效，回傳 False，所以並行淘汰同一則對話也安全，
        也不會像交易那樣互卡讀鎖。已不存在或不屬於該 user 時什麼都不做。
        """
        reference = self._conversations().document(session_id)
        doc = await reference.get()
        data = doc.to_dict() or {}
        if not doc.exists or data.get("userId") != user_id:
            return False

        batch = self._db.batch()
        batch.delete(reference, option=self._db.write_option(last_update_time=doc.update_time))
        origin = (data.get("metadata") or {}).get("origin")
        if origin:
            origin_ref = self._origin(user_id, origin)
            origin_doc = await origin_ref.get()
            if origin_doc.exists and origin_doc.get("conversationId") == session_id:
                batch.delete(
                    origin_ref,
                    option=self._db.write_option(last_update_time=origin_doc.update_time),
                )
        batch.set(self._user(user_id), {"conversationCount": firestore.Increment(-1)}, merge=True)
        batch.set(
            self._cleanup(user_id).document(session_id),
            {"createdAt": firestore.SERVER_TIMESTAMP},
        )
        try:
            await batch.commit()
        except FailedPrecondition, NotFound, Conflict:
            return False
        return True

    async def delete_all_sessions(self, user_id: str) -> None:
        """
        刪除該 user 的所有對話與訊息，包括已被淘汰、訊息還沒清完的對話（cleanup marker）。

        刪完重新查詢確認沒有剩下的對話與訊息，最多重試 DELETE_ATTEMPTS 次，仍有殘留就拋出
        DeletionIncompleteError。origin 與 cleanup 文件隨 user 文件一起刪除。
        """
        seen: set[str] = set()
        for attempt in range(DELETE_ATTEMPTS + 1):
            owned = [doc.id async for doc in self._owned(user_id).stream()]
            seen.update(owned)
            seen.update([doc.id async for doc in self._cleanup(user_id).stream()])
            leftovers = set(owned)
            for session_id in seen - leftovers:
                if await self._messages(session_id).limit(1).get():
                    leftovers.add(session_id)
            if not leftovers:
                return
            if attempt == DELETE_ATTEMPTS:
                break
            for session_id in leftovers:
                # 文件已不存在時也會刪除底下的 messages
                await self._db.recursive_delete(self._conversations().document(session_id))
        raise DeletionIncompleteError(user_id)

    async def _drain_cleanup(self, user_id: str) -> None:
        """
        清除已刪除對話留下的訊息。

        marker 在對話被刪除的同一個交易裡寫入，程序中斷或清理失敗都會留著，下次再試；
        對話 id 是隨機值、不會重複使用，多個實例同時清理也安全。
        """
        query = self._cleanup(user_id).order_by("createdAt").limit(MAX_SESSIONS_PER_USER)
        try:
            async for marker in query.stream():
                try:
                    messages = self._messages(marker.id)
                    await self._db.recursive_delete(messages)
                    # BulkWriter 個別寫入的重試用盡時不一定會拋出例外
                    if await messages.limit(1).get():
                        logger.warning("Conversation cleanup deferred: messages remain")
                        continue
                    await marker.reference.delete()
                except Exception as error:  # noqa: BLE001 -- 對話已刪除，清理可下次再做
                    logger.warning("Conversation cleanup deferred: {}", type(error).__name__)
        except Exception as error:  # noqa: BLE001 -- 列出 marker 失敗時保留 marker
            logger.warning("Conversation cleanup deferred: {}", type(error).__name__)

    # -- messages --
    @staticmethod
    def _message_from(doc) -> Message:
        data = doc.to_dict() or {}
        return Message(
            id=doc.id,
            role=data.get("role", "assistant"),
            content=data.get("content", ""),
            created_at=data.get("createdAt") or now_utc(),
            tool_calls=[ToolCall(**tc) for tc in data.get("toolCalls", [])],
        )

    async def list_messages(self, user_id, session_id, limit) -> list[Message]:
        if await self.get_session(user_id, session_id) is None:
            return []
        query = (
            self._messages(session_id)
            .order_by("createdAt", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        messages = [self._message_from(doc) async for doc in query.stream()]
        messages.reverse()
        return messages

    async def add_message(self, user_id, session_id, role, content, tool_calls=None, meta=None):
        now = now_utc()
        meta = meta or MessageMeta()
        reference = self._messages(session_id).document(new_id())
        payload = {
            "role": role,
            "contentType": "text",
            "content": content,
            "moduleId": None,
            "model": meta.model,
            "promptVersion": meta.prompt_version,
            "toolCalls": [tc.model_dump() for tc in (tool_calls or [])],
            "tokenUsage": meta.token_usage.model_dump() if meta.token_usage else None,
            "latencyMs": meta.latency_ms,
            "createdAt": now,
        }
        batch = self._db.batch()
        batch.set(reference, payload)
        batch.update(
            self._conversations().document(session_id),
            {"lastMessageAt": now, "messageCount": firestore.Increment(1)},
        )
        await batch.commit()
        return Message(
            id=reference.id,
            role=role,
            content=content,
            created_at=now,
            tool_calls=tool_calls or [],
        )
