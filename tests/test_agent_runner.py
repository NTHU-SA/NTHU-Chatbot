from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.infrastructure.ai import agent_runner
from src.infrastructure.ai.agent_runner import AgentRunner
from tests.fakes import make_settings


def raw(type_, **kw):
    return SimpleNamespace(type="raw_response_event", data=SimpleNamespace(type=type_, **kw))


def item(name, **kw):
    return SimpleNamespace(type="run_item_stream_event", name=name, item=SimpleNamespace(**kw))


def called(call_id, name, arguments="{}"):
    return item(
        "tool_called",
        raw_item=SimpleNamespace(call_id=call_id, name=name, arguments=arguments),
    )


def output(call_id, value):
    return item("tool_output", raw_item={"call_id": call_id}, output=value)


def fake_run(events, final_output="answer"):
    async def stream_events():
        for event in events:
            yield event

    return SimpleNamespace(stream_events=stream_events, final_output=final_output)


def make_runner(**overrides) -> AgentRunner:
    runner = AgentRunner(make_settings(**overrides))
    runner._connected = True  # 不連真的 MCP
    return runner


async def collect(runner, events, final_output="answer"):
    with patch.object(
        agent_runner.Runner, "run_streamed", return_value=fake_run(events, final_output)
    ):
        return [event async for event in runner.stream([], "hi")]


def types(events) -> list[str]:
    return [e.type for e in events]


async def test_reasoning_summary_is_forwarded_as_thinking():
    events = [
        raw("response.reasoning_summary_part.added"),
        raw("response.reasoning_summary_text.delta", delta="先查"),
        raw("response.reasoning_summary_text.delta", delta="公車"),
        raw("response.reasoning_summary_part.added"),
        raw("response.reasoning_summary_text.delta", delta="再整理"),
        raw("response.output_text.delta", delta="好"),
    ]
    out = await collect(make_runner(), events, "好")
    assert [(e.type, e.data.get("delta")) for e in out[:-1]] == [
        ("thinking", "先查"),
        ("thinking", "公車"),
        ("thinking", "\n\n"),
        ("thinking", "再整理"),
        ("token", "好"),
    ]
    assert out[-1].type == "done"
    assert out[-1].data["content"] == "好"


async def test_tool_calls_are_paired_and_persisted():
    events = [
        called("c1", "get_next_buses", '{"route":"nanda"}'),
        output("c1", [{"type": "text", "text": "{}"}]),
        raw("response.output_text.delta", delta="17:00"),
    ]
    out = await collect(make_runner(), events, "17:00")
    assert types(out) == ["tool_call_start", "tool_call_end", "token", "done"]
    assert out[0].data == {"call_id": "c1", "name": "get_next_buses", "args": {"route": "nanda"}}
    assert out[1].data["ok"]
    assert out[-1].data["tool_calls"][0]["name"] == "get_next_buses"


async def test_text_before_tool_call_becomes_interim_not_answer():
    events = [
        raw("response.output_text.delta", delta="本汪查一下！"),
        called("c1", "get_next_buses"),
        output("c1", "{}"),
        raw("response.output_text.delta", delta="下一班 17:00"),
    ]
    out = await collect(make_runner(), events, "下一班 17:00")
    assert types(out) == ["token", "interim", "tool_call_start", "tool_call_end", "token", "done"]
    assert out[1].data == {"text": "本汪查一下！", "discard": False}
    assert out[-1].data["content"] == "下一班 17:00"


SUGGEST_ARGS = '{"options": ["北校門口", "綜二館"]}'


async def test_question_before_suggest_replies_stays_the_answer():
    events = [
        raw("response.output_text.delta", delta="你在哪一站？"),
        called("s1", "suggest_replies", SUGGEST_ARGS),
        output("s1", "ok"),
    ]
    out = await collect(make_runner(), events, "你在哪一站？")
    assert types(out) == ["token", "suggestions", "done"]
    assert out[-1].data["content"] == "你在哪一站？"


async def test_question_repeated_after_suggest_replies_is_not_duplicated():
    events = [
        raw("response.output_text.delta", delta="你在哪一站？"),
        called("s1", "suggest_replies", SUGGEST_ARGS),
        output("s1", "ok"),
        raw("response.output_text.delta", delta="你目前在哪一站呢？"),
    ]
    out = await collect(make_runner(), events, "你目前在哪一站呢？")
    assert types(out) == ["token", "suggestions", "interim", "token", "done"]
    assert out[2].data == {"text": "你在哪一站？", "discard": True}
    assert out[-1].data["content"] == "你目前在哪一站呢？"


async def test_tool_call_without_final_text_falls_back_to_last_remark():
    events = [
        raw("response.output_text.delta", delta="本汪查一下！"),
        called("c1", "get_next_buses"),
        output("c1", "{}"),
    ]
    out = await collect(make_runner(), events, "")
    assert out[-1].data["content"] == "本汪查一下！"


async def test_suggest_replies_becomes_suggestions_not_a_tool_card():
    options = '{"options": [" 北校門 ", "綜二館", "北校門", "", "其他地點", "請輸入站名", "台積館", "南門", "多的"]}'
    events = [
        called("s1", "suggest_replies", options),
        output("s1", "ok"),
        raw("response.output_text.delta", delta="你在哪一站？"),
    ]
    out = await collect(make_runner(), events, "你在哪一站？")
    cleaned = ["北校門", "綜二館", "台積館", "南門"]
    assert types(out) == ["suggestions", "token", "done"]
    assert out[0].data["options"] == cleaned
    assert out[-1].data["tool_calls"] == [
        {
            "name": "suggest_replies",
            "args": {"options": cleaned},
            "result_preview": None,
            "duration_ms": None,
            "ok": True,
        }
    ]


def test_suggest_replies_tool_is_registered():
    assert "suggest_replies" in [tool.name for tool in make_runner()._agent.tools]


def test_reasoning_summary_only_requested_for_responses_api():
    on = make_runner(openai_use_responses_api=True, reasoning_summary=True)
    assert on._agent.model_settings.reasoning.summary == "auto"
    for overrides in ({"reasoning_summary": True}, {"openai_use_responses_api": True}):
        assert make_runner(**overrides)._agent.model_settings.reasoning is None


@pytest.mark.parametrize("responses", [False, True])
@pytest.mark.parametrize("reasoning", [False, True])
def test_output_token_limit_applies_to_both_apis_with_reasoning(responses, reasoning):
    runner = make_runner(
        openai_use_responses_api=responses,
        reasoning_summary=reasoning,
        max_output_tokens=1234,
    )
    assert runner._agent.model_settings.max_tokens == 1234


LONG_TEXT = "這是很長的回答"


@pytest.mark.parametrize(
    "events",
    [
        [],
        [raw("response.output_text.delta", delta=LONG_TEXT)],
        [raw("response.output_text.delta", delta=LONG_TEXT), called("c1", "get_next_buses")],
        [
            raw("response.output_text.delta", delta=LONG_TEXT),
            called("s1", "suggest_replies", '{"options": ["北校門", "南門"]}'),
        ],
    ],
    ids=["final-output", "streamed", "interim", "pending-question"],
)
async def test_final_content_bound_includes_all_fallbacks(events):
    out = await collect(make_runner(max_output_chars=5), events, LONG_TEXT)
    assert out[-1].type == "done"
    assert out[-1].data["content"] == LONG_TEXT[:5]


async def test_short_content_is_unchanged():
    out = await collect(make_runner(max_output_chars=5), [], "短回答")
    assert out[-1].data["content"] == "短回答"
