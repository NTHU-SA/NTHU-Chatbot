import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.modules.bus import bus
from templates.messages.bus_message import bus_carousel
from templates.messages.flex_theme import FLEX_THEME


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
        ("main_red", "red", "紅線"),
        ("main_green", "green", "綠線"),
        ("nanda_route_1", "blue", "路線一"),
        ("nanda_route_2", "blue", "路線二"),
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


def detailed_schedule(line="main_red"):
    return {
        "dep_info": {
            "time": "8:05",
            "line": line,
            "dep_stop": "校門",
            "bus_type": "middle-sized_bus",
            "description": "",
        },
        "stops_time": [
            {"stop": "北校門口", "arrive_time": "08:05"},
            {"stop": "綜二館", "arrive_time": "08:10"},
        ],
    }


def test_selected_stop_time_and_canonical_lines_are_preserved_without_mutation():
    schedules = [detailed_schedule("main_red"), detailed_schedule("nanda_route_2")]
    original = copy.deepcopy(schedules)
    result = bus._stop_arrivals(schedules, "綜二館")
    assert [item["line"] for item in result] == ["main_red", "nanda_route_2"]
    assert all(item["dep_time"] == "8:05" and item["arrive_time"] == "08:10" for item in result)
    result[0]["description"] = "changed"
    assert schedules == original


def test_missing_route_is_not_inferred_from_vehicle_type():
    schedule = detailed_schedule()
    del schedule["dep_info"]["line"]
    result = bus._stop_arrivals([schedule], "綜二館")
    assert result[0]["line"] == ""
    card = bus_carousel(result, "綜二館", "上山", "@公車")["contents"][0]
    assert card["header"]["contents"][0]["contents"][1]["contents"][0]["text"] == "路線待確認"


def test_missing_selected_stop_is_an_explicit_error():
    with pytest.raises(ValueError, match="omitted the requested stop"):
        bus._stop_arrivals([detailed_schedule()], "不存在的站")


@pytest.mark.parametrize("response", [None, {}, {"schedules": []}])
def test_invalid_schedule_response_is_an_explicit_error(response):
    with pytest.raises(ValueError, match="Invalid bus schedule response"):
        bus._stop_arrivals(response, "綜二館")


async def test_canonical_filters_use_one_uncached_request_and_keep_in_transit_buses():
    with patch.object(
        bus.nthuapi, "get", new=AsyncMock(return_value=[detailed_schedule("nanda_route_2")])
    ) as get:
        messages = await bus.query_stop_bus(
            SimpleNamespace(
                params={
                    "stop_name": "綜二館",
                    "route": "nanda",
                    "day": "weekday",
                    "direction": "down",
                    "time": "08:08",
                    "limit": "2",
                }
            )
        )
    get.assert_awaited_once_with(
        "/buses/schedule",
        params={
            "route": "nanda",
            "day": "weekday",
            "direction": "down",
            "time": "08:08",
            "limit": 2,
            "stop": "綜二館",
            "details": True,
        },
        cache=False,
    )
    card = messages[1].contents.contents[0]
    assert card.body.contents[0].contents[1].text == "08:10"
    assert "8:05" in card.footer.contents[1].text
    assert "路線二" in card.header.contents[1].text


async def test_legacy_postback_filters_are_translated_to_canonical_query_parameters():
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[])) as get:
        await bus.query_stop_bus(
            SimpleNamespace(params={"stop_name": "綜二館", "bus_type": "main", "limits": "99"})
        )
    assert get.await_args.kwargs["params"]["route"] == "main"
    assert get.await_args.kwargs["params"]["limit"] == 12
    assert "bus_type" not in get.await_args.kwargs["params"]
    assert "limits" not in get.await_args.kwargs["params"]


@pytest.mark.parametrize(
    "params",
    [{"route": "red"}, {"day": "monday"}, {"direction": "left"}, {"limit": "invalid"}],
)
async def test_invalid_bus_filters_are_reported_without_calling_api(params):
    with patch.object(bus.nthuapi, "get", new=AsyncMock()) as get:
        messages = await bus.query_stop_bus(
            SimpleNamespace(params={"stop_name": "綜二館", **params})
        )
    get.assert_not_awaited()
    assert messages[0].type == "text"


async def test_invalid_schedule_is_logged_and_reported_by_command_handler():
    from src.app.handlers.command_handler import command_handler
    from src.app.handlers.command_handler import logger as command_logger

    with (
        patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[detailed_schedule()])),
        patch.object(command_logger, "error") as error,
    ):
        result = await command_handler.process_message(
            "@公車/查詢站點與方向 stop_name=台積館", "user"
        )
    error.assert_called_once()
    assert result == "處理訊息時發生錯誤，請稍後再試。"


async def test_all_directions_are_labeled_and_canonical_filters_override_legacy_aliases():
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[detailed_schedule()])) as get:
        messages = await bus.query_stop_bus(
            SimpleNamespace(
                params={
                    "stop_name": "綜二館",
                    "direction": "all",
                    "route": "main",
                    "bus_type": "nanda",
                    "limit": "1",
                    "limits": "10",
                }
            )
        )
    assert "雙向" in messages[0].text
    assert get.await_args.kwargs["params"]["route"] == "main"
    assert get.await_args.kwargs["params"]["limit"] == 1


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
