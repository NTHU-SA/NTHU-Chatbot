import asyncio
import gzip
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from agents.tool_context import ToolContext

from src.infrastructure.ai import webpage
from src.infrastructure.ai.agent_runner import AgentRunner, _instructions, _tool_error_message
from src.infrastructure.ai.prompts import build_instructions
from src.infrastructure.ai.run_state import RUN, RunState
from src.infrastructure.ai.webpage import (
    PublicWebTransport,
    _extract_content,
    _public_address,
    _validated_url,
    build_visit_webpage_tool,
)
from tests.fakes import make_settings

URL = "https://www.nthu.edu.tw/news"
BODY = """
<main><article>
<header><h1>校園活動報名資訊</h1></header>
<p>清華大學校園活動於十月十日舉辦，歡迎在學學生報名參加，活動地點為校本部大禮堂。</p>
<p>報名期間為十月一日至十月八日，請於期限內完成報名，錄取結果將由主辦單位通知。</p>
<ul><li>請攜帶學生證以確認報名資格。</li><li>入場前請先至服務台完成報到。</li></ul>
<table><tr><th>場次</th><th>時間</th></tr><tr><td>上午場</td><td>09:00</td></tr></table>
<p>詳細辦法請見<a href="https://www.nthu.edu.tw/details">活動說明</a>。</p>
</article></main>
"""
HTML = f"""<!DOCTYPE html><html><head>
<title>網站名稱與 SEO 雜訊</title><meta name="description" content="搜尋引擎雜訊">
<style>CSS雜訊</style><script>JS雜訊</script></head><body>
<header>全站頁首雜訊</header><nav>導覽選單雜訊 首頁 聯絡我們</nav>
<div role="navigation">角色導覽雜訊</div><div role="dialog">Cookie彈窗雜訊</div>
<aside>廣告側欄雜訊</aside><form>搜尋表單雜訊</form>
{BODY}
<div hidden>隱藏內容雜訊</div><div aria-hidden="true">輔助隱藏雜訊</div>
<section id="comments"><p>留言雜訊：這篇文章真是太棒了！</p></section>
<footer>頁尾版權雜訊 聯絡地址</footer></body></html>"""


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.read_count = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk

    async def aclose(self):
        pass


def streamed_html(content=HTML.encode(), *, headers=None):
    return httpx.Response(
        200,
        headers=headers or {"Content-Type": "text/html; charset=utf-8"},
        stream=httpx.ByteStream(content),
    )


@pytest.fixture
def run_state():
    state = RunState(max_tool_calls=2, max_web_searches=0)
    token = RUN.set(state)
    yield state
    RUN.reset(token)


@pytest.fixture
def mock_web(monkeypatch):
    calls = []
    responses = {}

    def handler(request):
        calls.append(request)
        response = responses.get(str(request.url))
        if isinstance(response, Exception):
            raise response
        return response or streamed_html()

    monkeypatch.setattr(webpage, "PublicWebTransport", lambda: httpx.MockTransport(handler))
    return calls, responses


async def invoke(url=URL, *, max_chars=6000):
    tool = build_visit_webpage_tool(max_chars, _tool_error_message)
    args = json.dumps({"url": url})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="c", tool_arguments=args)
    return await tool.on_invoke_tool(context, args)


def test_extract_returns_only_main_text_and_keeps_useful_structure():
    text = _extract_content(HTML, 6000)
    for expected in (
        "校園活動報名資訊",
        "十月十日",
        "十月八日",
        "學生證",
        "服務台",
        "上午場",
        "09:00",
        "活動說明",
    ):
        assert expected in text
    assert "\n" in text
    assert "雜訊" not in text
    assert "<" not in text and "https://" not in text
    assert "output_format" not in text and "metadata" not in text


def test_extract_short_page():
    text = _extract_content(
        "<html><body><main><h1>圖書館開放時間</h1><p>平日早上八點至晚上十點。</p></main></body></html>",
        6000,
    )
    assert "平日早上八點至晚上十點。" in text


def test_extract_rejects_empty_or_navigation_only_page():
    with pytest.raises(ValueError, match="找不到可讀取"):
        _extract_content(
            "<html><body><nav>首頁 聯絡我們</nav><script>alert(1)</script></body></html>", 6000
        )


