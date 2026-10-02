"""
帳號 API：個人資訊、隱私權政策同意、刪除我的資料。

同意紀錄是每個版本一份文件；政策改版（`PRIVACY_POLICY_VERSION` 遞增）後，
使用者要重新同意才能再使用 AI 對話。`@` 指令不經過 AI，不需要同意。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from loguru import logger

from src.app.auth.dependencies import get_principal
from src.app.background import BackgroundWrites
from src.application.models.chat import ConsentRequest, ConsentState, MeResponse
from src.application.models.identity import CONSENT_TYPES, LiffClientInfo, Principal
from src.application.services.chat_store import ChatStore
from src.application.services.user_store import UserStore
from src.core.config import Settings

router = APIRouter(prefix="/api", tags=["account"])

PRIVACY_POLICY = "privacy_policy"
CONSENT_SOURCE = "LIFF"
CONSENT_REQUIRED = {"code": "consent_required", "message": "請先閱讀並同意隱私權政策。"}


def _users(request: Request) -> UserStore:
    return request.app.state.user_store


def _settings(request: Request) -> Settings:
    return request.app.state.settings


async def consent_state(request: Request, user: Principal) -> ConsentState:
    version = _settings(request).privacy_policy_version
    accepted = await _users(request).has_consent(user.user_id, PRIVACY_POLICY, version)
    return ConsentState(type=PRIVACY_POLICY, version=version, accepted=accepted)


async def require_consent(request: Request, user: Principal) -> None:
    """未同意目前版本的隱私權政策時回 403 `consent_required`（前端據此顯示同意畫面）。"""
    if not (await consent_state(request, user)).accepted:
        raise HTTPException(status.HTTP_403_FORBIDDEN, CONSENT_REQUIRED)


async def _me(request: Request, user: Principal, client: LiffClientInfo | None) -> MeResponse:
    settings = _settings(request)
    metadata = {"liff": client.as_metadata(settings.liff_id)} if client else None
    writes = BackgroundWrites()
    writes.spawn(_users(request).record_login(user, metadata), "record_login")
    response = MeResponse(
        display_name=user.display_name,
        picture_url=user.picture_url,
        liff_id=settings.liff_id,
        consent=await consent_state(request, user),
    )
    await writes.drain()
    return response


@router.get("/me", response_model=MeResponse)
async def me(request: Request, user: Principal = Depends(get_principal)):
    return await _me(request, user, None)


@router.post("/me", response_model=MeResponse)
async def me_with_client(
    request: Request,
    client: LiffClientInfo = Body(default_factory=LiffClientInfo),
    user: Principal = Depends(get_principal),
):
    """
    LIFF 頁面開啟時呼叫，順帶回報 LIFF 執行環境。

    這些資訊是前端自報的，只存成 `identities/line.metadata.liff`，不參與任何授權判斷。
    """
    return await _me(request, user, client)


def _check_type(consent_type: str) -> None:
    if consent_type not in CONSENT_TYPES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown consent type")


@router.get("/consents", response_model=list[ConsentState])
async def list_consents(request: Request, user: Principal = Depends(get_principal)):
    return [await consent_state(request, user)]


@router.post("/consents/{consent_type}", response_model=ConsentState)
async def accept_consent(
    consent_type: str,
    body: ConsentRequest,
    request: Request,
    user: Principal = Depends(get_principal),
):
    """同意目前版本；畫面上的版本已過期時回 409，前端應重新載入政策內容。"""
    _check_type(consent_type)
    version = _settings(request).privacy_policy_version
    if body.version != version:
        raise HTTPException(status.HTTP_409_CONFLICT, "policy version changed")
    await _users(request).set_consent(user.user_id, consent_type, version, True, CONSENT_SOURCE)
    return ConsentState(type=consent_type, version=version, accepted=True)


@router.post("/consents/{consent_type}/revoke", response_model=ConsentState)
async def revoke_consent(
    consent_type: str, request: Request, user: Principal = Depends(get_principal)
):
    """撤回同意：之後無法使用 AI 對話，直到再次同意。既有資料不會因撤回而刪除（另有刪除功能）。"""
    _check_type(consent_type)
    version = _settings(request).privacy_policy_version
    await _users(request).set_consent(user.user_id, consent_type, version, False, CONSENT_SOURCE)
    return ConsentState(type=consent_type, version=version, accepted=False)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(request: Request, user: Principal = Depends(get_principal)):
    """
    刪除我的所有資料：對話與訊息、偏好、同意紀錄、使用紀錄、外部身分對應與帳號本身。

    完成後同一個 LINE 帳號再開啟頁面會是全新的使用者（需要重新同意隱私權政策）。
    """
    chats: ChatStore = request.app.state.store
    await chats.delete_all_sessions(user.user_id)
    await _users(request).delete_user(user.user_id)
    request.app.state.identity_service.forget_user(user.user_id)
    logger.info("User data deleted on request")
