import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from agents.tool_context import ToolContext
from mcp.types import CallToolResult, TextContent

from src.core.config import Settings
from src.infrastructure.ai import agent_runner
from src.infrastructure.ai.agent_runner import (
    AgentRunner,
    BoundedMCPServer,
    _tool_error_message,
)
from src.infrastructure.ai.run_state import (
    RUN,
    RunState,
    ToolBudgetExceeded,
    is_tainted,
)
from src.infrastructure.ai.web_search import build_web_search_tool
from tests.fakes import make_settings


@pytest.fixture
def run_state():
    state = RunState(max_tool_calls=3, max_web_searches=1)
    token = RUN.set(state)
    yield state
    RUN.reset(token)


@pytest.fixture
def server():
    """不連線的 BoundedMCPServer；底層 call_tool 換成計數的假實作。"""
    instance = BoundedMCPServer.__new__(BoundedMCPServer)
    instance._max_output_chars = 50
    instance._cache = {}
    calls = []

    async def fake_call(self, tool_name, arguments, meta=None):
        calls.append((tool_name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=f"{tool_name}:{len(calls)}")])

    with patch.object(agent_runner.MCPServerStreamableHttp, "call_tool", fake_call):
        yield instance, calls


# -- MCP cache --
async def test_cacheable_results_are_reused_and_isolated(server, run_state):
    mcp, calls = server
    first = await mcp.call_tool("get_announcements", {"department": "x", "limit": 5})
    first.content[0].text = "mutated by caller"
    second = await mcp.call_tool(
        "get_announcements", {"limit": 5, "department": "x"}
    )  # 參數順序不同
    assert len(calls) == 1
    assert second.content[0].text == "get_announcements:1"
    await mcp.call_tool("get_announcements", {"department": "y"})
    assert len(calls) == 2


async def test_live_bus_data_is_never_cached(server, run_state):
    mcp, calls = server
    await mcp.call_tool("get_bus_schedule", {"stop": "綜二館", "day": "current", "details": True})
    await mcp.call_tool("get_bus_schedule", {"stop": "綜二館", "day": "current", "details": True})
    assert len(calls) == 2


async def test_cache_expires(server, run_state):
    mcp, calls = server
    with patch.object(agent_runner.time, "monotonic", return_value=1000.0):
        await mcp.call_tool("find_dining", {})
    with patch.object(agent_runner.time, "monotonic", return_value=1000.0 + 301):
        await mcp.call_tool("find_dining", {})
    assert len(calls) == 2


async def test_error_results_are_not_cached(run_state):
    mcp = BoundedMCPServer.__new__(BoundedMCPServer)
    mcp._max_output_chars = 50
    mcp._cache = {}
    error = CallToolResult(content=[TextContent(type="text", text="boom")], isError=True)
    with patch.object(
        agent_runner.MCPServerStreamableHttp, "call_tool", AsyncMock(return_value=error)
    ) as call:
        await mcp.call_tool("search_courses", {"q": "x"})
        await mcp.call_tool("search_courses", {"q": "x"})
    assert call.await_count == 2


async def test_results_are_truncated_before_reaching_the_model(server, run_state):
    mcp, _ = server
    with patch.object(
        agent_runner.MCPServerStreamableHttp,
        "call_tool",
        AsyncMock(return_value=CallToolResult(content=[TextContent(type="text", text="x" * 500)])),
    ):
        result = await mcp.call_tool("get_bus_schedule", {})
    assert result.content[0].text.startswith("x" * 50)
    assert "已截斷" in result.content[0].text


# -- per-message budget --
async def test_tool_budget_counts_cache_hits_and_marks_the_run(server, run_state):
    mcp, calls = server
    for _ in range(3):
        await mcp.call_tool("search_campus", {"q": "圖書館"})
    assert run_state.tool_calls == 3 and run_state.tainted
    assert len(calls) == 1  # 後兩次是快取命中，仍然計次
    with pytest.raises(ToolBudgetExceeded):
        await mcp.call_tool("search_campus", {"q": "圖書館"})


async def test_no_run_state_means_no_limit(server):
    mcp, _ = server
    for _ in range(10):
        await mcp.call_tool("get_bus_schedule", {})
    assert not is_tainted()


# -- web search --
def citation(url, title="t"):
    return SimpleNamespace(type="url_citation", url=url, title=title)


