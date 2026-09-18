"""
以 OpenAI Agents SDK 搭配 NTHU Data MCP server 執行的 LLM agent。

`AgentRunner.stream()` 把 SDK 的事件流轉成一組與傳輸無關的事件，
由 chat route 轉成 SSE 送到 LIFF 頁面：

  thinking        {delta}      模型的思考摘要（需 REASONING_SUMMARY=true 與 reasoning 模型）
  suggestions     {options}    模型反問時提供的快速回覆選項
  tool_call_start {call_id, name, args}
  tool_call_end   {call_id, name, ok, duration_ms, result_preview}
  token           {delta}
  done            {content, tool_calls}
  error           {message}
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from agents import (
    Agent,
    AgentsException,
    MaxTurnsExceeded,
    ModelSettings,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    Runner,
    function_tool,
    set_tracing_disabled,
)
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter
from loguru import logger
from openai import APIStatusError, AsyncOpenAI
from openai.types.shared import Reasoning

from src.application.models.chat import Message, ToolCall
from src.core.config import Settings
from src.infrastructure.ai.prompts import build_instructions

TOOL_ERROR_PREFIX = "[TOOL_ERROR]"
SUGGEST_TOOL = "suggest_replies"
MAX_SUGGESTIONS = 4
MAX_SUGGESTION_CHARS = 30
TRUNCATION_NOTE = "\n\n[結果過長已截斷；如需更多請縮小查詢範圍（例如減少 limit 或加 keyword）]"
MCP_UNAVAILABLE_MESSAGE = "校園資料服務暫時無法連線，本汪晚點再幫你查，請稍後再試。"


class BoundedMCPServer(MCPServerStreamableHttp):
    """工具結果在送進模型前先截斷，避免撐爆小模型的 token 預算。"""

    def __init__(self, *args: Any, max_output_chars: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._max_output_chars = max_output_chars

    async def call_tool(self, tool_name, arguments, meta=None):
        result = await super().call_tool(tool_name, arguments, meta)
        budget = self._max_output_chars
        for block in result.content:
            text = getattr(block, "text", None)
            if text is None:
                continue
            if len(text) > budget:
                block.text = text[: max(budget, 0)] + TRUNCATION_NOTE
                budget = 0
            else:
                budget -= len(text)
        return result


@dataclass
class AgentEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


def _tool_error_message(_ctx, err: Exception) -> str:
    # 回傳給模型而不是直接拋出；前綴讓我們能標記 ok=False。
    return f"{TOOL_ERROR_PREFIX} {type(err).__name__}: {str(err)[:200]}"


def _preview(value: Any, limit: int) -> str:
    text = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )
    return text if len(text) <= limit else text[:limit] + "…"


def _output_text(output: Any) -> str:
    """MCP 工具輸出是 content block（{"type": "text", ...}），攤平成純文字。"""
    if isinstance(output, str):
        return output
    if isinstance(output, dict) and output.get("type") == "text":
        return output.get("text") or ""
    if isinstance(output, list):
        texts = [
            b.get("text")
            for b in output
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if texts:
            return "\n".join(t for t in texts if t)
    return _preview(output, 10_000)


def _parse_args(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except json.JSONDecodeError:
        return {"raw": raw[:500]}


def _instructions(_ctx, _agent) -> str:
    return build_instructions()


@function_tool(name_override=SUGGEST_TOOL)
def suggest_replies(options: list[str]) -> str:
    """向使用者反問時呼叫，提供 2 到 4 個可以一鍵回覆的簡短選項（每個一句話）。"""
    return "ok"


def clean_suggestions(raw: Any) -> list[str]:
    """整理模型給的選項：去空白、去重、截長、最多 MAX_SUGGESTIONS 個。"""
    if not isinstance(raw, list):
        return []
    options: list[str] = []
    for item in raw:
        text = " ".join(str(item).split())[:MAX_SUGGESTION_CHARS]
        if text and text not in options:
            options.append(text)
        if len(options) == MAX_SUGGESTIONS:
            break
    return options


class AgentRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connected = False

        # 一個共用的 client；官方 OpenAI 或任何 OpenAI 相容端點都可用。
        client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=60,
            max_retries=1,
        )
        # 明確把模型綁到這個 client。若只傳字串，SDK 會做 provider 前綴解析，
        # 把 Groq/OpenRouter 風格的 "openai/gpt-oss-120b" 之類的 id 去掉 "openai/"。
        model_cls = (
            OpenAIResponsesModel
            if settings.openai_use_responses_api
            else OpenAIChatCompletionsModel
        )
        model = model_cls(model=settings.openai_model, openai_client=client)
        # 永遠不把對話 trace 送到 OpenAI 的 tracing 後端。
        set_tracing_disabled(True)

        self._mcp = BoundedMCPServer(
            name="nthu-data",
            max_output_chars=settings.max_tool_output_chars,
            params={
                "url": settings.mcp_server_url,
                "timeout": settings.mcp_timeout_seconds,
                "sse_read_timeout": settings.mcp_timeout_seconds * 2,
            },
            cache_tools_list=True,
            client_session_timeout_seconds=settings.mcp_timeout_seconds,
            tool_filter=create_static_tool_filter(
                allowed_tool_names=list(settings.mcp_allowed_tools)
            ),
            failure_error_function=_tool_error_message,
            max_retry_attempts=1,
        )
        # 思考摘要只有 reasoning 模型走 Responses API 才支援；其他情況不帶參數以免被端點拒絕。
        model_settings = ModelSettings()
        if settings.reasoning_summary and settings.openai_use_responses_api:
            model_settings = ModelSettings(reasoning=Reasoning(summary="auto"))

        self._agent = Agent(
            name="狗狗情報員",
            instructions=_instructions,
            model=model,
            model_settings=model_settings,
            tools=[suggest_replies],
            mcp_servers=[self._mcp],
        )

    # -- lifecycle --
    async def start(self) -> None:
        """
        連線 MCP server。

        失敗只記錄不拋出，避免校園 API 掛掉時整個服務（含 webhook 指令）起不來；
        之後每次 `stream()` 會再嘗試連線。
        """
        try:
            await self._ensure_connected()
        except Exception as error:
            logger.error("MCP connect failed at startup: {}", type(error).__name__)

    async def stop(self) -> None:
        if not self._connected:
            return
        self._connected = False
        try:
            await self._mcp.cleanup()
        except Exception:
            logger.opt(exception=True).debug("MCP cleanup raised")

    async def _ensure_connected(self) -> None:
        if self._connected:
            return
        await self._mcp.connect()
        tools = await self._mcp.list_tools()
        self._connected = True
        logger.info("MCP connected: {} tools available", len(tools))

    # -- chat --
    async def stream(
        self, history: list[Message], user_text: str
    ) -> AsyncIterator[AgentEvent]:
        try:
            await self._ensure_connected()
        except Exception as error:
            logger.error("MCP connect failed: {}", type(error).__name__)
            yield AgentEvent("error", {"message": MCP_UNAVAILABLE_MESSAGE})
            return

        cap = self._settings.history_message_chars
        items: list[dict[str, Any]] = [
            {"role": m.role, "content": _preview(m.content, cap)}
            for m in history
            if m.content
        ]
        items.append({"role": "user", "content": user_text})

        pending: dict[str, tuple[str, dict[str, Any], float]] = {}
        suggestion_calls: set[str] = set()
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        preview_chars = self._settings.tool_result_preview_chars
        summary_parts = 0

        try:
            result = Runner.run_streamed(
                self._agent, input=items, max_turns=self._settings.max_agent_turns
            )
            async for event in result.stream_events():
                if event.type == "raw_response_event":
                    data = event.data
                    data_type = getattr(data, "type", "")
                    if data_type == "response.output_text.delta" and data.delta:
                        text_parts.append(data.delta)
                        yield AgentEvent("token", {"delta": data.delta})
                    elif data_type == "response.reasoning_summary_text.delta" and data.delta:
                        yield AgentEvent("thinking", {"delta": data.delta})
                    elif data_type == "response.reasoning_summary_part.added":
                        # 多段摘要之間留空行，前端直接串接即可
                        if summary_parts:
                            yield AgentEvent("thinking", {"delta": "\n\n"})
                        summary_parts += 1
                    continue

                if event.type != "run_item_stream_event":
                    continue

                item = event.item
                if event.name == "tool_called":
                    raw = item.raw_item
                    call_id = getattr(raw, "call_id", None) or f"call_{len(pending)}"
                    name = getattr(raw, "name", "unknown")
                    args = _parse_args(getattr(raw, "arguments", None))
                    if name == SUGGEST_TOOL:
                        # 不是真的查資料：轉成快速回覆選項，並記進 tool_calls 供重新載入時重繪
                        suggestion_calls.add(call_id)
                        options = clean_suggestions(args.get("options"))
                        if options:
                            tool_calls.append(
                                ToolCall(name=SUGGEST_TOOL, args={"options": options})
                            )
                            yield AgentEvent("suggestions", {"options": options})
                        continue
                    pending[call_id] = (name, args, time.monotonic())
                    yield AgentEvent(
                        "tool_call_start",
                        {"call_id": call_id, "name": name, "args": args},
                    )

                elif event.name == "tool_output":
                    raw = item.raw_item
                    call_id = (
                        raw.get("call_id")
                        if isinstance(raw, dict)
                        else getattr(raw, "call_id", None)
                    )
                    if call_id in suggestion_calls:
                        continue
                    name, args, started = pending.pop(
                        call_id, ("unknown", {}, time.monotonic())
                    )
                    output_text = _output_text(item.output)
                    ok = not output_text.startswith(TOOL_ERROR_PREFIX)
                    tool_call = ToolCall(
                        name=name,
                        args=args,
                        result_preview=_preview(output_text, preview_chars),
                        duration_ms=int((time.monotonic() - started) * 1000),
                        ok=ok,
                    )
                    tool_calls.append(tool_call)
                    yield AgentEvent(
                        "tool_call_end",
                        {
                            "call_id": call_id,
                            "name": name,
                            "ok": ok,
                            "duration_ms": tool_call.duration_ms,
                            "result_preview": tool_call.result_preview,
                        },
                    )

            final = result.final_output
            content = final if isinstance(final, str) and final else "".join(text_parts)
            yield AgentEvent(
                "done",
                {
                    "content": content,
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                },
            )

        except MaxTurnsExceeded:
            yield AgentEvent(
                "error", {"message": "工具呼叫次數過多，請把問題拆小一點再試。"}
            )
        except APIStatusError as error:
            logger.warning("LLM API error: {}", error.status_code)
            if error.status_code in (413, 429):
                message = "這次請求超過模型的額度或大小限制，請縮小問題範圍，或稍等一分鐘再試。"
            elif error.status_code in (401, 403, 404):
                message = "模型設定有誤（金鑰或模型名稱），請聯絡管理員。"
            else:
                message = "模型服務暫時無法回應，請稍後再試。"
            yield AgentEvent("error", {"message": message})
        except AgentsException as error:
            logger.warning("Agent error: {}", type(error).__name__)
            yield AgentEvent("error", {"message": "模型回覆時發生錯誤，請稍後再試。"})
        except Exception as error:
            logger.error("Unexpected agent failure: {}", type(error).__name__)
            logger.opt(exception=True).debug("Agent failure traceback")
            yield AgentEvent("error", {"message": "系統暫時無法回應，請稍後再試。"})
