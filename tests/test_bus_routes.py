import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.modules.bus import bus
from templates.messages.bus_message import bus_carousel
from templates.messages.flex_theme import FLEX_THEME

QUERY = {"day": "current", "bus_type": "all", "direction": "up"}
NOW = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)


def arrival(**changes):
    return {
        "arrive_time": "08:10",
        "dep_time": "08:05",
        "dep_stop": "校門",
        "description": "",
        "bus_type": "middle-sized_bus",
        **changes,
    }


@pytest.mark.parametrize(
    "line, family, suffix",
    [
        ("red", "red", "紅線"),
        ("green", "green", "綠線"),
        ("route_1", "blue", "路線一"),
        ("route_2", "blue", "路線二"),
    ],
)
def test_route_cards_use_semantic_color_and_text_label(line, family, suffix):
    card = bus_carousel([arrival(line=line)], "綜二館", "上山", "@公車")["contents"][0]
    heading = card["header"]["contents"][0]
    badge = heading["contents"][1]
    assert heading["layout"] == "horizontal"
    assert heading["contents"][0]["flex"] == 1
    assert badge["flex"] == 0
    assert badge["paddingTop"] == FLEX_THEME["spacing"]["badge_y"]
    assert badge["contents"][0]["size"] == FLEX_THEME["typography"]["badge"]
    if family == "blue":
        assert badge["contents"][0]["text"] == "藍線"
        assert suffix in card["header"]["contents"][1]["text"]
    else:
        assert suffix in badge["contents"][0]["text"]
    assert badge["backgroundColor"] == FLEX_THEME["routes"][family]["tint"]
    assert badge["contents"][0]["color"] == FLEX_THEME["routes"][family]["ink"]
    assert (
        card["body"]["contents"][0]["contents"][1]["color"] == FLEX_THEME["routes"][family]["ink"]
    )
    assert card["footer"]["contents"][-1]["color"] == FLEX_THEME["colors"]["brand"]


def test_unknown_route_is_neutral_not_inferred_from_bus_type():
    for vehicle in ("large-sized_bus", "middle-sized_bus", "route_83"):
        card = bus_carousel([arrival(bus_type=vehicle)], "綜二館", "上山", "@公車")["contents"][0]
        badge = card["header"]["contents"][0]["contents"][1]
        assert badge["contents"][0]["text"] == "路線待確認"
        assert badge["contents"][0]["color"] == FLEX_THEME["colors"]["muted"]


async def test_matching_departures_normalizes_time_and_does_not_mutate_sources():
    arrivals = [arrival(), arrival(dep_time="08:20", bus_type="large-sized_bus")]
    schedules = [
        {
            "time": "8:05",
            "line": "red",
            "dep_stop": "校門",
            "bus_type": "middle-sized_bus",
            "description": "",
        },
        {
            "time": "8:20",
            "line": "route_2",
            "dep_stop": "校門",
            "bus_type": "large-sized_bus",
            "description": "",
        },
    ]
    originals = copy.deepcopy((arrivals, schedules))
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=schedules)) as get:
        result = await bus._with_route_labels(arrivals, QUERY, NOW)
    assert [item["line"] for item in result] == ["red", "route_2"]
    assert (arrivals, schedules) == originals
    get.assert_awaited_once_with(
        "/buses/schedules",
        params={
            "bus_type": "all",
            "day": "weekday",
            "direction": "up",
            "time": "08:05",
            "limits": bus.ROUTE_LOOKUP_LIMIT,
        },
        cache=False,
    )


async def test_known_routes_do_not_make_an_extra_request():
    arrivals = [arrival(line="green")]
    with patch.object(bus.nthuapi, "get", new=AsyncMock()) as get:
        result = await bus._with_route_labels(arrivals, QUERY, NOW)
    get.assert_not_awaited()
    assert result == arrivals and result[0] is not arrivals[0]


async def test_ambiguous_or_missing_departures_remain_unknown():
    schedules = [
        {
            "time": "8:05",
            "line": line,
            "dep_stop": "校門",
            "bus_type": "middle-sized_bus",
            "description": "",
        }
        for line in ("red", "green")
    ]
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=schedules)):
        result = await bus._with_route_labels(
            [arrival(), arrival(dep_stop="綜二"), arrival(dep_time="invalid")], QUERY, NOW
        )
    assert all("line" not in item for item in result)


async def test_lookup_failure_is_logged_and_visible_without_losing_arrival_data():
    with (
        patch.object(bus.nthuapi, "get", new=AsyncMock(side_effect=ValueError("unavailable"))),
        patch.object(bus.logger, "warning") as warning,
    ):
        result = await bus._with_route_labels([arrival()], QUERY, NOW)
    warning.assert_called_once()
    assert result[0]["arrive_time"] == "08:10"
    card = bus_carousel(result, "綜二館", "上山", "@公車")["contents"][0]
    assert card["header"]["contents"][0]["contents"][1]["contents"][0]["text"] == "路線暫無資料"


async def test_weekend_lookup_uses_timetable_day_and_earliest_departure():
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[])) as get:
        await bus._with_route_labels(
            [arrival(dep_time="09:20"), arrival(dep_time="08:05")],
            QUERY,
            datetime(2026, 10, 4, 8, 0),
        )
    assert get.await_args.kwargs["params"]["day"] == "weekend"
    assert get.await_args.kwargs["params"]["time"] == "08:05"


def test_direction_card_keeps_existing_postbacks_and_quick_replies():
    message = bus.select_route(SimpleNamespace(params={}))[0]
    actions = message.contents.footer.contents
    assert [button.action.data for button in actions] == [
        "@公車/選擇站點 direction=up",
        "@公車/選擇站點 direction=down",
    ]
    assert [item.action.data for item in message.quick_reply.items] == [
        button.action.data for button in actions
    ]
