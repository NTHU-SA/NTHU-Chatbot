import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from linebot.v3.messaging import FlexContainer

from src.modules.bus import bus
from templates.messages.bus_message import (
    BUS_TYPE_NAMES,
    CARD_ROUTE_LABEL,
    FARE_INK,
    FARE_TINT,
    UNKNOWN_LABEL,
    bus_arrival_carousel,
    bus_menu_bubble,
    bus_reply,
    direction_prompt,
    no_bus_bubble,
    runs_today,
    service_state,
    split_note,
)
from templates.messages.flex_theme import FLEX_THEME

NOON = datetime(2026, 10, 23, 12, 0)  # 星期五
BOUNDS = {"first_time": "07:31", "last_time": "23:29", "tomorrow_first": "08:49"}


def arrival(**changes):
    return {
        "arrive_time": "12:10",
        "dep_time": "12:05",
        "dep_stop": "校門",
        "description": "",
        "bus_type": "middle-sized_bus",
        "line": "main_red",
        "stops": ["北校門口", "綜二館", "台積館"],
        **changes,
    }


def cards(arrivals, stop="綜二館", direction="上山", now=NOON):
    return bus_arrival_carousel(arrivals, stop, direction, now)["contents"]


def body_text(card):
    return [item.get("text", "") for item in card["body"]["contents"] if item["type"] == "text"]


# ─────────────────────── 一條線一張卡 ───────────────────────


def test_each_route_becomes_one_card_in_fixed_colour_order():
    result = cards(
        [
            arrival(line="nanda_route_1", arrive_time="12:30"),
            arrival(line="main_green", arrive_time="12:20"),
            arrival(line="main_red", arrive_time="12:10"),
        ]
    )
    assert [card["header"]["contents"][0]["text"] for card in result] == [
        CARD_ROUTE_LABEL["red"],
        CARD_ROUTE_LABEL["green"],
        CARD_ROUTE_LABEL["blue"],
    ]
    for card, family in zip(result, ("red", "green", "blue"), strict=True):
        assert card["header"]["backgroundColor"] == FLEX_THEME["routes"][family]["tint"]
        assert card["header"]["contents"][0]["color"] == FLEX_THEME["routes"][family]["ink"]
        assert card["body"]["contents"][0]["color"] == FLEX_THEME["routes"][family]["ink"]


def test_card_count_follows_the_lines_that_actually_serve_the_stop():
    """下山紅線不經過人社院，所以張數必須能是 1~3，不能寫死成 3。"""
    assert len(cards([arrival(line="main_green")])) == 1
    assert len(cards([arrival(line="main_green"), arrival(line="nanda_route_2")])) == 2
    assert (
        len(
            cards(
                [
                    arrival(line="main_red"),
                    arrival(line="main_green"),
                    arrival(line="nanda_route_2"),
                ]
            )
        )
        == 3
    )


def test_same_line_collapses_into_one_card_with_later_arrivals_listed():
    result = cards(
        [
            arrival(line="main_red", arrive_time="12:40"),
            arrival(line="main_red", arrive_time="12:10"),
            arrival(line="main_red", arrive_time="12:25"),
        ]
    )
    assert len(result) == 1
    rows = body_text(result[0])
    assert rows[0] == "12:10 到站"
    assert "後續到站 12:25 · 12:40" in rows


def test_unknown_route_keeps_the_departure_on_a_neutral_card():
    """沒有 line 時不依車種猜測，但也不能讓班次憑空消失。"""
    result = cards([arrival(line="")])
    assert result[0]["header"]["contents"][0]["text"] == UNKNOWN_LABEL
    assert result[0]["header"]["contents"][0]["color"] == FLEX_THEME["colors"]["muted"]


def test_unknown_route_card_sorts_after_the_known_lines():
    result = cards([arrival(line=""), arrival(line="main_red")])
    assert [card["header"]["contents"][0]["text"] for card in result] == [
        CARD_ROUTE_LABEL["red"],
        UNKNOWN_LABEL,
    ]


# ─────────────────────── 卡片內容 ───────────────────────


@pytest.mark.parametrize(
    "arrive_time, expected",
    [("12:10", "還有 10 分鐘"), ("12:00", "即將進站"), ("11:59", "即將進站"), (None, "時間未知")],
)
def test_countdown_reads_as_waiting_time_not_a_raw_difference(arrive_time, expected):
    rows = body_text(cards([arrival(arrive_time=arrive_time)])[0])
    assert any(row.startswith(expected) for row in rows)


