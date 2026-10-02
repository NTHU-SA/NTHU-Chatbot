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
    AccountDisabledError,
    IdentityConflictError,
    LastIdentityError,
    Principal,
    VerifiedIdentity,
    lookup_key,
    new_user_id,
)
from src.application.services.chat_store import now_utc


class UserStore(Protocol):
    async def resolve_or_create(self, identity: VerifiedIdentity) -> tuple[str, bool]:
        """
        外部身分 → 內部 user id；沒有就建立 user、identity 與 lookup（同一個交易）。

        回傳 (user_id, created)。帳號被封鎖或刪除時拋出 AccountDisabledError。
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

    async def record_login(
        self, user: Principal, metadata: dict[str, Any] | None = None
    ) -> None:
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
        self._usage: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def _check_active(self, user_id: str) -> None:
        if self.users.get(user_id, {}).get("status", "active") != "active":
            raise AccountDisabledError(user_id)

    async def resolve_or_create(self, identity: VerifiedIdentity) -> tuple[str, bool]:
        key = lookup_key(identity.provider, identity.provider_user_id)
        async with self._lock:
            if key in self.lookup:
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