@pytest.mark.parametrize("limit", [10, 100])
def test_text_is_bounded_and_truncation_is_explicit(limit):
    text = _extract_content(HTML, limit)
    assert len(text) <= limit
    assert "截斷" in text
    if limit == 100:
        assert text.startswith("校園活動報名資訊")
        assert text.endswith(webpage.TRUNCATION_NOTE)


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://example.com/news",
        "https://nthu.edu.tw.evil.example/news",
        "https://evilnthu.edu.tw/news",
        "http://www.nthu.edu.tw/news",
        "file:///etc/passwd",
        "https://127.0.0.1/",
        "https://[::1]/",
        "https://user:password@www.nthu.edu.tw/news",
        "https://@www.nthu.edu.tw/news",
        "https://www.nthu.edu.tw:8080/",
        "https://www.nthu.edu.tw:invalid/",
        "https://www.nthu.edu.tw\\@example.com/",
        "https://www.nthu.edu.tw/\nnews",
        "https://www.nthu.edu.tw/\x00news",
        "https://www.nthu.edu.tw/" + "x" * webpage.MAX_URL_CHARS,
    ],
)
async def test_invalid_urls_are_reported_without_network_requests(url, mock_web, run_state):
    calls, _ = mock_web
    result = await invoke(url)
    assert result.startswith("[TOOL_ERROR] ValueError:")
    assert calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://nthu.edu.tw/",
        "https://adms.site.nthu.edu.tw/p/1?Lang=zh-tw",
        "https://WWW.NTHU.EDU.TW:443/news#details",
    ],
)
def test_official_https_urls_are_accepted(url):
    assert _validated_url(url).fragment == ""


async def test_fetch_returns_plain_body_and_marks_external_data(mock_web, run_state):
    calls, _ = mock_web
    text = await invoke()
    assert "十月十日" in text and "雜訊" not in text
    assert not text.startswith("{")
    assert calls[0].headers["accept"] == "text/html, application/xhtml+xml"
    assert calls[0].headers["accept-encoding"] == "identity"
    assert run_state.tainted
    assert run_state.tool_calls == 1 and run_state.web_searches == 0


async def test_fetch_follows_relative_official_redirects(mock_web):
    calls, responses = mock_web
    responses[URL] = httpx.Response(302, headers={"Location": "/news/detail"})
    assert "十月十日" in await invoke()
    assert [str(request.url) for request in calls] == [URL, URL + "/detail"]


@pytest.mark.parametrize(
    "location",
    ["https://example.com/", "http://www.nthu.edu.tw/news", "https://www.nthu.edu.tw:8080/"],
)
async def test_redirects_cannot_escape_restrictions(location, mock_web):
    calls, responses = mock_web
    responses[URL] = httpx.Response(302, headers={"Location": location})
    assert (await invoke()).startswith("[TOOL_ERROR]")
    assert len(calls) == 1


async def test_redirect_loop_is_bounded(mock_web):
    calls, responses = mock_web
    responses[URL] = httpx.Response(302, headers={"Location": URL})
    assert "重新導向次數過多" in await invoke()
    assert len(calls) == webpage.MAX_REDIRECTS + 1


async def test_redirect_without_location_is_reported(mock_web):
    _, responses = mock_web
    responses[URL] = httpx.Response(302)
    assert "沒有提供目的網址" in await invoke()


@pytest.mark.parametrize("status", [401, 403, 404, 500])
async def test_http_failures_are_explicit(status, mock_web):
    _, responses = mock_web
    responses[URL] = httpx.Response(status, text="Internal diagnostic details")
    result = await invoke()
    assert result.startswith("[TOOL_ERROR]")
    assert f"HTTP {status}" in result
    assert "Internal diagnostic" not in result


@pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "text/plain", ""])
async def test_non_html_is_rejected(content_type, mock_web):
    _, responses = mock_web
    responses[URL] = httpx.Response(200, headers={"Content-Type": content_type}, content=b"data")
    assert "不支援 PDF" in await invoke()