def test_vehicle_names_are_short_enough_for_a_narrow_card():
    assert BUS_TYPE_NAMES["large-sized_bus"] == "大型車"
    assert BUS_TYPE_NAMES["middle-sized_bus"] == "中型車"
    rows = body_text(cards([arrival(bus_type="large-sized_bus")])[0])
    assert "大型車" in rows[1]


def test_stops_line_marks_the_user_stop_and_keeps_the_prefix():
    card = cards([arrival(stops=["北校門口", "綜二館", "台積館"])], stop="綜二館")[0]
    line = card["body"]["contents"][-1]
    assert line["text"].startswith("經過 ")
    here = [span for span in line["contents"] if span.get("weight") == "bold"]
    assert [span["text"] for span in here] == ["綜二館"]
    assert "台積" in line["text"] and "台積館" not in line["text"]


def test_departure_stop_and_time_survive_the_redesign():
    """發車站點與時間是部長指名要留的欄位。"""
    rows = body_text(cards([arrival(dep_stop="台積館", dep_time="12:05")])[0])
    assert "台積 12:05 發車" in rows


# ─────────────────────── 備註分流 ───────────────────────


@pytest.mark.parametrize(
    "description, fare, other",
    [
        ("大型巴士", None, None),
        ("大型巴士,週六有行駛", None, "週六有行駛"),
        ("此為付費市區公車(83號公車直達兩校區)", "此為付費市區公車(83號公車直達兩校區)", None),
        ("週六有行駛", None, "週六有行駛"),
        ("   ", None, None),
        (None, None, None),
    ],
)
def test_vehicle_wording_is_dropped_and_fares_are_separated(description, fare, other):
    assert split_note(description) == (fare, other)


def test_duplicate_vehicle_note_does_not_appear_next_to_the_vehicle_name():
    rows = body_text(cards([arrival(bus_type="large-sized_bus", description="大型巴士")])[0])
    assert "大型車" in rows[1]
    assert "大型巴士" not in "".join(rows)


def test_paid_routes_get_their_own_warning_badge_not_grey_small_print():
    card = cards([arrival(description="此為付費市區公車(83號公車直達兩校區)")])[0]
    badge = next(item for item in card["body"]["contents"] if item["type"] == "box")
    assert badge["backgroundColor"] == FARE_TINT
    assert badge["contents"][0]["color"] == FARE_INK
    assert badge["contents"][0]["weight"] == "bold"
    assert badge["contents"][0]["text"].startswith("💰")
    # 標籤緊跟在倒數後面，不是滑到最底才看到
    assert card["body"]["contents"].index(badge) == 2


@pytest.mark.parametrize(
    "description, weekday, runs",
    [
        ("週五停駛", 4, False),  # 星期五
        ("週五停駛", 1, True),  # 星期二照常
        ("停駛", 4, False),  # 沒指定星期就保守當作不開
        ("週六有行駛", 5, True),
        ("大型巴士,週六有行駛", 4, True),
        ("此為付費市區公車", 4, True),
        ("", 4, True),
        (None, 4, True),
    ],
)
def test_suspension_is_matched_against_today_not_any_weekday(description, weekday, runs):
    assert runs_today(description, weekday) is runs


def test_suspended_departures_never_reach_a_card():
    """API 在星期五照樣回傳標註週五停駛的班次，過濾是我們的責任。"""
    container = bus_reply(
        [arrival(arrive_time="12:10"), arrival(arrive_time="12:40", description="週五停駛")],
        "綜二館",
        "上山",
        now=NOON,
        **BOUNDS,
    )
    rows = body_text(container["contents"][0])
    assert rows[0] == "12:10 到站"
    assert not any("12:40" in row for row in rows)


# ─────────────────────── 沒有車的三種情況 ───────────────────────


@pytest.mark.parametrize(
    "hour, minute, state",
    [
        (0, 30, "before_first"),
        (5, 30, "before_first"),
        (7, 45, "after_last"),
        (23, 50, "after_last"),
    ],
)
def test_service_state_separates_not_yet_running_from_finished(hour, minute, state):
    moment = datetime(2026, 10, 23, hour, minute)
    assert service_state("07:31", "23:29", moment) == state


