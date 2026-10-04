"""
使用者與外部身分儲存介面。

`UserStore` 管理 `users/{uid}`、`users/{uid}/identities/*`、`identityLookup/*` 與每日額度；
`MemoryUserStore` 供測試與本機開發，規則與 Firestore 實作一致：

- 外部身分 → 內部 user 的對應只能以「建立」寫入：同一個外部身分不可能屬於兩個 user。
- 連結第二個身分只能明確進行（呼叫端必須同時驗證過兩個身分），絕不以 email、名稱或學號自動合併。
- 至少保留一個身分；連結與解除都會寫稽核紀錄。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Protocol

from src.application.models.identity import (
    ACTIVE,
    DELETED,
    DELETING,
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


class UserStore(Protocol):
    async def resolve_or_create(self, identity: VerifiedIdentity) -> tuple[str, bool]:
        """
        外部身分 → 內部 user id；沒有就建立 user、identity 與 lookup（同一個交易）。

        回傳 (user_id, created)。帳號被封鎖或刪除中時拋出 AccountDisabledError（帶 status）；
        已刪除（墓碑）的 user 視為不存在，建立全新的 user。
        """
        ...

    async def get_status(self, user_id: str) -> str | None: ...

    async def link_identity(self, user_id: str, identity: VerifiedIdentity) -> None:
        """把另一個已驗證的外部身分連結到既有 user；已屬於別人時拋出 IdentityConflictError。"""
        ...

    async def unlink_identity(self, user_id: str, provider: str) -> None:
        """解除連結；只剩一個身分時拋出 LastIdentityError。"""
        ...

    async def touch_activity(self, user_id: str) -> None: ...

    async def record_login(self, user: Principal, metadata: dict[str, Any] | None = None) -> None:
        """更新顯示資料（只採用驗證過的 claims）、登入時間與 provider 專屬 metadata。"""
        ...

    async def update_identity_metadata(
        self, user_id: str, provider: str, metadata: dict[str, Any]
    ) -> None:
        """合併寫入 provider 專屬資料（例如 LINE 的 followed）。"""
        ...

    async def record_module_use(self, user_id: str, module_id: str) -> None: ...

    async def consume_daily_quota(self, user_id: str, limit: int) -> bool:
        """計入今日一則 LLM 訊息；超過上限時回傳 False。"""
        ...

    async def has_consent(self, user_id: str, consent_type: str, version: str) -> bool:
        """該版本是否為「已同意」。"""
        ...

    async def set_consent(self, user_id: str, consent_type: str, version: str, source: str) -> None:
        """同意某一版本；每個版本一份文件，不覆蓋其他版本，並寫稽核紀錄。"""
        ...

    async def get_profile(self, user_id: str) -> Profile:
        """稱呼、系所、記住的事與首次使用的引導狀態。"""
        ...

    async def set_preference(self, user_id: str, key: str, value: str, source: str) -> None:
        """寫入一項偏好；source 為 user（設定頁）或 assistant（對話中由模型記下）。"""
        ...

    async def delete_preference(self, user_id: str, key: str) -> None: ...

    async def set_preferences(
        self, user_id: str, values: dict[str, str | None], source: str
    ) -> None:
        """一次寫入多項偏好（值為 None 代表清除），全部成功或全部不變。"""
        ...

    async def add_memory(
        self, user_id: str, value: str, source_conversation_id: str | None = None
    ) -> MemoryItem:
        """新增一則記憶；達到上限時拋出 MemoryLimitError。"""
        ...

    async def delete_memory(self, user_id: str, memory_id: str) -> bool: ...

    async def set_onboarding(self, user_id: str, state: str) -> None: ...

    async def begin_deletion(self, user_id: str) -> None:
        """標記 status=deleting：之後只能再呼叫刪除（失敗時可以重試）。"""
        ...

    async def delete_user(self, user_id: str) -> None:
        """
        刪除這個 user 的所有資料：子集合、外部身分與 lookup；user 文件換成不含個資的墓碑。

        之後同一個 LINE 帳號再登入會得到全新的 user。對話由 ChatStore 另外刪除。
        沒有全部刪除時拋出 DeletionIncompleteError，帳號維持 deleting。
        """
        ...


def _merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class MemoryUserStore:
    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.identities: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.lookup: dict[str, str] = {}
        self.audit: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.module_states: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.consents: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.preferences: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.memories: dict[str, dict[str, MemoryItem]] = defaultdict(dict)
        self._usage: dict[tuple[str, str], int] = defaultdict(int)
        # lookup key → (day, count)：刪除帳號後保留當日用量（同 Firestore 的 quotaCarryover）
        self.quota_carryover: dict[str, tuple[str, int]] = {}
        self._lock = asyncio.Lock()

    def _check_active(self, user_id: str) -> None:
        status = self.users.get(user_id, {}).get("status", ACTIVE)
        if status != ACTIVE:
            raise AccountDisabledError(user_id, status)

    async def resolve_or_create(self, identity: VerifiedIdentity) -> tuple[str, bool]:
        key = lookup_key(identity.provider, identity.provider_user_id)
        async with self._lock:
            owner = self.users.get(self.lookup.get(key, ""))
            if owner is not None and owner.get("status") != DELETED:
                user_id = self.lookup[key]
                self._check_active(user_id)
                return user_id, False
            user_id = new_user_id()
            now = now_utc()
            self.lookup[key] = user_id
            self.users[user_id] = {
                "status": "active",
                "displayName": identity.display_name,
                "pictureUrl": identity.picture_url,
                "createdAt": now,
                "conversationCount": 0,
            }
            self.identities[user_id][identity.provider] = {
                "provider": identity.provider,
                "providerUserId": identity.provider_user_id,
                "linkedAt": now,
                "metadata": {},
            }
            day, count = self.quota_carryover.get(key, ("", 0))
            if day == now.strftime("%Y-%m-%d"):
                self._usage[(user_id, day)] = count
            return user_id, True

    async def get_status(self, user_id: str) -> str | None:
        user = self.users.get(user_id)
        return user.get("status") if user else None

    async def link_identity(self, user_id: str, identity: VerifiedIdentity) -> None:
        key = lookup_key(identity.provider, identity.provider_user_id)
        async with self._lock:
            self._check_active(user_id)
            owner = self.lookup.get(key)
            if owner == user_id:
                return
            if owner is not None or identity.provider in self.identities[user_id]:
                raise IdentityConflictError(identity.provider)
            self.lookup[key] = user_id
            self.identities[user_id][identity.provider] = {
                "provider": identity.provider,
                "providerUserId": identity.provider_user_id,
                "linkedAt": now_utc(),
                "metadata": {},
            }
            self.audit[user_id].append({"action": "link", "provider": identity.provider})

    async def unlink_identity(self, user_id: str, provider: str) -> None:
        async with self._lock:
            linked = self.identities[user_id]
            if provider not in linked:
                return
            if len(linked) <= 1:
                raise LastIdentityError(provider)
            record = linked.pop(provider)
            self.lookup.pop(lookup_key(provider, record["providerUserId"]), None)
            self.audit[user_id].append({"action": "unlink", "provider": provider})

    async def touch_activity(self, user_id: str) -> None:
        if user_id in self.users:
            self.users[user_id]["lastActiveAt"] = now_utc()

    async def record_login(self, user, metadata=None) -> None:
        if user.user_id in self.users:
            self.users[user.user_id].update(
                displayName=user.display_name, pictureUrl=user.picture_url
            )
        await self.update_identity_metadata(
            user.user_id,
            user.provider,
            {
                "displayName": user.display_name,
                "pictureUrl": user.picture_url,
                **(metadata or {}),
            },
        )

    async def update_identity_metadata(self, user_id, provider, metadata) -> None:
        record = self.identities[user_id].get(provider)
        if record is not None:
            record["metadata"] = _merge(record.get("metadata", {}), metadata)

    async def record_module_use(self, user_id: str, module_id: str) -> None:
        now = now_utc()
        state = self.module_states[user_id].setdefault(module_id, {"usageCount": 0})
        state["usageCount"] += 1
        state["lastUsedAt"] = now
        if user_id in self.users:
            self.users[user_id].update(lastModuleId=module_id, lastModuleUsedAt=now)

    async def consume_daily_quota(self, user_id: str, limit: int) -> bool:
        key = (user_id, now_utc().strftime("%Y-%m-%d"))
        async with self._lock:
            if self._usage[key] >= limit:
                return False
            self._usage[key] += 1
            return True

    async def has_consent(self, user_id, consent_type, version) -> bool:
        record = self.consents[user_id].get(consent_doc_id(consent_type, version))
        return bool(record) and record["status"] == "accepted"

    async def set_consent(self, user_id, consent_type, version, source) -> None:
        doc_id = consent_doc_id(consent_type, version)
        record = self.consents[user_id].setdefault(
            doc_id, {"type": consent_type, "version": version}
        )
        record.update(status="accepted", source=source)
        record["acceptedAt"] = now_utc()
        self.audit[user_id].append({"action": "consent", "document": doc_id})

    async def get_profile(self, user_id: str) -> Profile:
        prefs = self.preferences[user_id]
        state = self.module_states[user_id].get(ONBOARDING_MODULE, {})
        return Profile(
            nickname=(prefs.get("nickname") or {}).get("value"),
            department=(prefs.get("department") or {}).get("value"),
            memories=list(self.memories[user_id].values()),
            onboarding=state.get("state"),
        )

    async def set_preference(self, user_id, key, value, source) -> None:
        self.preferences[user_id][key] = {"value": value, "source": source, "updatedAt": now_utc()}

    async def delete_preference(self, user_id, key) -> None:
        self.preferences[user_id].pop(key, None)

    async def set_preferences(self, user_id, values, source) -> None:
        for key, value in values.items():
            if value is None:
                await self.delete_preference(user_id, key)
            else:
                await self.set_preference(user_id, key, value, source)

    async def add_memory(self, user_id, value, source_conversation_id=None) -> MemoryItem:
        async with self._lock:
            if len(self.memories[user_id]) >= MAX_MEMORIES:
                raise MemoryLimitError(user_id)
            item = MemoryItem(id=new_id(), value=value, created_at=now_utc())
            self.memories[user_id][item.id] = item
            return item

    async def delete_memory(self, user_id, memory_id) -> bool:
        return self.memories[user_id].pop(memory_id, None) is not None

    async def set_onboarding(self, user_id, state) -> None:
        self.module_states[user_id].setdefault(ONBOARDING_MODULE, {})["state"] = state

    async def begin_deletion(self, user_id: str) -> None:
        self.users.setdefault(user_id, {})["status"] = DELETING

    async def delete_user(self, user_id: str) -> None:
        async with self._lock:
            day = now_utc().strftime("%Y-%m-%d")
            used = self._usage.get((user_id, day), 0)
            for record in self.identities.pop(user_id, {}).values():
                key = lookup_key(record["provider"], record["providerUserId"])
                if used:
                    self.quota_carryover[key] = (day, used)
                self.lookup.pop(key, None)
            for store in (
                self.audit,
                self.module_states,
                self.consents,
                self.preferences,
                self.memories,
            ):
                store.pop(user_id, None)
            for key in [k for k in self._usage if k[0] == user_id]:
                del self._usage[key]
            self.users[user_id] = {"status": DELETED}
