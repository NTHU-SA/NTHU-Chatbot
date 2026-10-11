"""登入身分（NTHUSA ID）→ user，以及 webhook 的 LINE userId → user。"""

from __future__ import annotations

import time
from collections import OrderedDict

from src.application.models.identity import (
    ACTIVE,
    DELETED,
    DELETING,
    LINKED_PROVIDERS,
    AccountDisabledError,
    Principal,
    VerifiedIdentity,
    lookup_key,
)
from src.application.services.user_store import UserStore

# 對應關係只有在 Auth0 改綁定時才會變，快取 10 分鐘；帳號狀態快取 60 秒，封鎖能很快生效
MAPPING_TTL_SECONDS = 600
STATUS_TTL_SECONDS = 60


class IdentityService:
    """
    把 Authenticator 驗證過的 NTHUSA ID 解析成 user（user id 就是 NTHUSA ID），第一次見到時建立。

    token 裡 NTHUSA ID 綁定的 LINE（`linked`）以 Auth0 為準同步到 `identityLookup`：
    綁了就對應到這個 user（原本屬於別的 user 就移過來），解除了就移除，webhook 靠它找到同一個人。
    前提是 Auth0 的 LINE 連線和 bot 的 Messaging API channel 在同一個 LINE Provider（userId 相同）。
    webhook 只查詢、不建立：沒有綁定 NTHUSA ID 的 LINE 使用者不會有任何個人資料。
    """

    def __init__(self, store: UserStore, max_cache: int = 10_000) -> None:
        self._store = store
        # lookup key → (到期時間, user id)：LINE userId 的對應，以及登入時已同步過的綁定
        self._mapping: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._status: dict[str, tuple[float, str]] = {}
        self._max_cache = max_cache

    async def resolve(
        self, identity: VerifiedIdentity, *, allow_deleting: bool = False
    ) -> Principal:
        """
        NTHUSA ID → Principal。帳號被封鎖、刪除中或剛刪除時拋出 AccountDisabledError；

        `allow_deleting=True` 只給刪除 API 使用：刪除中途失敗時，使用者仍能再呼叫一次刪除。
        """
        user_id = identity.provider_user_id
        try:
            await self._ensure_user(identity)
        except AccountDisabledError as error:
            if not (allow_deleting and error.status == DELETING):
                raise
        else:
            await self._sync_linked(user_id, identity.linked)
        return Principal(
            user_id=user_id,
            provider=identity.provider,
            display_name=identity.display_name,
            picture_url=identity.picture_url,
        )

    async def _ensure_user(self, identity: VerifiedIdentity) -> None:
        user_id = identity.provider_user_id
        now = time.monotonic()
        if await self._check_status(user_id, now):
            return
        # 沒有 user 或只剩墓碑：建立（store 會檢查狀態，停用時拋出例外）
        await self._store.get_or_create_user(identity)
        self._status[user_id] = (now + STATUS_TTL_SECONDS, ACTIVE)

    async def _sync_linked(self, user_id: str, linked: tuple[VerifiedIdentity, ...]) -> None:
        """讓 identityLookup 跟上 Auth0 的綁定；同一個綁定 10 分鐘內只同步一次。"""
        now = time.monotonic()
        for provider in LINKED_PROVIDERS:
            identity = next((item for item in linked if item.provider == provider), None)
            # 已同步的狀態：綁定的身分（或「沒有綁定」）→ user
            key = (
                lookup_key(provider, identity.provider_user_id)
                if identity
                else f"unlinked:{provider}:{user_id}"
            )
            if self._cached(key) == user_id:
                continue
            previous = await self._store.sync_identity(user_id, provider, identity)
            if previous is not None:
                self.forget_user(previous)
            self._remember(key, user_id, now)

    async def find_line_user(self, line_user_id: str) -> str | None:
        """
        LINE webhook 的 userId（已由簽章驗證）→ 綁定它的 user；沒有綁定時回傳 None（不建立）。

        帳號被封鎖或刪除中時拋出 AccountDisabledError。
        """
        key = lookup_key("line", line_user_id)
        now = time.monotonic()
        user_id = self._cached(key)
        if user_id is None:
            user_id = await self._store.find_user(
                VerifiedIdentity(provider="line", provider_user_id=line_user_id)
            )
            if user_id is None:
                return None
            self._remember(key, user_id, now)
        if not await self._check_status(user_id, now):
            self._mapping.pop(key, None)
            return None
        return user_id

    def _cached(self, key: str) -> str | None:
        cached = self._mapping.get(key)
        if cached and cached[0] > time.monotonic():
            self._mapping.move_to_end(key)
            return cached[1]
        return None

    def forget_user(self, user_id: str) -> None:
        """刪除資料後清掉本實例指向這個 user 的所有快取。"""
        for key in [k for k, (_, uid) in self._mapping.items() if uid == user_id]:
            del self._mapping[key]
        self._status.pop(user_id, None)

    async def _check_status(self, user_id: str, now: float) -> bool:
        """
        user 是否存在且可用。

        被封鎖或刪除中 → 拋出 AccountDisabledError；user 不存在或已刪除（墓碑）→ 回傳 False。
        """
        cached = self._status.get(user_id)
        if cached and cached[0] > now:
            status = cached[1]
        else:
            status = await self._store.get_status(user_id)
            if status in (None, DELETED):
                self._status.pop(user_id, None)
                return False
            self._status[user_id] = (now + STATUS_TTL_SECONDS, status)
        if status != ACTIVE:
            raise AccountDisabledError(user_id, status)
        return True

    def _remember(self, key: str, user_id: str, now: float) -> None:
        self._mapping[key] = (now + MAPPING_TTL_SECONDS, user_id)
        self._mapping.move_to_end(key)
        while len(self._mapping) > self._max_cache:
            self._mapping.popitem(last=False)
        if len(self._status) > self._max_cache:
            self._status.clear()
