"""外部身分 → 內部 user 的解析。"""

from __future__ import annotations

import time
from collections import OrderedDict

from loguru import logger

from src.application.models.identity import (
    ACTIVE,
    DELETED,
    DELETING,
    NTHUSA_PROVIDER,
    AccountDisabledError,
    DeletionIncompleteError,
    IdentityConflictError,
    Principal,
    VerifiedIdentity,
    lookup_key,
)
from src.application.services.chat_store import ChatStore
from src.application.services.erasure import erase_user
from src.application.services.user_store import UserStore

# 對應關係只有在解除連結時才會變，快取 10 分鐘；帳號狀態快取 60 秒，封鎖能很快生效
MAPPING_TTL_SECONDS = 600
STATUS_TTL_SECONDS = 60


class IdentityService:
    """
    把 Authenticator（或 LINE webhook 簽章）驗證過的外部身分解析成內部 user。

    第一次見到的身分會建立新 user。webhook 與 LIFF 共用同一份對應，
    前提是 LINE Login channel 與 Messaging API channel 在同一個 Provider（`sub` 相同）。

    身分帶有 `linked`（Auth0 token 裡 NTHUSA ID 綁定的 LINE）時：
    - 第一次見到這個 NTHUSA ID、而 LINE 已經有 user（之前用 LINE 登入或傳過訊息）：沿用那個 user，
      對話與設定都保留；
    - 之後以 IdP 為準，把 LINE 對應到這個 user。LINE 原本屬於另一個沒有 NTHUSA ID 的 user
      （只在 LINE 用過的孤兒帳號）時，刪除那個 user 的資料；被封鎖或刪除中的帳號則不移動。
    """

    def __init__(
        self, store: UserStore, chats: ChatStore | None = None, max_cache: int = 10_000
    ) -> None:
        self._store = store
        self._chats = chats
        self._mapping: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._status: dict[str, tuple[float, str]] = {}
        self._max_cache = max_cache

    async def resolve(
        self, identity: VerifiedIdentity, *, allow_deleting: bool = False
    ) -> Principal:
        """
        外部身分 → Principal。帳號被封鎖或刪除中時拋出 AccountDisabledError；

        `allow_deleting=True` 只給刪除 API 使用：刪除中途失敗時，使用者仍能再呼叫一次刪除。
        """
        try:
            if identity.linked:
                await self._adopt_linked_user(identity)
            user_id = await self._resolve_user_id(identity)
        except AccountDisabledError as error:
            if not (allow_deleting and error.status == DELETING):
                raise
            user_id = error.args[0]
        else:
            await self._attach_linked(user_id, identity.linked)
        return Principal(
            user_id=user_id,
            provider=identity.provider,
            display_name=identity.display_name,
            picture_url=identity.picture_url,
        )

    async def _resolve_user_id(self, identity: VerifiedIdentity) -> str:
        key = lookup_key(identity.provider, identity.provider_user_id)
        now = time.monotonic()
        cached = self._mapping.get(key)
        if cached and cached[0] > now and await self._check_status(cached[1], now):
            self._mapping.move_to_end(key)
            return cached[1]
        # resolve_or_create 本身會檢查狀態並在停用時拋出例外
        user_id, _ = await self._store.resolve_or_create(identity)
        self._remember(key, user_id, now)
        self._status[user_id] = (now + STATUS_TTL_SECONDS, ACTIVE)
        return user_id

    async def _adopt_linked_user(self, identity: VerifiedIdentity) -> None:
        """主要身分還沒有 user 時，連結到 linked 身分已經有的 user（而不是另外建立一個）。"""
        if self._cached(lookup_key(identity.provider, identity.provider_user_id)):
            return
        if await self._store.find_user(identity) is not None:
            return
        for other in identity.linked:
            owner = await self._store.find_user(other)
            if owner is None:
                continue
            try:
                await self._store.link_identity(owner, identity)
            except IdentityConflictError:
                pass  # 並行的請求已經替它建立或連結了 user：交給 resolve 讀回
            return

    async def _attach_linked(self, user_id: str, linked: tuple[VerifiedIdentity, ...]) -> None:
        now = time.monotonic()
        for other in linked:
            key = lookup_key(other.provider, other.provider_user_id)
            if self._cached(key) == user_id:
                continue
            owner = await self._store.find_user(other)
            if owner not in (None, user_id) and await self._store.get_status(owner) != ACTIVE:
                # 被封鎖或刪除中的帳號不移走：不能靠綁定繞過封鎖或刪掉它的紀錄
                continue
            previous = await self._store.move_identity(user_id, other)
            self._remember(key, user_id, now)
            if previous is None:
                continue
            self.forget_user(previous)
            if previous == owner and not await self._store.has_identity(previous, NTHUSA_PROVIDER):
                await self._erase_orphan(previous)

    async def _erase_orphan(self, user_id: str) -> None:
        if self._chats is None:
            return
        try:
            await erase_user(self._chats, self._store, user_id, lambda: self.forget_user(user_id))
        except DeletionIncompleteError:
            # 帳號停在 deleting（只能再刪一次）；不影響這次登入
            logger.error("Orphaned user deletion incomplete")
        else:
            logger.info("Orphaned user deleted after identity moved")

    def _cached(self, key: str) -> str | None:
        cached = self._mapping.get(key)
        return cached[1] if cached and cached[0] > time.monotonic() else None

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

        被封鎖或刪除中 → 拋出 AccountDisabledError；user 已刪除（墓碑或文件不存在）→ 回傳 False，
        呼叫端改走 store 重新解析，同一個外部身分會得到全新的 user。
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