def fake_response():
    content = SimpleNamespace(
        annotations=[
            citation("https://www.nthu.edu.tw/news/1", "清大新聞"),
            citation("https://adms.site.nthu.edu.tw/p/1", "註冊組"),
            citation("https://nthu.edu.tw.evil.example/x"),  # 網域偽裝
            citation("http://www.nthu.edu.tw/insecure"),  # 非 https
            citation("https://www.nthu.edu.tw/news/1"),  # 重複
        ]
    )
    return SimpleNamespace(output_text="摘要 [1]", output=[SimpleNamespace(content=[content])])


async def call_search(tool, query="註冊時間"):
    args = json.dumps({"query": query}, ensure_ascii=False)
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="c", tool_arguments=args)
    return await tool.on_invoke_tool(context, args)


async def test_web_search_returns_only_allowed_https_sources(run_state):
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=fake_response()))
    )
    tool = build_web_search_tool(client, "test-model", ("nthu.edu.tw",), _tool_error_message)
    assert tool.name == "nthu_web_search"
    result = json.loads(await call_search(tool, "註冊\n時間<script>"))
    assert result["summary"] == "摘要 [1]"
    assert [s["url"] for s in result["sources"]] == [
        "https://www.nthu.edu.tw/news/1",
        "https://adms.site.nthu.edu.tw/p/1",
    ]
    request = client.responses.create.await_args.kwargs
    assert request["tools"][0]["filters"] == {"allowed_domains": ["nthu.edu.tw"]}
    assert request["input"] == "註冊 時間 script"
    assert run_state.tainted and run_state.web_searches == 1


async def test_web_search_limit_is_reported_to_the_model(run_state):
    client = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=fake_response()))
    )
    tool = build_web_search_tool(client, "m", ("nthu.edu.tw",), _tool_error_message)
    await call_search(tool)
    second = await call_search(tool)
    assert second.startswith("[TOOL_ERROR] ToolBudgetExceeded")
    assert client.responses.create.await_count == 1


# -- settings / wiring --
BASE = {
    "LINE_CHANNEL_SECRET": "s",
    "LINE_CHANNEL_ACCESS_TOKEN": "t",
    "LINE_LOGIN_CHANNEL_ID": "1",
    "LIFF_ID": "l",
    "OPENAI_API_KEY": "k",
    "CHAT_STORE": "memory",
}


def test_tool_settings_defaults_and_validation():
    with patch.dict(os.environ, BASE, clear=True):
        settings = Settings.from_env()
    assert settings.max_tool_calls_per_message == 6
    assert not settings.web_search_enabled
    assert settings.web_search_domains == ("nthu.edu.tw",)
    with patch.dict(
        os.environ, {**BASE, "WEB_SEARCH_DOMAINS": "nthu.edu.tw, NCTU.edu.tw"}, clear=True
    ):
        assert Settings.from_env().web_search_domains == ("nthu.edu.tw", "nctu.edu.tw")
    for bad in ("https://nthu.edu.tw", "*.nthu.edu.tw", "nthu"):
        with (
            patch.dict(os.environ, {**BASE, "WEB_SEARCH_DOMAINS": bad}, clear=True),
            pytest.raises(RuntimeError, match="WEB_SEARCH_DOMAINS"),
        ):
            Settings.from_env()


def tool_names(runner):
    return {tool.name for tool in runner._agent.tools}


def test_web_search_requires_responses_api():
    assert "nthu_web_search" not in tool_names(AgentRunner(make_settings()))
    assert "nthu_web_search" not in tool_names(AgentRunner(make_settings(web_search_enabled=True)))
    enabled = AgentRunner(make_settings(web_search_enabled=True, openai_use_responses_api=True))
    assert "nthu_web_search" in tool_names(enabled)
    assert "web_search" not in tool_names(enabled)
    instructions = agent_runner._instructions(SimpleNamespace(context=None), enabled._agent)
    assert "nthu_web_search 只會搜尋清大官方網站" in instructions
    plain = AgentRunner(make_settings())
    assert "網路搜尋" not in agent_runner._instructions(SimpleNamespace(context=None), plain._agent)


def test_parallel_tool_calls_enabled():
    assert AgentRunner(make_settings())._agent.model_settings.parallel_tool_calls is True
