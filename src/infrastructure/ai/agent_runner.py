"""
以 OpenAI Agents SDK 搭配 NTHU Data MCP server 執行的 LLM agent。

`AgentRunner.stream()` 把 SDK 的事件流轉成一組與傳輸無關的事件，
由 chat route 轉成 SSE 送到 LIFF 頁面：

  thinking        {delta}      模型的思考摘要（需 REASONING_SUMMARY=true 與 reasoning 模型）
  suggestions     {options}    模型反問時提供的快速回覆選項
  memory          {action, items}  個人化工具寫入了稱呼 / 系所 / 記憶（action: saved / forgotten）
  interim         {text, discard}  呼叫工具前講的過場句；前端把它從回答移到「過程」卡片，
                                   discard=true 時只清掉（模型在 suggest_replies 後重講了問題）
  tool_call_start {call_id, name, title, args}
  tool_call_end   {call_id, name, title, ok, duration_ms, result_preview}
  token           {delta}
  done            {content, tool_calls, usage}   usage 只給後端存檔，不送前端
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

from src.application.models.chat import Message, TokenUsage, ToolCall
from src.core.config import Settings
from src.infrastructure.ai.personal_tools import (
    PERSONAL_TOOL_OBJECTS,
    PERSONAL_TOOLS,
    ChatContext,
)
from src.infrastructure.ai.prompts import build_instructions
from src.infrastructure.ai.run_state import RUN, RunState, begin_external_call
from src.infrastructure.ai.web_search import WEB_SEARCH, WEB_SEARCH_TITLE, build_web_search_tool

TOOL_ERROR_PREFIX = "[TOOL_ERROR]"
SUGGEST_TOOL = "suggest_replies"
MAX_SUGGESTIONS = 4
MAX_SUGGESTION_CHARS = 30
# 佔位型選項（「請輸入…」「其他」）不是可以直接送出的答案，一律過濾
PLACEHOLDER_MARKERS = ("請輸入", "輸入", "其他", "自行", "自訂", "告訴我", "…", "...", "?", "？")
TRUNCATION_NOTE = "\n\n[結果過長已截斷；如需更多請縮小查詢範圍（例如減少 limit 或加 keyword）]"
MCP_UNAVAILABLE_MESSAGE = "校園資料服務暫時無法連線，本汪晚點再幫你查，請稍後再試。"

# MCP 結果快取秒數（依工具；結果與使用者無關，跨使用者共用）。
# 公車即時資料不快取；沒有列出的工具也不快取。
MCP_CACHE_TTL = {
    "get_announcements": 300,
    "get_newsletters": 600,
    "get_library_info": 300,
    "find_dining": 300,
    "get_energy_usage": 300,
    "search_campus": 3600,
    "get_bus_stops": 3600,
    "search_courses": 3600,
}
MCP_CACHE_MAX_ENTRIES = 256


class BoundedMCPServer(MCPServerStreamableHttp):
    """
    校園資料 MCP server 的包裝。

    - 每次呼叫都計入本則訊息的工具上限，並標記本輪已讀取外部資料（個人化寫入工具因此停用）；
    - 唯讀、與使用者無關的工具結果短暫快取，同樣的查詢不重打 MCP；
    - 結果在送進模型前先截斷，避免撐爆 token 預算。
    """

    def __init__(self, *args: Any, max_output_chars: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._max_output_chars = max_output_chars
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cache_key(self, tool_name: str, arguments: Any) -> str | None:
        if tool_name not in MCP_CACHE_TTL:
            return None
        return tool_name + ":" + json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False)

    async def call_tool(self, tool_name, arguments, meta=None):
        # 外部資料即將進入對話（快取命中也算）：計次、標記本輪已污染
        begin_external_call()
        key = self._cache_key(tool_name, arguments)
        now = time.monotonic()
        if key is not None:
            cached = self._cache.get(key)
            if cached and cached[0] > now:
                return cached[1].model_copy(deep=True)
        result = await self._call_and_truncate(tool_name, arguments, meta)
        failed = getattr(result, "is_error", False) or getattr(result, "isError", False)
        if key is not None and not failed:
            if len(self._cache) >= MCP_CACHE_MAX_ENTRIES:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) < MCP_CACHE_MAX_ENTRIES:
                self._cache[key] = (now + MCP_CACHE_TTL[tool_name], result.model_copy(deep=True))
        return result

    async def _call_and_truncate(self, tool_name, arguments, meta):
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
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _output_text(output: Any) -> str:
    """MCP 工具輸出是 content block（{"type": "text", ...}），攤平成純文字。"""
    if isinstance(output, str):
        return output
    if isinstance(output, dict) and output.get("type") == "text":
        return output.get("text") or ""
    if isinstance(output, list):
        texts = [b.get("text") for b in output if isinstance(b, dict) and b.get("type") == "text"]
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


def _usage(result: Any) -> dict[str, int] | None:
    """SDK 累計的 token 用量（含 reasoning tokens）；取不到時回傳 None。"""
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if usage is None:
        return None
    details = getattr(usage, "output_tokens_details", None)
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        reasoning_tokens=getattr(details, "reasoning_tokens", 0) or 0,
        requests=getattr(usage, "requests", 0) or 0,
    ).model_dump()


def _instructions(ctx, agent) -> str:
    context = ctx.context if isinstance(getattr(ctx, "context", None), ChatContext) else None
    web_search = any(getattr(tool, "name", "") == WEB_SEARCH for tool in agent.tools)
    if context is None:
        return build_instructions(web_search=web_search)
    return build_instructions(
        profile=context.profile, onboarding=context.onboarding, web_search=web_search
    )


def _personal_event(output: str) -> AgentEvent | None:
    """個人化工具的結果 → 前端的 memory 事件；沒有寫入任何東西時回傳 None。"""
    try:
        data = json.loads(output)
    except TypeError, json.JSONDecodeError:
        return None
    if data.get("status") == "saved" and data.get("saved"):
        return AgentEvent("memory", {"action": "saved", "items": data["saved"]})
    if data.get("status") == "forgotten":
        return AgentEvent(
            "memory",
            {"action": "forgotten", "items": [{"kind": "memory", "value": data["forgotten"]}]},
        )
    return None


@function_tool(name_override=SUGGEST_TOOL)
def suggest_replies(options: list[str]) -> str:
    """向使用者反問時呼叫，提供 2 到 4 個可以一鍵回覆的簡短選項（每個一句話）。"""
    return "ok"


def clean_suggestions(raw: Any) -> list[str]:
    """
    整理模型給的選項：去空白、去重、截長、最多 MAX_SUGGESTIONS 個。

    只保留可以直接當作回覆送出的具體選項；「請輸入…」「其他」這類佔位選項會被移除，
    模型應改在文字訊息裡請使用者自行輸入。
    """
    if not isinstance(raw, list):
        return []
    options: list[str] = []
    for item in raw:
        text = " ".join(str(item).split())[:MAX_SUGGESTION_CHARS]
        if not text or text in options:
            continue
        if any(marker in text for marker in PLACEHOLDER_MARKERS):
            continue
        options.append(text)
        if len(options) == MAX_SUGGESTIONS:
            break
    return options


class AgentRunner:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._connected = False
        self._tool_titles: dict[str, str] = {WEB_SEARCH: WEB_SEARCH_TITLE}

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
        # parallel_tool_calls：彼此獨立的查詢在同一步一起發出，SDK 會並行執行
        model_settings = ModelSettings(
            max_tokens=settings.max_output_tokens, parallel_tool_calls=True
        )
        if settings.reasoning_summary and settings.openai_use_responses_api:
            model_settings.reasoning = Reasoning(summary="auto")

        tools = [suggest_replies, *PERSONAL_TOOL_OBJECTS]
        # 網路搜尋需要官方 OpenAI 的 Responses API（web_search 工具）
        self._web_search = settings.web_search_enabled and settings.openai_use_responses_api
        if settings.web_search_enabled and not self._web_search:
            logger.warning("WEB_SEARCH_ENABLED ignored: requires OPENAI_USE_RESPONSES_API=true")
        if self._web_search:
            tools.append(
                build_web_search_tool(
                    client,
                    settings.web_search_model or settings.openai_model,
                    settings.web_search_domains,
                    _tool_error_message,
                )
            )

        self._agent = Agent(
            name="狗狗情報員",
            instructions=_instructions,
            model=model,
            model_settings=model_settings,
            tools=tools,
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
        for tool in tools:
            title = tool.title or (tool.annotations.title if tool.annotations else None)
            if title:
                self._tool_titles[tool.name] = title
        self._connected = True
        logger.info("MCP connected: {} tools available", len(tools))

    # -- chat --
    async def stream(
        self, history: list[Message], user_text: str, context: ChatContext | None = None
    ) -> AsyncIterator[AgentEvent]:
        try:
            await self._ensure_connected()
        except Exception as error:
            logger.error("MCP connect failed: {}", type(error).__name__)
            yield AgentEvent("error", {"message": MCP_UNAVAILABLE_MESSAGE})
            return

        cap = self._settings.history_message_chars
        items: list[dict[str, Any]] = [
            {"role": m.role, "content": _preview(m.content, cap)} for m in history if m.content
        ]
        items.append({"role": "user", "content": user_text})

        pending: dict[str, tuple[str, dict[str, Any], float]] = {}
        suggestion_calls: set[str] = set()
        personal_calls: set[str] = set()
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []
        interims: list[str] = []
        # suggest_replies 之前寫的問題：模型之後若不再說話它就是回答，若重講一次則丟棄
        pending_question: str | None = None
        preview_chars = self._settings.tool_result_preview_chars
        summary_parts = 0

        # 必須在 run_streamed 之前設定：SDK 的背景 task 會複製當下的 context
        run_state = RunState(
            max_tool_calls=self._settings.max_tool_calls_per_message,
            max_web_searches=self._settings.max_web_searches_per_message,
        )
        token = RUN.set(run_state)
        try:
            result = Runner.run_streamed(
                self._agent,
                input=items,
                max_turns=self._settings.max_agent_turns,
                context=context,
            )
            async for event in result.stream_events():
                if event.type == "raw_response_event":
                    data = event.data
                    data_type = getattr(data, "type", "")
                    if data_type == "response.output_text.delta" and data.delta:
                        if pending_question is not None:
                            yield AgentEvent("interim", {"text": pending_question, "discard": True})
                            pending_question = None
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
                    title = self._tool_titles.get(name)
                    args = _parse_args(getattr(raw, "arguments", None))
                    if name == SUGGEST_TOOL:
                        # 不是真的查資料：轉成快速回覆選項，並記進 tool_calls 供重新載入時重繪
                        suggestion_calls.add(call_id)
                        if text_parts:
                            pending_question = "".join(text_parts).strip() or None
                            text_parts.clear()
                        options = clean_suggestions(args.get("options"))
                        if options:
                            tool_calls.append(
                                ToolCall(name=SUGGEST_TOOL, args={"options": options})
                            )
                            yield AgentEvent("suggestions", {"options": options})
                        continue
                    if name in PERSONAL_TOOLS:
                        # 寫入使用者資料：不顯示成工具卡片，也不把內容存進訊息的 tool_calls
                        personal_calls.add(call_id)
                        continue
                    # 模型在呼叫工具前講的話只是過場（「本汪查一下！」），不算最終回答：
                    # 通知前端把已串流的文字移到過程卡片，並從頭累積正式回答。
                    interim = "".join(text_parts).strip()
                    if pending_question is not None:
                        interim = (pending_question + "\n\n" + interim).strip()
                        pending_question = None
                    if interim:
                        interims.append(interim)
                        text_parts.clear()
                        yield AgentEvent("interim", {"text": interim, "discard": False})
                    pending[call_id] = (name, args, time.monotonic())
                    yield AgentEvent(
                        "tool_call_start",
                        {"call_id": call_id, "name": name, "title": title, "args": args},
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
                    if call_id in personal_calls:
                        personal = _personal_event(_output_text(item.output))
                        if personal is not None:
                            yield personal
                        continue
                    name, args, started = pending.pop(call_id, ("unknown", {}, time.monotonic()))
                    output_text = _output_text(item.output)
                    ok = not output_text.startswith(TOOL_ERROR_PREFIX)
                    tool_call = ToolCall(
                        name=name,
                        title=self._tool_titles.get(name),
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
                            "title": tool_call.title,
                            "ok": ok,
                            "duration_ms": tool_call.duration_ms,
                            "result_preview": tool_call.result_preview,
                        },
                    )

            # 以最後一次工具呼叫之後串流出去的文字為準；模型若以工具呼叫收尾而沒再說話，
            # 退而用最後一句過場句或 SDK 的 final_output。
            streamed = "".join(text_parts).strip()
            final = result.final_output
            content = (
                streamed
                or pending_question
                or (interims[-1] if interims else "")
                or (final if isinstance(final, str) else "")
            )
            # 只記次數，方便在 Logging 觀察成本；不含任何 ID 或內容
            logger.info(
                "Agent run finished: {} tool calls, {} web searches",
                run_state.tool_calls,
                run_state.web_searches,
            )
            yield AgentEvent(
                "done",
                {
                    "content": content[: self._settings.max_output_chars],
                    "tool_calls": [tc.model_dump() for tc in tool_calls],
                    "usage": _usage(result),
                },
            )

        except MaxTurnsExceeded:
            yield AgentEvent("error", {"message": "工具呼叫次數過多，請把問題拆小一點再試。"})
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
        finally:
            try:
                RUN.reset(token)
            except ValueError:
                # 產生器在不同的 context 被關閉（例如連線中斷）：該 context 隨請求結束
                pass
