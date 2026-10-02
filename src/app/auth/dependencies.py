"""FastAPI 依賴：bearer token → 對應 provider 驗證 → 內部 user → 限流。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from src.app.auth.rate_limit import RateLimiter
from src.app.auth.service import IdentityService
from src.application.models.identity import DELETING, AccountDisabledError, Principal

PROVIDER_HEADER = "x-auth-provider"
DEFAULT_PROVIDER = "line"
ACCOUNT_DELETING = {
    "code": "account_deleting",
    "message": "你的資料刪除還沒完成，請再執行一次刪除。",
}


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


async def get_principal(
    request: Request, token: str = Depends(_bearer_token)
) -> Principal:
    return await _principal(request, token, allow_deleting=False)


async def get_principal_for_deletion(
    request: Request, token: str = Depends(_bearer_token)
) -> Principal:
    """同 `get_principal`，但刪除中途失敗（status=deleting）的帳號也能通過，好讓使用者重試刪除。"""
    return await _principal(request, token, allow_deleting=True)


async def _principal(request: Request, token: str, *, allow_deleting: bool) -> Principal:
    """
    依 `X-Auth-Provider`（預設 line）挑選 authenticator 驗證 token。

    只接受 `app.state.authenticators` 裡註冊的 provider，其餘一律 401；
    錯誤訊息不區分「provider 不存在」與「token 無效」，不給探測線索。
    """
    provider = request.headers.get(PROVIDER_HEADER, DEFAULT_PROVIDER).strip().lower()
    authenticator = request.app.state.authenticators.get(provider)
    if authenticator is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    identity = await authenticator.verify(token)

    identities: IdentityService = request.app.state.identity_service
    try:
        principal = await identities.resolve(identity, allow_deleting=allow_deleting)
    except AccountDisabledError as error:
        if error.status == DELETING:
            raise HTTPException(status.HTTP_403_FORBIDDEN, ACCOUNT_DELETING) from None
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled") from None

    limiter: RateLimiter = request.app.state.rate_limiter
    if not limiter.allow(principal.user_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "slow down")
    return principal