async def test_download_size_is_bounded(mock_web, monkeypatch):
    _, responses = mock_web
    monkeypatch.setattr(webpage, "MAX_PAGE_BYTES", 32)
    stream = ChunkedStream(b"x" * 65536, b"not read")
    responses[URL] = httpx.Response(200, headers={"Content-Type": "text/html"}, stream=stream)
    assert "網頁檔案過大" in await invoke()
    assert stream.read_count == 1


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "GZIP", "identity, gzip"])
async def test_compressed_html_is_rejected_before_reading(mock_web, encoding):
    _, responses = mock_web
    compressed = gzip.compress(("<html><body>" + "x" * 10000 + "</body></html>").encode())
    stream = ChunkedStream(compressed)
    responses[URL] = httpx.Response(
        200,
        headers={"Content-Type": "text/html", "Content-Encoding": encoding},
        stream=stream,
    )
    assert "不支援的內容編碼" in await invoke()
    assert stream.read_count == 0


async def test_identity_content_encoding_is_accepted(mock_web):
    _, responses = mock_web
    responses[URL] = streamed_html(
        headers={"Content-Type": "text/html", "Content-Encoding": "identity"}
    )
    assert "校園活動報名資訊" in await invoke()


async def test_declared_download_size_is_bounded_before_reading(mock_web, monkeypatch):
    _, responses = mock_web
    monkeypatch.setattr(webpage, "MAX_PAGE_BYTES", 32)
    stream = ChunkedStream(HTML.encode())
    responses[URL] = httpx.Response(
        200,
        headers={"Content-Type": "text/html", "Content-Length": "33"},
        stream=stream,
    )
    assert "網頁檔案過大" in await invoke()
    assert stream.read_count == 0


async def test_inaccurate_content_length_cannot_bypass_raw_limit(mock_web, monkeypatch):
    _, responses = mock_web
    monkeypatch.setattr(webpage, "MAX_PAGE_BYTES", 32)
    responses[URL] = streamed_html(headers={"Content-Type": "text/html", "Content-Length": "1"})
    assert "網頁檔案過大" in await invoke()


async def test_invalid_content_length_is_rejected(mock_web):
    _, responses = mock_web
    responses[URL] = streamed_html(
        headers={"Content-Type": "text/html", "Content-Length": "invalid"}
    )
    assert "網頁回應長度無效" in await invoke()


async def test_raw_download_accepts_exact_size_limit(mock_web, monkeypatch):
    _, responses = mock_web
    monkeypatch.setattr(webpage, "MAX_PAGE_BYTES", 32)
    responses[URL] = streamed_html(
        b"x" * 32, headers={"Content-Type": "text/html", "Content-Length": "32"}
    )
    assert await webpage._fetch_html(_validated_url(URL)) == b"x" * 32


async def test_raw_download_counts_multiple_chunks(mock_web, monkeypatch):
    _, responses = mock_web
    monkeypatch.setattr(webpage, "MAX_PAGE_BYTES", 65536)
    stream = ChunkedStream(b"x" * 32768, b"x" * 32768, b"x" * 65536, b"not read")
    responses[URL] = httpx.Response(200, headers={"Content-Type": "text/html"}, stream=stream)
    with pytest.raises(ValueError, match="網頁檔案過大"):
        await webpage._fetch_html(_validated_url(URL))
    assert stream.read_count == 3


async def test_html_encoding_from_header_is_honored(mock_web):
    _, responses = mock_web
    responses[URL] = streamed_html(
        ("<html><body>" + BODY + "</body></html>").encode("big5"),
        headers={"Content-Type": "text/html; charset=big5"},
    )
    assert "校園活動報名資訊" in await invoke()


async def test_html_encoding_from_meta_is_honored(mock_web):
    _, responses = mock_web
    responses[URL] = streamed_html(
        ('<html><head><meta charset="big5"></head><body>' + BODY + "</body></html>").encode("big5"),
        headers={"Content-Type": "text/html"},
    )
    assert "校園活動報名資訊" in await invoke()


async def test_connection_failure_is_reported(mock_web):
    _, responses = mock_web
    responses[URL] = httpx.ConnectError("connection refused")
    assert "無法連線" in await invoke()


async def test_total_timeout_is_reported():
    with patch.object(webpage, "_fetch_html", AsyncMock(side_effect=TimeoutError)):
        result = await invoke()
    assert result.startswith("[TOOL_ERROR]") and "逾時" in result


async def test_total_deadline_cancels_slow_fetch(monkeypatch):
    cancelled = False

    async def slow_fetch(url):
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled = True

    monkeypatch.setattr(webpage, "PAGE_TIMEOUT_SECONDS", 0.01)
    with patch.object(webpage, "_fetch_html", slow_fetch):
        result = await invoke()
    assert result.startswith("[TOOL_ERROR]") and "逾時" in result
    assert cancelled


