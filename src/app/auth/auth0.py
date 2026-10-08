"""Auth0 登入（一般瀏覽器）的 access token 驗證。"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import OrderedDict

import httpx
import jwt
from fastapi import HTTPException, status
from loguru import logger

from src.application.models.identity import NTHUSA_PROVIDER, VerifiedIdentity

ALGORITHM = "RS256"
# post-login Action（infra/auth0/post-login-nthusa-id.js）放進 access token 的自訂 claim
CLAIM_NAMESPACE = "https://nthusa.tw/"
USR_CLAIM = CLAIM_NAMESPACE + "usr"
LINE_CLAIM = CLAIM_NAMESPACE + "line_user_id"
NTHUSA_ID = re.compile(r"^usr_[0-9A-HJKMNP-TV-Z]{26}$")
LINE_USER_ID = re.compile(r"^U[0-9a-f]{32}$")
# 簽章金鑰快取一小時；遇到不認得的 kid（金鑰輪替）才提早重抓，但最多每分鐘一次，避免被偽造的 kid 拖著打 Auth0
JWKS_TTL_SECONDS = 3600
JWKS_MIN_REFRESH_SECONDS = 60
# 時鐘誤差容忍
LEEWAY_SECONDS = 30


def _unauthorized() -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid access token")


def _unavailable(error: Exception) -> HTTPException:
    logger.warning("Auth0 endpoint unreachable: {}", type(error).__name__)
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "auth backend unavailable")


class Auth0Authenticator:
    """
    在本地驗證 Auth0 簽發給這個 API 的 access token（JWT）。

    以 tenant 的 JWKS 驗簽（只接受 RS256），並檢查 `iss`（這個網域）、`aud`（這個環境的 API）、
    `exp`，以及 `azp`（必須是 chat 自己的 Application）：同一個 tenant 裡其他 Application
    即使取得同一個 audience 的 token 也不會被接受。

    身分是 Action 放進 token 的 NTHUSA ID（`usr_<ULID>`），不是 Auth0 的 `sub`：
    `sub` 在 Auth0 連結帳號時會變，NTHUSA ID 不會。token 沒有這個 claim（Action 沒設定）時一律 401。
    使用者在 Auth0 綁定了我們的 LINE 連線時，token 也帶 LINE userId，放進 `linked`。
    顯示名稱與頭像不在 access token 裡，第一次見到某個 token 時向 `/userinfo` 取一次（失敗就略過），
    驗證結果以 token 雜湊為 key 快取到 `exp`（最多一小時）。
    """

    provider = "auth0"

    def __init__(
        self,
        domain: str,
        audience: str,
        client_id: str,
        http: httpx.AsyncClient,
        max_cache: int = 2048,
    ):
        self._base = f"https://{domain}"
        self._issuer = f"{self._base}/"
        self._audience = audience
        self._client_id = client_id
        self._http = http
        self._cache: OrderedDict[str, tuple[float, VerifiedIdentity]] = OrderedDict()
        self._max_cache = max_cache
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_fetched_at = float("-inf")
        self._keys_lock = asyncio.Lock()

    async def verify(self, token: str) -> VerifiedIdentity:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = time.time()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            self._cache.move_to_end(key)
            return cached[1]

        claims = await self._decode(token)
        name, picture = await self._profile(token)
        line_user_id = claims.get(LINE_CLAIM)
        linked = (
            (VerifiedIdentity(provider="line", provider_user_id=line_user_id),)
            if isinstance(line_user_id, str) and LINE_USER_ID.match(line_user_id)
            else ()
        )
        identity = VerifiedIdentity(
            provider=NTHUSA_PROVIDER,
            provider_user_id=claims[USR_CLAIM],
            display_name=name,
            picture_url=picture,
            linked=linked,
        )
        self._cache[key] = (min(float(claims["exp"]), now + 3600), identity)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)
        return identity

    async def _decode(self, token: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise _unauthorized() from None
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str) or not kid:
            raise _unauthorized()
        signing_key = await self._signing_key(kid)
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError:
            raise _unauthorized() from None
        sub = claims.get("sub")
        if claims.get("azp") != self._client_id or not isinstance(sub, str) or not sub:
            raise _unauthorized()
        nthusa_id = claims.get(USR_CLAIM)
        if not isinstance(nthusa_id, str) or not NTHUSA_ID.match(nthusa_id):
            logger.warning("Auth0 access token has no valid NTHUSA ID claim")
            raise _unauthorized()
        return claims

    async def _signing_key(self, kid: str) -> jwt.PyJWK:
        key = self._keys.get(kid)
        if key and time.monotonic() - self._keys_fetched_at < JWKS_TTL_SECONDS:
            return key
        async with self._keys_lock:
            age = time.monotonic() - self._keys_fetched_at
            # 別的請求剛抓過，或距離上次抓取不到一分鐘：不再打 Auth0
            if (
                kid not in self._keys and age >= JWKS_MIN_REFRESH_SECONDS
            ) or age >= JWKS_TTL_SECONDS:
                await self._fetch_keys()
        key = self._keys.get(kid)
        if key is None:
            raise _unauthorized()
        return key

    async def _fetch_keys(self) -> None:
        try:
            response = await self._http.get(f"{self._base}/.well-known/jwks.json", timeout=10)
            response.raise_for_status()
            jwks = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, ValueError, jwt.PyJWTError) as error:
            # 抓不到新金鑰時沿用舊的；完全沒有金鑰才回 503
            if not self._keys:
                raise _unavailable(error) from error
            logger.warning("Auth0 JWKS refresh failed: {}", type(error).__name__)
            self._keys_fetched_at = time.monotonic()
            return
        self._keys = {
            k.key_id: k
            for k in jwks.keys
            if k.key_id and k.key_type == "RSA" and k.public_key_use in (None, "sig")
        }
        self._keys_fetched_at = time.monotonic()

    async def _profile(self, token: str) -> tuple[str | None, str | None]:
        """顯示用的名稱與頭像（盡力而為，不影響驗證結果）；只接受 https 頭像。"""
        try:
            response = await self._http.get(
                f"{self._base}/userinfo",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
            )
            if response.status_code != 200:
                return None, None
            info = response.json()
        except (httpx.HTTPError, ValueError) as error:
            logger.warning("Auth0 userinfo unavailable: {}", type(error).__name__)
            return None, None
        if not isinstance(info, dict):
            return None, None
        name = info.get("name") or info.get("nickname")
        picture = info.get("picture")
        return (
            name[:100] if isinstance(name, str) and name else None,
            picture if isinstance(picture, str) and picture.startswith("https://") else None,
        )
