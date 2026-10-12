"""
聊天室（LINE webhook）的使用統計：只記每天的總數，不記是誰。

聊天室不需要登入，大多數人也沒有綁定 NTHUSA ID，所以這裡刻意不帶任何 user id 或 LINE userId；
個人的使用紀錄只來自登入後的網頁對話。
"""

from __future__ import annotations

from collections import Counter
from typing import Protocol

from src.application.services.chat_store import now_utc

# 統計的事件種類（Firestore 欄位名稱）
MESSAGE = "messages"
POSTBACK = "postbacks"
FOLLOW = "follows"
UNFOLLOW = "unfollows"


class UsageStats(Protocol):
    async def record(self, event: str) -> None:
        """今天的 `event` 加一。"""
        ...

    async def record_module(self, module_id: str) -> None:
        """今天 `@` 指令模組 `module_id` 的使用次數加一。"""
        ...


class MemoryUsageStats:
    def __init__(self) -> None:
        self.counts: Counter[tuple[str, str]] = Counter()

    @staticmethod
    def _day() -> str:
        return now_utc().strftime("%Y-%m-%d")

    async def record(self, event: str) -> None:
        self.counts[(self._day(), event)] += 1

    async def record_module(self, module_id: str) -> None:
        self.counts[(self._day(), f"modules.{module_id}")] += 1
