"""
個人化資料：稱呼、系所與使用者要求記住的事。

這些內容都是使用者自己提供的，會被放進給模型的 instructions，所以一律視為「資料」：
寫入前去掉換行與角括號、限制長度，注入時再用分隔標記包起來（見 prompts.py）。
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field

PREFERENCE_KEYS = ("nickname", "department")
MAX_NICKNAME_CHARS = 20
MAX_DEPARTMENT_CHARS = 40
MAX_MEMORIES = 20
MAX_MEMORY_CHARS = 100

# 首次使用時只主動問一次稱呼與系所
ONBOARDING_MODULE = "onboarding"
ONBOARDING_STATES = ("asked", "done", "skipped")

_UNSAFE = re.compile(r"[<>\r\n\t\x00-\x1f\x7f]")


def clean_text(value: str, limit: int) -> str:
    """去掉控制字元、換行與角括號，壓縮空白並截斷。"""
    return " ".join(_UNSAFE.sub(" ", value or "").split())[:limit]


class MemoryItem(BaseModel):
    id: str
    value: str
    created_at: datetime | None = None


class Profile(BaseModel):
    nickname: str | None = None
    department: str | None = None
    memories: list[MemoryItem] = Field(default_factory=list)
    onboarding: str | None = None  # None / asked / done / skipped

    @property
    def is_empty(self) -> bool:
        return not (self.nickname or self.department or self.memories)


class ProfileUpdate(BaseModel):
    """設定頁送出的修改；空字串代表清除。"""

    nickname: str | None = Field(default=None, max_length=MAX_NICKNAME_CHARS * 2)
    department: str | None = Field(default=None, max_length=MAX_DEPARTMENT_CHARS * 2)
    skip_onboarding: bool = False


class MemoryLimitError(Exception):
    """記憶已達上限。"""
