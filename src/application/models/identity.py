"""
身分相關的資料模型。

所有資料都以 NTHUSA ID（Auth0 post-login Action 產生的 `usr_<ULID>`，學生會跨系統共用、
staging 與 prod 相同）為主鍵：`users/{NTHUSA ID}`。它只來自驗證過的 Auth0 access token。
LINE 是綁定在 NTHUSA ID 上的外部身分：原始 LINE userId 只存在 `users/{uid}/identities/line`，
webhook 查詢用的 `identityLookup` 文件 ID 是雜湊值，避免外部 ID 出現在文件路徑、Console 與 log。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def lookup_key(provider: str, provider_user_id: str) -> str:
    """`identityLookup` 的文件 ID。"""
    return hashlib.sha256(f"{provider}:{provider_user_id}".encode()).hexdigest()


# 登入身分（Auth0 token 裡的 NTHUSA ID）的 provider 名稱；它的 provider_user_id 就是 user id
NTHUSA_PROVIDER = "nthusa"
# 由 Auth0 綁定、對應到 NTHUSA ID 的外部身分（webhook 用 LINE userId 找到同一個人）
LINKED_PROVIDERS = ("line",)


@dataclass(frozen=True)
class VerifiedIdentity:
    """
    已由 provider 驗證過的外部身分。

    只能由 Authenticator 建立：`provider_user_id` 來自驗證過的 token，前端無法自行宣稱。
    `linked` 是同一個 token 裡 IdP 保證屬於同一個人的其他身分（NTHUSA ID 綁定的 LINE），
    由 IdentityService 對應到同一個 user，讓 LINE webhook 找得到這個人。
    """

    provider: str
    provider_user_id: str = field(repr=False)
    display_name: str | None = None
    picture_url: str | None = None
    linked: tuple[VerifiedIdentity, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Principal:
    """通過驗證的請求者。`user_id` 是 NTHUSA ID；路由、限流、額度都只認它。"""

    user_id: str
    provider: str
    display_name: str | None = None
    picture_url: str | None = None


# users/{uid}.status
# - active：正常
# - blocked：管理者封鎖
# - deleting：使用者要求刪除、資料清除中（只允許再次呼叫刪除，讓失敗時可以重試）
# - deleted：資料已清除，只留下不含個資的墓碑（TTL 過期後自動消失）；同一個 NTHUSA ID 隔一段時間
#   （REUSE_AFTER_DELETION）再登入，會在同一個 key 重建一個空白的 user
ACTIVE = "active"
BLOCKED = "blocked"
DELETING = "deleting"
DELETED = "deleted"
# 刪除前已在進行的請求可能在刪除後才寫入，靠讀到 deleted 自己撤銷（見 FirestoreUserStore._undo_if_deleted）。
# 同一個 key 重建得太快，這些寫入會讀到 active 而留在新帳號裡，所以墓碑至少要留這麼久才能重建：
# 比 IdentityService 的狀態快取（60 秒）與 Cloud Run 的請求上限（180 秒）都長。
# 預設值；staging 為了反覆測試可用 ACCOUNT_REUSE_DELAY_SECONDS 縮短（見 Settings）。
REUSE_AFTER_DELETION = timedelta(minutes=10)
# 這些狀態下寫入的個人資料必須撤銷（見 FirestoreUserStore.delete_user）
GONE_STATUSES = (DELETING, DELETED)


class AccountDisabledError(Exception):
    """帳號被封鎖、刪除中，或剛刪除完成（status=deleted，還不能重建）。"""

    def __init__(self, user_id: str, status: str | None = None) -> None:
        super().__init__(user_id)
        self.status = status


class DeletionIncompleteError(Exception):
    """資料沒有全部刪除（Firestore 個別刪除重試用盡）；帳號維持 deleting，可以再呼叫一次刪除。"""


# 需要使用者同意的文件；文件 ID 為 `{type}_v{version}`，改版時另存一份，不覆蓋舊版本
CONSENT_TYPES = ("privacy_policy",)


def consent_doc_id(consent_type: str, version: str) -> str:
    return f"{consent_type}_v{version}"


class LiffClientInfo(BaseModel):
    """
    LIFF 前端自報的執行環境資訊。

    只當 metadata 保存，**不參與任何授權判斷**。欄位白名單、長度上限，多餘欄位直接拒絕。
    spec 裡的 contextId（可能是群組 ID）刻意不收。
    """

    model_config = ConfigDict(extra="forbid")

    os: Literal["ios", "android", "web"] | None = None
    line_version: str | None = Field(default=None, max_length=32)
    language: str | None = Field(default=None, max_length=16)
    context_type: Literal["utou", "group", "room", "external", "none", "square_chat"] | None = None
    friendship: bool | None = None

    def as_metadata(self, liff_id: str) -> dict:
        return {
            "liffId": liff_id,
            "os": self.os,
            "appVersion": self.line_version,
            "language": self.language,
            "contextType": self.context_type,
            "friendshipStatus": self.friendship,
        }
