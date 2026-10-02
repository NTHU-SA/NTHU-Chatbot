"""外部身分 → 內部 user 的解析。"""

from __future__ import annotations

import time
from collections import OrderedDict

from src.application.models.identity import (
    AccountDisabledError,
    Principal,
    VerifiedIdentity,
    lookup_key,
)
from src.application.services.user_store import UserStore

# 對應關係只有在解除連結時才會變，快取 10 分鐘；帳號狀態快取 60 秒，封鎖能很快生效
MAPPING_TTL_SECONDS = 600
STATUS_TTL_SECONDS = 60


class IdentityService:
    """
    把 Authenticator（或 LINE webhook 簽章）驗證過的外部身分解析成內部 user。

    第一次見到的身分會建立新 user。webhook 與 LIFF 共用同一份對應，
    前提是 LINE Login channel 與 Messaging API channel 在同一個 Provider（`sub` 相同）。
    """

    def __init__(self, store: UserStore, max_cache: int = 10_000) -> None:
        self._store = store
        self._mapping: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._status: dict[str, tuple[float, str]] = {}
        self._max_cache = max_cache

    async def resolve(self, identity: VerifiedIdentity) -> Principal:
        key = lookup_key(identity.provider, identity.provider_user_id)
        now = time.monotonic()
        cached = self._mapping.get(key)
        if cached and cached[0] > now and await self._check_status(cached[1], now):
            user_id = cached[1]
            self._mapping.move_to_end(key)
        else:
            # resolve_or_create 本身會檢查狀態並在停用時拋出例外
            user_id, _ = await self._store.resolve_or_create(identity)
            self._remember(key, user_id, now)
            self._status[user_id] = (now + STATUS_TTL_SECONDS, "active")
        return Principal(
            user_id=user_id,
            provider=identity.provider,
            display_name=identity.display_name,
            picture_url=identity.picture_url,
        )

    async def resolve_line_user(self, line_user_id: str) -> str:
        """LINE webhook 的 userId（已由簽章驗證）→ 內部 user id。"""
        principal = await self.resolve(
            VerifiedIdentity(provider="line", provider_user_id=line_user_id)
        )
        return principal.user_id

    def forget(self, identity: VerifiedIdentity) -> None:
        """解除連結後清掉本實例的快取。"""
        self._mapping.pop(lookup_key(identity.provider, identity.provider_user_id), None)

    def forget_user(self, user_id: str) -> None:
        """刪除資料後清掉本實例指向這個 user 的所有快取。"""
        for key in [k for k, (_, uid) in self._mapping.items() if uid == user_id]:
            del self._mapping[key]
        self._status.pop(user_id, None)

    async def _check_status(self, user_id: str, now: float) -> bool:
        """
        快取的對應是否仍可用。

        被封鎖或刪除中 → 拋出 AccountDisabledError；user 已被完全刪除（文件不存在）→ 回傳 False，
        呼叫端改走 store 重新解析，同一個外部身分會得到全新的 user。
        """
        cached = self._status.get(user_id)
        if cached and cached[0] > now:
            status = cached[1]
        else:
            status = await self._store.get_status(user_id)
            if status is None:
                self._status.pop(user_id, None)
                return False
            self._status[user_id] = (now + STATUS_TTL_SECONDS, status)
        if status != "active":
            raise AccountDisabledError(user_id)
        return True

    def _remember(self, key: str, user_id: str, now: float) -> None:
        self._mapping[key] = (now + MAPPING_TTL_SECONDS, user_id)
        self._mapping.move_to_end(key)
        while len(self._mapping) > self._max_cache:
            self._mapping.popitem(last=False)
        if len(self._status) > self._max_cache:
            self._status.clear()