def test_missing_timetable_is_reported_as_no_service_not_as_a_finished_day():
    assert service_state(None, None, NOON) == "no_service"


@pytest.mark.parametrize(
    "state, headline, extra",
    [
        ("after_last", "末班車已過", {"next_time": "08:49"}),
        ("before_first", "尚未發車", {"today_time": "07:31"}),
        ("no_service", "今日無班次", {}),
    ],
)
def test_no_bus_bubble_says_which_kind_of_nothing_it_is(state, headline, extra):
    bubble = no_bus_bubble("綜二館", "下山", state=state, **extra)
    rows = [item["text"] for item in bubble["body"]["contents"] if item["type"] == "text"]
    assert rows[0] == headline
    assert bubble["header"]["contents"][0]["text"] == "綜二館 · 下山"


def test_early_morning_shows_the_first_departure_instead_of_a_huge_countdown():
    """決策 B：清晨不是「還有 421 分鐘」，而是「尚未發車 · 今日首班」。"""
    container = bus_reply(
        [arrival(arrive_time="07:31")],
        "綜二館",
        "下山",
        now=datetime(2026, 10, 23, 0, 30),
        **BOUNDS,
    )
    assert container["type"] == "bubble"
    rows = [item["text"] for item in container["body"]["contents"] if item["type"] == "text"]
    assert rows[0] == "尚未發車"
    assert "07:31" in rows[1]


def test_finished_notice_never_prints_a_last_departure_that_has_not_happened():
    """剩餘班次全停駛時也走 after_last，這時末班還沒到，印出來會自相矛盾。"""
    bubble = bus_reply(
        [arrival(arrive_time="23:29", description="週五停駛")],
        "綜二館",
        "下山",
        now=datetime(2026, 10, 23, 23, 3),
        **BOUNDS,
    )
    rows = [item["text"] for item in bubble["body"]["contents"] if item["type"] == "text"]
    assert rows[0] == "末班車已過"
    assert not any("23:29" in row for row in rows)
    assert rows[-1] == "明日首班 08:49"


def test_every_departure_suspended_falls_back_to_the_notice_bubble():
    container = bus_reply([arrival(description="週五停駛")], "綜二館", "下山", now=NOON, **BOUNDS)
    assert container["type"] == "bubble"
    rows = [item["text"] for item in container["body"]["contents"] if item["type"] == "text"]
    assert rows[0] == "末班車已過"
    assert "08:49" in rows[-1]


def test_real_departures_win_over_the_inferred_state():
    container = bus_reply([arrival()], "綜二館", "上山", now=NOON, **BOUNDS)
    assert container["type"] == "carousel"


# ─────────────────────── 選單與選方向 ───────────────────────


def test_menu_fits_five_options_on_one_card_without_scrolling():
    bubble = bus_menu_bubble()
    buttons = [item for item in bubble["body"]["contents"] if item["type"] == "button"]
    assert [button["action"]["label"] for button in buttons] == [
        "動態查詢",
        "最新資訊",
        "校本部路線圖",
        "南大專車",
        "NTHUMods 公車查詢 ↗",
    ]
    assert buttons[0]["style"] == "primary"
    assert buttons[-1]["action"]["type"] == "uri"


def test_direction_prompt_names_all_three_lines_in_plain_text():
    prompt = direction_prompt()
    for key in ("red", "green", "blue"):
        assert FLEX_THEME["routes"][key]["label"] in prompt


def test_direction_step_is_a_bubble_with_quick_replies_not_a_flex_card():
    """二選一的問題用一整張卡太重。"""
    message = bus.select_route(SimpleNamespace(params={}))[0]
    assert message.type == "text"
    assert [item.action.data for item in message.quick_reply.items] == [
        "@公車/選擇站點 direction=up",
        "@公車/選擇站點 direction=down",
    ]


def test_module_menu_replaces_the_generated_carousel_with_one_card():
    message = bus.bus_menu(SimpleNamespace(params={}))[0]
    assert message.contents.type == "bubble"


# ─────────────────────── view model ───────────────────────


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


def test_full_stop_sequence_is_carried_for_the_route_preview():
    result = bus._stop_arrivals([detailed_schedule()], "綜二館")
    assert result[0]["stops"] == ["北校門口", "綜二館"]


