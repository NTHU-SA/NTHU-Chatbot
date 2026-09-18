"""LIFF id_token 驗證與限流。LINE webhook 簽章由 linebot SDK 的 WebhookParser 處理。"""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, Request, status
from loguru import logger

LINE_VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
LINE_ISSUER = "https://access.line.me"


@dataclass(frozen=True)
class LineUser:
    user_id: str
    display_name: str | None = None
    picture_url: str | None = None


class LiffTokenVerifier:
    """
    向 LINE 的 verify endpoint 驗證 LIFF id_token。

    userId 只取自驗證過的 `sub`，前端無法自行宣稱身分。
    驗證過的 token 以雜湊為 key 快取到 `exp`（最多一小時），LIFF 頁面不必每個請求都打 LINE。
    """

    def __init__(self, channel_id: str, http: httpx.AsyncClient, max_cache: int = 2048):
        self._channel_id = channel_id
        self._http = http
        self._cache: OrderedDict[str, tuple[float, LineUser]] = OrderedDict()
        self._max_cache = max_cache

    async def verify(self, id_token: str) -> LineUser:
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

        user = LineUser(
            user_id=sub,
            display_name=claims.get("name"),
            picture_url=claims.get("picture"),
        )
        exp = float(claims.get("exp", now + 60))
        self._cache[key] = (min(exp, now + 3600), user)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)
        return user


class RateLimiter:
    """
    單一實例的 token bucket 限流（防突發）。

    每日額度另外存在 chat store，才能跨 Cloud Run 實例。
    """

    def __init__(self, rate_per_minute: int = 20, burst: int = 10, max_keys: int = 10_000):
        self._rate = rate_per_minute / 60.0
        self._burst = float(burst)
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._max_keys = max_keys

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets.get(key, (self._burst, now))
        tokens = min(self._burst, tokens + (now - last) * self._rate)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return allowed


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


async def get_current_user(
    request: Request, token: str = Depends(_bearer_token)
) -> LineUser:
    verifier: LiffTokenVerifier = request.app.state.token_verifier
    user = await verifier.verify(token)
    limiter: RateLimiter = request.app.state.rate_limiter
    if not limiter.allow(user.user_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "slow down")
    return user
