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

    async def test_text_before_tool_call_becomes_interim_not_answer(self):
        events = [
            raw("response.output_text.delta", delta="本汪查一下！"),
            item("tool_called", raw_item=SimpleNamespace(call_id="c1", name="get_next_buses", arguments="{}")),
            item("tool_output", raw_item={"call_id": "c1"}, output="{}"),
            raw("response.output_text.delta", delta="下一班 17:00"),
        ]
        out = await self.collect(self.runner(), events, "下一班 17:00")
        self.assertEqual(
            [e.type for e in out],
            ["token", "interim", "tool_call_start", "tool_call_end", "token", "done"],
        )
        self.assertEqual(out[1].data, {"text": "本汪查一下！", "discard": False})
        self.assertEqual(out[-1].data["content"], "下一班 17:00")

    async def test_question_before_suggest_replies_stays_the_answer(self):
        events = [
            raw("response.output_text.delta", delta="你在哪一站？"),
            item("tool_called", raw_item=SimpleNamespace(call_id="s1", name="suggest_replies", arguments='{"options": ["北校門口", "綜二館"]}')),
            item("tool_output", raw_item={"call_id": "s1"}, output="ok"),
        ]
        out = await self.collect(self.runner(), events, "你在哪一站？")
        self.assertEqual([e.type for e in out], ["token", "suggestions", "done"])
        self.assertEqual(out[-1].data["content"], "你在哪一站？")

    async def test_question_repeated_after_suggest_replies_is_not_duplicated(self):
        events = [
            raw("response.output_text.delta", delta="你在哪一站？"),
            item("tool_called", raw_item=SimpleNamespace(call_id="s1", name="suggest_replies", arguments='{"options": ["北校門口", "綜二館"]}')),
            item("tool_output", raw_item={"call_id": "s1"}, output="ok"),
            raw("response.output_text.delta", delta="你目前在哪一站呢？"),
        ]
        out = await self.collect(self.runner(), events, "你目前在哪一站呢？")
        self.assertEqual([e.type for e in out], ["token", "suggestions", "interim", "token", "done"])
        self.assertEqual(out[2].data, {"text": "你在哪一站？", "discard": True})
        self.assertEqual(out[-1].data["content"], "你目前在哪一站呢？")

    async def test_tool_call_without_final_text_falls_back_to_last_remark(self):
        events = [
            raw("response.output_text.delta", delta="本汪查一下！"),
            item("tool_called", raw_item=SimpleNamespace(call_id="c1", name="get_next_buses", arguments="{}")),
            item("tool_output", raw_item={"call_id": "c1"}, output="{}"),
        ]
        out = await self.collect(self.runner(), events, "")
        self.assertEqual(out[-1].data["content"], "本汪查一下！")

    async def test_suggest_replies_becomes_suggestions_not_a_tool_card(self):
        events = [
            item(
                "tool_called",
                raw_item=SimpleNamespace(
                    call_id="s1",
                    name="suggest_replies",
                    arguments='{"options": [" 北校門 ", "綜二館", "北校門", "", "其他地點", "請輸入站名", "台積館", "南門", "多的"]}',
                ),
            ),
            item("tool_output", raw_item={"call_id": "s1"}, output="ok"),
            raw("response.output_text.delta", delta="你在哪一站？"),
        ]
        out = await self.collect(self.runner(), events, "你在哪一站？")
        self.assertEqual([e.type for e in out], ["suggestions", "token", "done"])
        self.assertEqual(out[0].data["options"], ["北校門", "綜二館", "台積館", "南門"])
        self.assertEqual(
            out[-1].data["tool_calls"],
            [{"name": "suggest_replies", "args": {"options": ["北校門", "綜二館", "台積館", "南門"]},
              "result_preview": None, "duration_ms": None, "ok": True}],
        )

    def test_suggest_replies_tool_is_registered(self):
        names = [tool.name for tool in self.runner()._agent.tools]
        self.assertIn("suggest_replies", names)

    def test_reasoning_summary_only_requested_for_responses_api(self):
        on = self.runner(openai_use_responses_api=True, reasoning_summary=True)
        self.assertEqual(on._agent.model_settings.reasoning.summary, "auto")
        for overrides in ({"reasoning_summary": True}, {"openai_use_responses_api": True}):
            self.assertIsNone(self.runner(**overrides)._agent.model_settings.reasoning)

    def test_output_token_limit_applies_to_both_apis_with_reasoning(self):
        for responses in (False, True):
            for reasoning in (False, True):
                with self.subTest(responses=responses, reasoning=reasoning):
                    runner = self.runner(
                        openai_use_responses_api=responses,
                        reasoning_summary=reasoning,
                        max_output_tokens=1234,
                    )
                    self.assertEqual(runner._agent.model_settings.max_tokens, 1234)

    async def test_final_content_bound_includes_all_fallbacks(self):
        text = "這是很長的回答"
        tool = item(
            "tool_called",
            raw_item=SimpleNamespace(call_id="c1", name="get_next_buses", arguments="{}"),
        )
        suggestion = item(
            "tool_called",
            raw_item=SimpleNamespace(
                call_id="s1", name="suggest_replies",
                arguments='{"options": ["北校門", "南門"]}',
            ),
        )
        for events in (
            [],
            [raw("response.output_text.delta", delta=text)],
            [raw("response.output_text.delta", delta=text), tool],
            [raw("response.output_text.delta", delta=text), suggestion],
        ):
            with self.subTest(events=events):
                out = await self.collect(self.runner(max_output_chars=5), events, text)
                self.assertEqual(out[-1].type, "done")
                self.assertEqual(out[-1].data["content"], text[:5])

    async def test_short_content_is_unchanged(self):
        out = await self.collect(self.runner(max_output_chars=5), [], "短回答")
        self.assertEqual(out[-1].data["content"], "短回答")