async def test_tool_budget_prevents_additional_requests(mock_web, run_state):
    calls, _ = mock_web
    await invoke()
    await invoke()
    result = await invoke()
    assert result.startswith("[TOOL_ERROR] ToolBudgetExceeded")
    assert len(calls) == 2


def dns_records(*addresses):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443),
        )
        for address in addresses
    ]


@pytest.mark.parametrize(
    "addresses",
    [
        (),
        ("127.0.0.1",),
        ("10.0.0.1",),
        ("169.254.169.254",),
        ("100.64.0.1",),
        ("::1",),
        ("fe80::1",),
        ("::ffff:127.0.0.1",),
        ("224.0.0.1",),
        ("ff02::1",),
        ("fec0::1",),
        ("8.8.8.8", "10.0.0.1"),
    ],
)
async def test_non_public_dns_answers_are_blocked(addresses):
    loop = asyncio.get_running_loop()
    with (
        patch.object(loop, "getaddrinfo", AsyncMock(return_value=dns_records(*addresses))),
        pytest.raises(ValueError, match="內網或非公開"),
    ):
        await _public_address("www.nthu.edu.tw")


async def test_public_ipv4_is_preferred_on_dual_stack_hosts():
    loop = asyncio.get_running_loop()
    with patch.object(
        loop, "getaddrinfo", AsyncMock(return_value=dns_records("2606:4700:4700::1111", "8.8.8.8"))
    ):
        assert await _public_address("www.nthu.edu.tw") == "8.8.8.8"


async def test_ipv6_only_public_host_is_supported():
    loop = asyncio.get_running_loop()
    with patch.object(
        loop, "getaddrinfo", AsyncMock(return_value=dns_records("2606:4700:4700::1111"))
    ):
        assert await _public_address("www.nthu.edu.tw") == "2606:4700:4700::1111"


def test_pinned_ip_connections_are_not_shared_between_tls_names():
    with patch.object(httpx.AsyncHTTPTransport, "__init__", return_value=None) as initialize:
        PublicWebTransport()
    assert initialize.call_args.kwargs["limits"].max_keepalive_connections == 0


async def test_transport_pins_public_ip_preserving_host_and_tls_name():
    loop = asyncio.get_running_loop()
    request = httpx.Request("GET", URL)
    with (
        patch.object(loop, "getaddrinfo", AsyncMock(return_value=dns_records("8.8.8.8"))) as dns,
        patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            AsyncMock(return_value=httpx.Response(200)),
        ) as send,
    ):
        async with PublicWebTransport() as transport:
            await transport.handle_async_request(request)
    pinned = send.await_args.args[0]
    assert pinned.url.host == "8.8.8.8"
    assert pinned.headers["host"] == "www.nthu.edu.tw"
    assert pinned.extensions["sni_hostname"] == "www.nthu.edu.tw"
    assert dns.await_count == 1


async def test_transport_blocks_private_ip_before_connecting():
    with (
        patch.object(webpage, "_public_address", AsyncMock(side_effect=ValueError("內網"))),
        patch.object(httpx.AsyncHTTPTransport, "handle_async_request", AsyncMock()) as send,
    ):
        async with PublicWebTransport() as transport:
            with pytest.raises(ValueError, match="內網"):
                await transport.handle_async_request(httpx.Request("GET", URL))
    send.assert_not_awaited()


@pytest.mark.parametrize("responses", [False, True])
def test_tool_is_registered_independently_of_search_and_prompts_agent(responses):
    runner = AgentRunner(
        make_settings(
            openai_use_responses_api=responses,
            web_search_domains=("example.com",),
            max_tool_output_chars=100,
        )
    )
    tool = next(tool for tool in runner._agent.tools if tool.name == "visit_webpage")
    assert "非清大網址不要呼叫" in tool.description
    assert "不要重送相同請求" in tool.description
    assert "nthu.edu.tw" in tool.description and "example.com" not in tool.description
    assert runner._tool_titles[tool.name] == "讀取清大網頁內文"
    assert "visit_webpage" not in build_instructions()
    instructions = _instructions(SimpleNamespace(context=None), runner._agent)
    assert "visit_webpage 只能讀取清大官方 HTTPS 網頁" in instructions
    assert "不要猜測網址" in instructions
    assert "已知附件不要送出請求" in instructions
    assert "不要重送相同請求" in instructions
