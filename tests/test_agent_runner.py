import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.infrastructure.ai import agent_runner
from src.infrastructure.ai.agent_runner import AgentRunner
from tests.fakes import make_settings


def raw(type_, **kw):
    return SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type=type_, **kw))


def item(name, **kw):
    return SimpleNamespace(type="run_item_stream_event", name=name, item=SimpleNamespace(**kw))


def fake_run(events, final_output="answer"):
    async def stream_events():
        for event in events:
            yield event

    return SimpleNamespace(stream_events=stream_events, final_output=final_output)


class AgentRunnerStreamTests(unittest.IsolatedAsyncioTestCase):
    def runner(self, **overrides) -> AgentRunner:
        runner = AgentRunner(make_settings(**overrides))
        runner._connected = True  # 不連真的 MCP
        return runner

    async def collect(self, runner, events, final_output="answer"):
        with patch.object(agent_runner.Runner, "run_streamed", return_value=fake_run(events, final_output)):
            return [event async for event in runner.stream([], "hi")]

    async def test_reasoning_summary_is_forwarded_as_thinking(self):
        events = [
            raw("response.reasoning_summary_part.added"),
            raw("response.reasoning_summary_text.delta", delta="先查"),
            raw("response.reasoning_summary_text.delta", delta="公車"),
            raw("response.reasoning_summary_part.added"),
            raw("response.reasoning_summary_text.delta", delta="再整理"),
            raw("response.output_text.delta", delta="好"),
        ]
        out = await self.collect(self.runner(), events, "好")
        self.assertEqual(
            [(e.type, e.data.get("delta")) for e in out[:-1]],
            [
                ("thinking", "先查"),
                ("thinking", "公車"),
                ("thinking", "\n\n"),
                ("thinking", "再整理"),
                ("token", "好"),
            ],
        )
        self.assertEqual(out[-1].type, "done")
        self.assertEqual(out[-1].data["content"], "好")

    async def test_tool_calls_are_paired_and_persisted(self):
        events = [
            item("tool_called", raw_item=SimpleNamespace(call_id="c1", name="get_next_buses", arguments='{"route":"nanda"}')),
            item("tool_output", raw_item={"call_id": "c1"}, output=[{"type": "text", "text": "{}"}]),
            raw("response.output_text.delta", delta="17:00"),
        ]
        out = await self.collect(self.runner(), events, "17:00")
        self.assertEqual([e.type for e in out], ["tool_call_start", "tool_call_end", "token", "done"])
        self.assertEqual(out[0].data, {"call_id": "c1", "name": "get_next_buses", "args": {"route": "nanda"}})
        self.assertTrue(out[1].data["ok"])
        self.assertEqual(out[-1].data["tool_calls"][0]["name"], "get_next_buses")

    def test_reasoning_summary_only_requested_for_responses_api(self):
        on = self.runner(openai_use_responses_api=True, reasoning_summary=True)
        self.assertEqual(on._agent.model_settings.reasoning.summary, "auto")
        for overrides in ({"reasoning_summary": True}, {"openai_use_responses_api": True}):
            self.assertIsNone(self.runner(**overrides)._agent.model_settings.reasoning)
