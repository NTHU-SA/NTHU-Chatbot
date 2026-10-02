"""Firestore `modules/{moduleId}` 的啟用狀態，快取 60 秒。"""

from __future__ import annotations

import time

from google.cloud import firestore
from loguru import logger

CACHE_SECONDS = 60


class FirestoreModuleRegistry:
    def __init__(self, db: firestore.AsyncClient) -> None:
        self._db = db
        self._cache: dict[str, tuple[float, bool]] = {}

    async def is_enabled(self, module_id: str) -> bool:
        now = time.monotonic()
        cached = self._cache.get(module_id)
        if cached and cached[0] > now:
            return cached[1]
        try:
            snapshot = await self._db.collection("modules").document(module_id).get(
                field_paths=["enabled"]
            )
            enabled = (snapshot.to_dict() or {}).get("enabled", True) is not False
        except Exception as error:  # noqa: BLE001 -- 讀不到設定時不擋指令
            logger.warning("Module registry unavailable: {}", type(error).__name__)
            return cached[1] if cached else True
        self._cache[module_id] = (now + CACHE_SECONDS, enabled)
        return enabled