def test_missing_route_is_not_inferred_from_vehicle_type():
    schedule = detailed_schedule()
    del schedule["dep_info"]["line"]
    result = bus._stop_arrivals([schedule], "綜二館")
    assert result[0]["line"] == ""


def test_missing_selected_stop_is_an_explicit_error():
    with pytest.raises(ValueError, match="omitted the requested stop"):
        bus._stop_arrivals([detailed_schedule()], "不存在的站")


@pytest.mark.parametrize("response", [None, {}, {"schedules": []}])
def test_invalid_schedule_response_is_an_explicit_error(response):
    with pytest.raises(ValueError, match="Invalid bus schedule response"):
        bus._stop_arrivals(response, "綜二館")


def test_dynamic_text_with_quotes_and_newlines_stays_a_valid_flex_container():
    """Jinja 時代靠跳脫，改成組 dict 後由 SDK 保證；這條守住同一個性質。"""
    container = bus_arrival_carousel(
        [arrival(dep_stop='校門 "A"', description="第一行\n第二行")],
        '校門 "A"',
        "上山",
        NOON,
    )
    assert FlexContainer.from_dict(container).to_dict() == container
    assert len(json.dumps(container, ensure_ascii=False).encode()) <= 30_000


# ─────────────────────── 指令整合 ───────────────────────


async def test_canonical_filters_reach_the_api_without_extra_schedule_requests():
    with (
        patch.object(
            bus.nthuapi, "get", new=AsyncMock(return_value=[detailed_schedule("nanda_route_2")])
        ) as get,
        patch.object(bus, "_day_bounds", new=AsyncMock(return_value=("07:00", "22:00"))),
        patch.object(bus, "datetime") as clock,
    ):
        clock.now.return_value = datetime(2026, 10, 23, 8, 0)
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
    assert card.header.contents[0].text == CARD_ROUTE_LABEL["blue"]
    assert "綜二館 · 下山" in card.header.contents[1].text


async def test_day_bounds_reuse_the_shared_cache_and_read_the_whole_day():
    """首末班是靜態班表，不該每次查詢都重打一次。"""
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[detailed_schedule()])) as get:
        bounds = await bus._day_bounds("綜二館", "up", "weekday")
    assert bounds == ("08:10", "08:10")
    assert "cache" not in get.await_args.kwargs
    assert get.await_args.kwargs["params"]["day"] == "weekday"
    assert get.await_args.kwargs["params"]["limit"] == 200


async def test_legacy_postback_filters_are_translated_to_canonical_query_parameters():
    with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[])) as get:
        await bus.query_stop_bus(
            SimpleNamespace(params={"stop_name": "綜二館", "bus_type": "main", "limits": "99"})
        )
    params = get.await_args_list[0].kwargs["params"]
    assert params["route"] == "main"
    assert params["limit"] == 12
    assert "bus_type" not in params
    assert "limits" not in params


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


async def test_missing_stop_name_is_reported_without_calling_api():
    with patch.object(bus.nthuapi, "get", new=AsyncMock()) as get:
        messages = await bus.query_stop_bus(SimpleNamespace(params={}))
    get.assert_not_awaited()
    assert messages[0].type == "text"


async def test_day_bounds_survive_an_api_failure_instead_of_breaking_the_reply():
    with patch.object(bus.nthuapi, "get", new=AsyncMock(side_effect=ValueError("down"))):
        assert await bus._day_bounds("綜二館", "up", "weekday") == (None, None)


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
    with (
        patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[detailed_schedule()])) as get,
        patch.object(bus, "datetime") as clock,
    ):
        clock.now.return_value = datetime(2026, 10, 23, 8, 0)
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
    params = get.await_args_list[0].kwargs["params"]
    assert params["route"] == "main"
    assert params["limit"] == 1


async def test_announcements_come_from_the_bus_board_not_the_catch_all_one():
    """事務組「最新公告」混著垃圾清運與環境消毒，公車有自己的看板。"""
    with patch.object(bus.announcecrawler, "get", new=AsyncMock()) as get:
        await bus.bus_announce(SimpleNamespace(params={}))
    get.assert_awaited_once_with("事務組", "校園公車暨巡迴公車公告")
