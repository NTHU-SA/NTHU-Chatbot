"""
`@` 指令模組的啟用狀態。

沒有後台，維運在 Firestore Console 建立 `modules/{moduleId}` 並設 `enabled: false` 即可暫停某個模組；
文件不存在代表沿用 `bot_config.yaml` 的預設（啟用）。
"""

from __future__ import annotations

from typing import Protocol


class ModuleRegistry(Protocol):
    async def is_enabled(self, module_id: str) -> bool: ...


class StaticModuleRegistry:
    """測試與本機開發用；`disabled` 內的模組視為停用。"""

    def __init__(self, disabled: set[str] | None = None) -> None:
        self.disabled = disabled or set()

    async def is_enabled(self, module_id: str) -> bool:
        return module_id not in self.disabled
