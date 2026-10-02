"""
單次 agent 執行（一則使用者訊息）的共享狀態。

- `tainted`：這一輪已讀取外部資料（MCP 工具、網路搜尋）。之後的個人化寫入工具一律拒絕，
  避免工具結果裡的文字誘導模型寫入使用者記憶（prompt injection）。
- 呼叫次數上限：每則訊息的外部工具呼叫與網路搜尋都有上限，控制成本與延遲。

用 ContextVar 傳遞：必須在 `Runner.run_streamed` 之前設定，SDK 的背景 task 會複製當下的 context，
所以工具在任何 task 裡改的都是同一個物件。
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


class ToolBudgetExceeded(Exception):
    """這則訊息的工具呼叫次數已達上限。"""


@dataclass
class RunState:
    max_tool_calls: int
    max_web_searches: int = 0
    tool_calls: int = 0
    web_searches: int = 0
    tainted: bool = False


RUN: ContextVar[RunState | None] = ContextVar("agent_run_state", default=None)


def current() -> RunState | None:
    return RUN.get()


def begin_external_call(*, web_search: bool = False) -> None:
    """
    外部資料即將進入對話：標記本輪已污染，並計入呼叫次數。

    超過上限時拋出 ToolBudgetExceeded（由工具的錯誤處理轉成給模型的訊息）。
    沒有執行狀態（例如單元測試直接呼叫）時不做任何限制。
    """
    state = RUN.get()
    if state is None:
        return
    state.tainted = True
    if state.tool_calls >= state.max_tool_calls:
        raise ToolBudgetExceeded("tool call limit for this message reached; answer with what you have")
    if web_search and state.web_searches >= state.max_web_searches:
        raise ToolBudgetExceeded("web search limit for this message reached")
    state.tool_calls += 1
    if web_search:
        state.web_searches += 1


def is_tainted() -> bool:
    state = RUN.get()
    return bool(state and state.tainted)
