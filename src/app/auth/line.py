"""LINE 登入（LIFF id_token）驗證。LINE webhook 簽章由 linebot SDK 的 WebhookParser 處理。"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict

import httpx
from fastapi import HTTPException, status
from loguru import logger

from src.application.models.identity import VerifiedIdentity

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
LINE_ISSUER = "https://access.line.me"


class LineLiffAuthenticator:
    """
    向 LINE 的 verify endpoint 驗證 LIFF id_token。

    `provider_user_id` 只取自驗證過的 `sub`，前端無法自行宣稱身分。
    驗證過的 token 以雜湊為 key 快取到 `exp`（最多一小時），LIFF 頁面不必每個請求都打 LINE。
    """

    provider = "line"

    def __init__(self, channel_id: str, http: httpx.AsyncClient, max_cache: int = 2048):
        self._channel_id = channel_id
        self._http = http
        self._cache: OrderedDict[str, tuple[float, VerifiedIdentity]] = OrderedDict()
        self._max_cache = max_cache

    async def verify(self, id_token: str) -> VerifiedIdentity:
        key = hashlib.sha256(id_token.encode("utf-8")).hexdigest()
        now = time.time()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            self._cache.move_to_end(key)
            return cached[1]

        try:
            response = await self._http.post(
                LINE_VERIFY_URL,
                data={"id_token": id_token, "client_id": self._channel_id},
                timeout=10,
            )
        except httpx.HTTPError as error:
            logger.warning("LINE verify endpoint unreachable: {}", type(error).__name__)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "auth backend unavailable"
            ) from error

        if response.status_code != 200:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid id_token")

        claims = response.json()
        if claims.get("aud") != self._channel_id or claims.get("iss") != LINE_ISSUER:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid id_token")
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid id_token")

        identity = VerifiedIdentity(
            provider=self.provider,
            provider_user_id=sub,
            display_name=claims.get("name"),
            picture_url=claims.get("picture"),
        )
        exp = float(claims.get("exp", now + 60))
        self._cache[key] = (min(exp, now + 3600), identity)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)
        return identity
