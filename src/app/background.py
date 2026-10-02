"""
非關鍵寫入（最近活動時間、模組使用次數、身分 metadata）。

這些寫入不該拖慢回應，但也不能丟到回應之後才做：Cloud Run 預設只在處理請求期間配置 CPU，
回應送出後的工作可能被暫停甚至遺失。所以在請求一開始就啟動、與主要工作並行，
並在請求結束前 `drain()` 收尾；失敗只記錄例外型別，不影響回應。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from loguru import logger


class BackgroundWrites:
    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []

    def spawn(self, coroutine: Coroutine[Any, Any, Any], name: str) -> None:
        self._tasks.append(asyncio.create_task(coroutine, name=name))

    async def drain(self) -> None:
        tasks, self._tasks = self._tasks, []
        for task, result in zip(
            tasks, await asyncio.gather(*tasks, return_exceptions=True), strict=True
        ):
            if isinstance(result, BaseException):
                logger.warning(
                    "Background write {} failed: {}", task.get_name(), type(result).__name__
                )
