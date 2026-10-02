"""
Firestore 使用者與外部身分儲存。

資料結構（欄位名稱依 firebase.spec）：
  identityLookup/{sha256(provider:id)}      userId / provider / createdAt
  users/{uid}                               status / displayName / pictureUrl / createdAt / updatedAt /
                                            lastActiveAt / lastConversationId / lastModuleId /
                                            lastModuleUsedAt / conversationCount
  users/{uid}/identities/{provider}         provider / providerUserId / linkedAt / lastLoginAt /
                                            updatedAt / metadata{provider 專屬欄位}
  users/{uid}/auditLog/{id}                 action / provider / at / expiresAt（TTL）
  users/{uid}/moduleStates/{moduleId}       lastUsedAt / usageCount / updatedAt
  users/{uid}/usage/{YYYY-MM-DD}            count / expiresAt（TTL）
  users/{uid}/consents/{type}_v{version}    type / version / status / acceptedAt / revokedAt / source
  users/{uid}/preferences/{key}             value / source(user|assistant) / updatedAt（nickname、department）
  users/{uid}/memory/{id}                   type / value / sourceConversationId / createdAt / updatedAt

瀏覽器永遠不直接碰 Firestore（rules 全部拒絕），這裡以 Admin SDK / ADC 存取。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from google.api_core.exceptions import (
    AlreadyExists,
    Conflict,
    FailedPrecondition,
    NotFound,
)
from google.cloud import firestore

from src.application.models.identity import (
    AccountDisabledError,
    IdentityConflictError,
    LastIdentityError,
    Principal,
    VerifiedIdentity,
    consent_doc_id,
    lookup_key,
    new_user_id,
)
from src.application.models.profile import (
    MAX_MEMORIES,
    ONBOARDING_MODULE,
    MemoryItem,
    MemoryLimitError,
    Profile,
)
from src.application.services.chat_store import new_id, now_utc

TRANSACTION_ATTEMPTS = 10
USAGE_RETENTION = timedelta(days=8)
AUDIT_RETENTION = timedelta(days=365)


class FirestoreUserStore:
    def __init__(self, db: firestore.AsyncClient):
        self._db = db

    # -- refs --
    def _user(self, user_id: str):
        return self._db.collection("users").document(user_id)

    def _identity(self, user_id: str, provider: str):
        return self._user(user_id).collection("identities").document(provider)

    def _lookup(self, provider: str, provider_user_id: str):
        return self._db.collection("identityLookup").document(
            lookup_key(provider, provider_user_id)
        )

    def _audit(self, user_id: str):
        return self._user(user_id).collection("auditLog").document(new_id())

    @staticmethod
    def _identity_payload(identity: VerifiedIdentity) -> dict:
        return {
            "provider": identity.provider,
            "providerUserId": identity.provider_user_id,
            "linkedAt": firestore.SERVER_TIMESTAMP,
            "lastLoginAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "metadata": {},
        }

    def _audit_payload(self, action: str, provider: str | None = None, **extra) -> dict:
        payload = {
            "action": action,
            "at": firestore.SERVER_TIMESTAMP,
            "expiresAt": now_utc() + AUDIT_RETENTION,
            **extra,
        }
        if provider is not None:
            payload["provider"] = provider
        return payload

    # -- resolve --
    async def resolve_or_create(self, identity: VerifiedIdentity) -> tuple[str, bool]:
        lookup = self._lookup(identity.provider, identity.provider_user_id)
        snapshot = await lookup.get()
        if snapshot.exists:
            user_id = snapshot.get("userId")
            if await self.get_status(user_id) is not None:
                await self._ensure_active(user_id)
                return user_id, False
            # 指向的 user 已被刪除（刪除流程中斷）：移除過期的 lookup，建立新的 user
            try:
                await lookup.delete(
                    option=self._db.write_option(last_update_time=snapshot.update_time)
                )
            except (FailedPrecondition, NotFound):
                pass
        try:
            return await self._create(identity, lookup)
        except (AlreadyExists, Conflict):
            # 另一個請求剛好同時建立了同一個身分：沿用它建立的 user
            snapshot = await lookup.get()
            user_id = snapshot.get("userId")
            await self._ensure_active(user_id)
            return user_id, False

    async def _create(self, identity: VerifiedIdentity, lookup) -> tuple[str, bool]:
        """
        一次原子 batch 寫入 lookup、user 與 identity，全部用 `create()`（文件已存在就失敗）。

        不用「先讀再寫」的交易：同一個身分的多個並行請求會互卡讀鎖升級。
        lookup 已被別人建立時整批都不會寫入，由呼叫端讀回既有的 user。
        """
        user_id = new_user_id()
        user = self._user(user_id)
        batch = self._db.batch()
        batch.create(
            lookup,
            {
                "userId": user_id,
                "provider": identity.provider,
                "createdAt": firestore.SERVER_TIMESTAMP,
            },
        )
        batch.create(
            user,
            {
                "status": "active",
                "displayName": identity.display_name,
                "pictureUrl": identity.picture_url,
                "createdAt": firestore.SERVER_TIMESTAMP,
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "lastActiveAt": firestore.SERVER_TIMESTAMP,
                "conversationCount": 0,
            },
        )
        batch.create(self._identity(user_id, identity.provider), self._identity_payload(identity))
        await batch.commit()
        return user_id, True

    async def _ensure_active(self, user_id: str) -> None:
        if await self.get_status(user_id) != "active":
            raise AccountDisabledError(user_id)

    async def get_status(self, user_id: str) -> str | None:
        snapshot = await self._user(user_id).get(field_paths=["status"])
        if not snapshot.exists:
            return None
        return (snapshot.to_dict() or {}).get("status", "active")

    # -- linking --
    async def link_identity(self, user_id: str, identity: VerifiedIdentity) -> None:
        user = self._user(user_id)
        lookup = self._lookup(identity.provider, identity.provider_user_id)
        identity_ref = self._identity(user_id, identity.provider)

        @firestore.async_transactional
        async def link(transaction):
            user_snapshot = await user.get(transaction=transaction)
            lookup_snapshot = await lookup.get(transaction=transaction)
            identity_snapshot = await identity_ref.get(transaction=transaction)
            status = (user_snapshot.to_dict() or {}).get("status") if user_snapshot.exists else None
            if status != "active":
                raise AccountDisabledError(user_id)
            if lookup_snapshot.exists:
                if lookup_snapshot.get("userId") == user_id:
                    return
                # 不透露屬於哪個 user
                raise IdentityConflictError(identity.provider)
            if identity_snapshot.exists:
                raise IdentityConflictError(identity.provider)
            transaction.create(
                lookup,
                {
                    "userId": user_id,
                    "provider": identity.provider,
                    "createdAt": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.create(identity_ref, self._identity_payload(identity))
            transaction.create(self._audit(user_id), self._audit_payload("link", identity.provider))

        await link(self._db.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    async def unlink_identity(self, user_id: str, provider: str) -> None:
        identities = self._user(user_id).collection("identities")

        @firestore.async_transactional
        async def unlink(transaction):
            linked = {doc.id: doc async for doc in identities.stream(transaction=transaction)}
            if provider not in linked:
                return
            if len(linked) <= 1:
                raise LastIdentityError(provider)
            record = linked[provider].to_dict() or {}
            transaction.delete(linked[provider].reference)
            transaction.delete(self._lookup(provider, record["providerUserId"]))
            transaction.create(self._audit(user_id), self._audit_payload("unlink", provider))

        await unlink(self._db.transaction(max_attempts=TRANSACTION_ATTEMPTS))

    # -- activity --
    async def touch_activity(self, user_id: str) -> None:
        await self._user(user_id).set(
            {"lastActiveAt": firestore.SERVER_TIMESTAMP}, merge=True
        )

    async def record_login(self, user: Principal, metadata: dict | None = None) -> None:
        """LIFF 頁面開啟時更新顯示資料與登入時間；顯示資料只採用驗證過的 claims。"""
        batch = self._db.batch()
        batch.set(
            self._user(user.user_id),
            {
                "displayName": user.display_name,
                "pictureUrl": user.picture_url,
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "lastActiveAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        identity_update = {
            "lastLoginAt": firestore.SERVER_TIMESTAMP,
            "updatedAt": firestore.SERVER_TIMESTAMP,
            "metadata": {
                "displayName": user.display_name,
                "pictureUrl": user.picture_url,
                **(metadata or {}),
            },
        }
        batch.set(self._identity(user.user_id, user.provider), identity_update, merge=True)
        await batch.commit()

    async def update_identity_metadata(self, user_id, provider, metadata) -> None:
        await self._identity(user_id, provider).set(
            {"metadata": metadata, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True
        )

    async def record_module_use(self, user_id: str, module_id: str) -> None:
        batch = self._db.batch()
        batch.set(
            self._user(user_id).collection("moduleStates").document(module_id),
            {
                "lastUsedAt": firestore.SERVER_TIMESTAMP,
                "usageCount": firestore.Increment(1),
                "updatedAt": firestore.SERVER_TIMESTAMP,
            },
            merge=True,
        )
        batch.set(
            self._user(user_id),
            {"lastModuleId": module_id, "lastModuleUsedAt": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
        await batch.commit()

    # -- quota --
    async def consume_daily_quota(self, user_id: str, limit: int) -> bool:
        """
        伺服器端原子遞增後讀回比較：不用交易鎖，尖峰時不會因競爭失敗。

        在邊界上同時到達的兩個請求可能都被拒絕，但絕不會都被允許。
        每天一份文件（取代原本無限長大的 usage map），`expiresAt` 由 TTL 自動清除。
        """
        now = now_utc()
        reference = (
            self._user(user_id).collection("usage").document(now.strftime("%Y-%m-%d"))
        )
        await reference.set(
            {"count": firestore.Increment(1), "expiresAt": now + USAGE_RETENTION},
            merge=True,
        )
        snapshot = await reference.get(field_paths=["count"])
        return (snapshot.to_dict() or {}).get("count", 0) <= limit

    # -- consent --
    async def has_consent(self, user_id: str, consent_type: str, version: str) -> bool:
        snapshot = await (
            self._user(user_id)
            .collection("consents")
            .document(consent_doc_id(consent_type, version))
            .get(field_paths=["status"])
        )
        return snapshot.exists and (snapshot.to_dict() or {}).get("status") == "accepted"

    async def set_consent(self, user_id, consent_type, version, accepted, source) -> None:
        doc_id = consent_doc_id(consent_type, version)
        payload = {
            "type": consent_type,
            "version": version,
            "status": "accepted" if accepted else "revoked",
            "source": source,
            "acceptedAt" if accepted else "revokedAt": firestore.SERVER_TIMESTAMP,
        }
        batch = self._db.batch()
        batch.set(self._user(user_id).collection("consents").document(doc_id), payload, merge=True)
        batch.create(
            self._audit(user_id),
            self._audit_payload("consent" if accepted else "revoke", document=doc_id),
        )
        await batch.commit()

    # -- personalisation --
    async def get_profile(self, user_id: str) -> Profile:
        user = self._user(user_id)
        prefs, memories, onboarding = await asyncio.gather(
            _collect(user.collection("preferences").stream()),
            _collect(user.collection("memory").order_by("createdAt").limit(MAX_MEMORIES).stream()),
            user.collection("moduleStates").document(ONBOARDING_MODULE).get(),
        )
        values = {doc.id: (doc.to_dict() or {}).get("value") for doc in prefs}
        return Profile(
            nickname=values.get("nickname"),
            department=values.get("department"),
            memories=[
                MemoryItem(
                    id=doc.id,
                    value=(doc.to_dict() or {}).get("value", ""),
                    created_at=(doc.to_dict() or {}).get("createdAt"),
                )
                for doc in memories
            ],
            onboarding=(onboarding.to_dict() or {}).get("state") if onboarding.exists else None,
        )

    async def set_preference(self, user_id, key, value, source) -> None:
        await self._user(user_id).collection("preferences").document(key).set(
            {"value": value, "source": source, "updatedAt": firestore.SERVER_TIMESTAMP}
        )

    async def delete_preference(self, user_id, key) -> None:
        await self._user(user_id).collection("preferences").document(key).delete()

    async def add_memory(self, user_id, value, source_conversation_id=None) -> MemoryItem:
        collection = self._user(user_id).collection("memory")
        aggregate = await collection.count().get()
        if (aggregate[0][0].value if aggregate else 0) >= MAX_MEMORIES:
            raise MemoryLimitError(user_id)
        now = now_utc()
        reference = collection.document(new_id())
        await reference.create(
            {
                "type": "fact",
                "value": value,
                "sourceConversationId": source_conversation_id,
                "createdAt": now,
                "updatedAt": now,
            }
        )
        return MemoryItem(id=reference.id, value=value, created_at=now)

    async def delete_memory(self, user_id, memory_id) -> bool:
        reference = self._user(user_id).collection("memory").document(memory_id)
        if not (await reference.get()).exists:
            return False
        await reference.delete()
        return True

    async def set_onboarding(self, user_id, state) -> None:
        await self._user(user_id).collection("moduleStates").document(ONBOARDING_MODULE).set(
            {"state": state, "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True
        )

    # -- deletion --
    async def delete_user(self, user_id: str) -> None:
        """
        刪除 user 的所有資料。

        1. 先標記 `status=deleted`：其他實例的狀態快取過期後立刻擋下這個帳號；
        2. 刪除外部身分的 lookup：同一個 LINE 帳號之後登入會是全新的 user；
        3. 遞迴刪除 user 文件與所有子集合（identities、consents、auditLog、usage、moduleStates…）。
        """
        user = self._user(user_id)
        await user.set({"status": "deleted", "updatedAt": firestore.SERVER_TIMESTAMP}, merge=True)
        batch = self._db.batch()
        async for doc in user.collection("identities").stream():
            data = doc.to_dict() or {}
            if data.get("provider") and data.get("providerUserId"):
                batch.delete(self._lookup(data["provider"], data["providerUserId"]))
        await batch.commit()
        await self._db.recursive_delete(user)


async def _collect(stream) -> list:
    return [doc async for doc in stream]
