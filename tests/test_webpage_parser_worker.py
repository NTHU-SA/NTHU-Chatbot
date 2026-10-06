import asyncio
import json

import pytest
from agents.tool_context import ToolContext

from src.infrastructure.ai import webpage
from src.infrastructure.ai.agent_runner import _tool_error_message

URL = "https://www.nthu.edu.tw/news"
HTML = (
    "<html><body><main><h1>圖書館開放時間</h1><p>平日早上八點至晚上十點。</p></main></body></html>"
)


async def invoke():
    tool = webpage.build_visit_webpage_tool(6000, _tool_error_message)
    args = json.dumps({"url": URL})
    context = ToolContext(context=None, tool_name=tool.name, tool_call_id="c", tool_arguments=args)
    return await tool.on_invoke_tool(context, args)


@pytest.fixture
def running_worker(monkeypatch):
    monkeypatch.setattr(webpage, "PARSER_WORKER_MODULE", "tests.webpage_parser_hanging_worker")
    original_spawn = asyncio.create_subprocess_exec
    workers = []
    started = asyncio.Event()

    async def spawn(*args, **kwargs):
        worker = await original_spawn(*args, **kwargs)
        workers.append(worker)

        async def communicate(input=None):
            worker.stdin.write(input)
            await worker.stdin.drain()
            worker.stdin.close()
            assert await worker.stdout.readline() == b"STARTED\n"
            started.set()
            result = await worker.stdout.read()
            await worker.wait()
            return result, None

        worker.communicate = communicate
        return worker

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return workers, started


async def test_worker_preserves_success_and_errors():
    text = await webpage._extract_in_worker(HTML, 6000)
    assert "平日早上八點至晚上十點。" in text
    assert "<" not in text
    with pytest.raises(ValueError, match="找不到可讀取"):
        await webpage._extract_in_worker("<html><body><nav>首頁</nav></body></html>", 6000)


async def test_worker_startup_failure_is_explicit(monkeypatch):
    monkeypatch.setattr(webpage, "PARSER_WORKER_MODULE", "src.infrastructure.ai.missing_parser")
    with pytest.raises(ValueError, match="無法解析"):
        await webpage._extract_in_worker(HTML, 6000)


async def test_tool_timeout_kills_and_reaps_running_worker(monkeypatch, running_worker):
    workers, started = running_worker

    async def fetch(url):
        return HTML

    monkeypatch.setattr(webpage, "_fetch_html", fetch)
    monkeypatch.setattr(webpage, "PAGE_TIMEOUT_SECONDS", 1)
    try:
        result = await asyncio.wait_for(invoke(), 3)
        assert started.is_set()
        assert result.startswith("[TOOL_ERROR]") and "逾時" in result
        assert len(workers) == 1
        assert workers[0].returncode is not None
        assert (await asyncio.wait_for(workers[0].wait(), 0.1)) == workers[0].returncode
    finally:
        if workers and workers[0].returncode is None:
            workers[0].kill()
            await workers[0].wait()


async def test_external_cancellation_kills_and_reaps_running_worker(running_worker):
    workers, started = running_worker
    task = asyncio.create_task(webpage._extract_in_worker(HTML, 6000))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert workers[0].returncode is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert workers[0].returncode is not None
        assert (await asyncio.wait_for(workers[0].wait(), 0.1)) == workers[0].returncode
    finally:
        if workers and workers[0].returncode is None:
            workers[0].kill()
            await workers[0].wait()


async def test_cancellation_during_spawn_still_reaps_worker(monkeypatch):
    monkeypatch.setattr(webpage, "PARSER_WORKER_MODULE", "tests.webpage_parser_hanging_worker")
    original_spawn = asyncio.create_subprocess_exec
    workers = []
    spawned = asyncio.Event()
    release_handle = asyncio.Event()

    async def spawn(*args, **kwargs):
        worker = await original_spawn(*args, **kwargs)
        workers.append(worker)
        spawned.set()
        await release_handle.wait()
        return worker

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(webpage._extract_in_worker(HTML, 6000))
    try:
        await asyncio.wait_for(spawned.wait(), 3)
        task.cancel()
        await asyncio.sleep(0)
        release_handle.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert workers[0].returncode is not None
        assert (await asyncio.wait_for(workers[0].wait(), 0.1)) == workers[0].returncode
    finally:
        release_handle.set()
        if workers and workers[0].returncode is None:
            workers[0].kill()
            await workers[0].wait()
