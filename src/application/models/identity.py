"""
身分相關的資料模型。

所有資料都以內部 `user_id`（`usr_` 開頭、不可變）為主鍵；LINE、未來的學校 OAuth 或 Google
都只是連結到這個 user 的「外部身分」。外部身分的原始 ID 只存在 `users/{uid}/identities/{provider}`，
查詢用的 `identityLookup` 文件 ID 是雜湊值，避免學號這類個資出現在文件路徑、Console 與 log。
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def new_user_id() -> str:
    """內部 user id：128 bit 隨機值，無法猜測，也不含任何外部身分資訊。"""
    return "usr_" + secrets.token_hex(16)


def lookup_key(provider: str, provider_user_id: str) -> str:
    """`identityLookup` 的文件 ID。"""
    return hashlib.sha256(f"{provider}:{provider_user_id}".encode()).hexdigest()


# 學生會跨系統的 NTHUSA ID（Auth0 post-login Action 產生的 `usr_<ULID>`）。
# 它和內部 user id 不同：內部 id 只在這個環境的資料庫裡用，NTHUSA ID 才是各系統之間交換的 ID，
# 兩者以外部身分 `nthusa` 對應（identityLookup），所以刪除、額度與並行建立沿用同一套機制。
NTHUSA_PROVIDER = "nthusa"


@dataclass(frozen=True)
class VerifiedIdentity:
    """
    已由 provider 驗證過的外部身分。

    只能由 Authenticator 建立：`provider_user_id` 來自驗證過的 token，前端無法自行宣稱。
    `linked` 是同一個 token 裡 IdP 保證屬於同一個人的其他身分（例如 NTHUSA ID 綁定的 LINE），
    由 IdentityService 對應到同一個 user，讓 LINE webhook 找得到這個人。
    """

    provider: str
    provider_user_id: str = field(repr=False)
    display_name: str | None = None
    picture_url: str | None = None
    linked: tuple[VerifiedIdentity, ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class Principal:
    """通過驗證並對應到內部 user 的請求者。路由、限流、額度都只認 `user_id`。"""

    user_id: str
    provider: str
    display_name: str | None = None
    picture_url: str | None = None


# users/{uid}.status
# - active：正常
# - blocked：管理者封鎖
# - deleting：使用者要求刪除、資料清除中（只允許再次呼叫刪除，讓失敗時可以重試）
# - deleted：資料已清除，只留下不含個資的墓碑（TTL 過期後自動消失）；同一個外部身分再登入會是全新的 user
ACTIVE = "active"
BLOCKED = "blocked"
DELETING = "deleting"
DELETED = "deleted"
# 這些狀態下寫入的個人資料必須撤銷（見 FirestoreUserStore.delete_user）
GONE_STATUSES = (DELETING, DELETED)


class AccountDisabledError(Exception):
    """帳號被封鎖或刪除中。"""

    def __init__(self, user_id: str, status: str | None = None) -> None:
        super().__init__(user_id)
        self.status = status


class DeletionIncompleteError(Exception):
    """資料沒有全部刪除（Firestore 個別刪除重試用盡）；帳號維持 deleting，可以再呼叫一次刪除。"""


class IdentityConflictError(Exception):
    """外部身分已經連結到另一個 user。"""


class LastIdentityError(Exception):
    """不能解除最後一個登入方式，否則帳號將無法再登入。"""


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
