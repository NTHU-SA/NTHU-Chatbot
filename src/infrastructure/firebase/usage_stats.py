"""
Firestore `usageStats/{YYYY-MM-DD}`：聊天室每天的總數（messages / postbacks / follows / unfollows /
modules{moduleId}），以伺服器端 Increment 累加。不含任何使用者識別資料。
"""

from __future__ import annotations

from google.cloud import firestore

from src.application.services.chat_store import now_utc


class FirestoreUsageStats:
    def __init__(self, db: firestore.AsyncClient) -> None:
        self._db = db

    def _today(self):
        return self._db.collection("usageStats").document(now_utc().strftime("%Y-%m-%d"))

    async def record(self, event: str) -> None:
        await self._today().set({event: firestore.Increment(1)}, merge=True)

    async def record_module(self, module_id: str) -> None:
        # 模組 ID 來自 bot_config.yaml 的註冊表（不是使用者輸入），可以直接當欄位名稱
        await self._today().set({"modules": {module_id: firestore.Increment(1)}}, merge=True)
