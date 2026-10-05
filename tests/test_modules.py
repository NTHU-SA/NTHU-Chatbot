import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.modules import dining
from src.modules import map as campus_map
from src.modules.bus import bus
from src.modules.library import library
from src.utils import announcecrawler


def command(**params):
    return SimpleNamespace(params=params)


def fake_api(module, return_value=None):
    return patch.object(module.nthuapi, "get", new=AsyncMock(return_value=return_value))


# -- bus --
async def test_bus_stop_picker_uses_canonical_endpoint_without_mutating_cached_data():
    stops = [
        {"name": "North Gate", "name_en": "North Gate", "latitude": "24.79", "longitude": "120.99"}
    ]
    original = copy.deepcopy(stops)
    with fake_api(bus, stops) as get:
        messages = await bus.select_stops(command(direction="down"))

    get.assert_awaited_once_with("/buses/stops")
    action = messages[0].quick_reply.items[0].action
    assert action.label == "North Gate"
    assert "stop_name=North Gate direction=down" in action.data
    assert stops == original


async def test_bus_arrivals_use_detailed_schedules_and_escape_json():
    schedules = [
        {
            "dep_info": {
                "time": "12:00",
                "dep_stop": 'North "Gate"',
                "description": "First line\nSecond line",
                "bus_type": "large-sized_bus",
                "line": "main_red",
            },
            "stops_time": [
                {"stop": "Other Stop", "arrive_time": "12:01"},
                {"stop": "North Gate", "arrive_time": "12:05"},
            ],
        },
    ]
    original = copy.deepcopy(schedules)
    with fake_api(bus, schedules) as get:
        messages = await bus.query_stop_bus(command(stop_name="North Gate"))

    get.assert_awaited_once_with(
        "/buses/schedule",
        params={
            "day": "current",
            "limit": 5,
            "route": "all",
            "direction": "up",
            "stop": "North Gate",
            "details": True,
        },
        cache=False,
    )
    assert schedules == original
    assert len(messages) == 2
    bubbles = messages[1].contents.contents
    assert len(bubbles) == 1
    assert bubbles[0].body.contents[0].contents[1].text == "12:05"
    assert 'North "Gate"' in bubbles[0].footer.contents[0].text
    assert "12:00" in bubbles[0].footer.contents[1].text
    assert bubbles[0].footer.contents[2].text == "First line\nSecond line"


async def test_bus_empty_schedules_return_text_instead_of_empty_carousel():
    with fake_api(bus, []):
        messages = await bus.query_stop_bus(command(stop_name="North Gate"))
    assert len(messages) == 1
    assert messages[0].type == "text"


# -- library --
async def test_library_space_preserves_zero_and_escapes_zone_names():
    spaces = [
        {"spacetype": 1, "spacetypename": "Room", "zoneid": "1", "zonename": 'Room "A"', "count": 0}
    ]
    with fake_api(library, spaces) as get:
        messages = await library.lib_space_flex_message(command())
    get.assert_awaited_once_with("/libraries/spaces", cache=False)
    row = messages[0].contents.body.contents[1]
    assert row.contents[0].text == 'Room "A"'
    assert row.contents[1].text == "0"


async def test_library_rss_accepts_command_event_and_does_not_mutate_cache():
    entries = [
        {
            "title": "News" * 20,
            "pubDate": "Tue, 15 Sep 2026 11:20:55 +0800",
            "link": None,
            "image": {"url": "https://www.lib.nthu.edu.tw/image.jpg"},
        }
    ]
    original = copy.deepcopy(entries)
    with fake_api(library, entries) as get:
        first = await library.rss(command(type="branches", page="1"))
        second = await library.rss(command(type="branches", page="1"))
    assert get.await_count == 2
    get.assert_awaited_with("/libraries/rss/branches")
    assert entries == original
    assert first[0].to_dict() == second[0].to_dict()
    assert "type=branches" in first[1].contents.footer.contents[0].action.data
    assert (
        first[0].contents.contents[0].footer.contents[0].action.uri
        == "https://www.lib.nthu.edu.tw/"
    )


async def test_library_empty_rss_page_returns_text():
    with fake_api(library, []):
        messages = await library.rss(command())
    assert messages[0].type == "text"


async def test_library_empty_space_returns_text():
    with fake_api(library, []):
        messages = await library.lib_space_flex_message(command())
    assert messages[0].type == "text"


# -- dining --
async def test_dining_weekend_filters_by_schedule_and_flattens_buildings():
    restaurants = [
        {
            "area": "Food Court",
            "name": "Cafe",
            "phone": "",
            "note": "",
            "image": None,
            "schedule": {"weekday": "09:00-17:00", "saturday": "10:00-14:00"},
        }
    ]
    buildings = [{"building": "Food Court", "restaurants": restaurants}, {"building": "Empty"}]
    with fake_api(dining, buildings) as get:
        messages = await dining.weekend_restaurants_command(command(schedule="saturday"))
    get.assert_awaited_once_with("/dining", params={"schedule": "saturday"}, cache=True)
    card = messages[0].contents.contents[0]
    assert "10:00-14:00" in card.body.contents[0].text
    assert "09:00-17:00" not in card.body.contents[0].text
    assert len(card.footer.contents) == 2
    assert "Food%20Court" in card.footer.contents[1].action.uri


async def test_dining_random_restaurant_handles_empty_buildings():
    with fake_api(dining, [{"building": "Empty", "restaurants": []}]) as get:
        messages = await dining.handle_random_restaurant(command())
    get.assert_awaited_once_with("/dining")
    assert messages[0].type == "text"


async def test_dining_directory_and_building_lookup_use_canonical_endpoint():
    buildings = [
        {
            "building": "Food Court",
            "restaurants": [
                {
                    "area": "Food Court",
                    "name": "Cafe",
                    "phone": "",
                    "note": "",
                    "image": None,
                    "schedule": {"weekday": "09:00-17:00"},
                }
            ],
        }
    ]
    original = copy.deepcopy(buildings)
    with fake_api(dining, buildings) as get:
        directory = await dining.handle_building_directory(command())
        restaurants = await dining.building_restaurant_command(command(building_name="Food Court"))
    assert get.await_args_list[0].args == ("/dining",)
    get.assert_awaited_with("/dining", params={"building_name": "Food Court"})
    assert "building_name=Food Court" in directory[0].quick_reply.items[0].action.data
    assert restaurants[0].type == "flex"
    assert buildings == original


async def test_dining_today_is_not_cached():
    with fake_api(dining, []) as get:
        await dining.weekend_restaurants_command(command(schedule="today"))
    get.assert_awaited_once_with("/dining", params={"schedule": "today"}, cache=False)


# -- announcements --
ANNOUNCEMENT_PARAMS = {"department": "Office", "language": "zh-tw"}


async def test_announcement_board_title_matching_ignores_extra_whitespace():
    boards = [
        {
            "title": "最新公告  - 清華書院",
            "language": "zh-tw",
            "link": "https://example.test/board",
            "articles": [
                {"title": "公告一", "date": "2026-09-18", "link": "https://example.test/1"}
            ],
        }
    ]
    with fake_api(announcecrawler, boards):
        message = await announcecrawler.get("清華學院住宿書院", "最新公告 - 清華書院")
    assert message.contents.contents[0].header.contents[0].text == "公告一"


async def test_announcement_repeated_calls_handle_nullable_articles_without_mutation():
    boards = [
        {
            "title": "News",
            "language": "zh-tw",
            "link": "https://nthu.edu.tw/",
            "articles": [{"title": None, "link": None, "date": None}],
        }
    ]
    original = copy.deepcopy(boards)
    with fake_api(announcecrawler, boards) as get:
        first = await announcecrawler.get("Office", "News")
        second = await announcecrawler.get("Office", "News")
    assert get.await_count == 2
    get.assert_awaited_with("/announcements", params=ANNOUNCEMENT_PARAMS)
    assert first.to_dict() == second.to_dict()
    assert boards == original


async def test_announcement_empty_returns_text():
    with fake_api(announcecrawler, []):
        message = await announcecrawler.get("Office", "News")
    assert message.type == "text"


async def test_announcement_board_name_is_filtered_locally_not_as_article_title():
    boards = [
        {
            "title": "Other",
            "language": "zh-tw",
            "articles": [{"title": "Unrelated", "link": "https://nthu.edu.tw/other", "date": None}],
        },
        {
            "title": "News",
            "language": "zh-tw",
            "articles": [
                {"title": "Bus schedule", "link": "https://nthu.edu.tw/bus", "date": None}
            ],
        },
    ]
    with fake_api(announcecrawler, boards) as get:
        message = await announcecrawler.get("Office", "News")
    get.assert_awaited_once_with("/announcements", params=ANNOUNCEMENT_PARAMS)
    assert len(message.contents.contents) == 1
    assert message.contents.contents[0].header.contents[0].text == "Bus schedule"


# -- map --
async def test_map_fuzzy_locations_are_returned():
    locations = [{"name": "Main Library", "latitude": "24.79", "longitude": "120.99"}]
    with fake_api(campus_map, locations) as get:
        messages = await campus_map.handle_location_command(command(query="Library"))
    get.assert_awaited_once_with("/locations", params={"name": "Library", "fuzzy": True})
    assert messages[0].title == "Main Library"
    assert messages[0].latitude == 24.79


async def test_map_missing_location_query_does_not_call_api():
    with fake_api(campus_map) as get:
        messages = await campus_map.handle_location_command(command())
    get.assert_not_awaited()
    assert messages[0].type == "text"


async def test_map_picker_uses_canonical_locations_endpoint():
    locations = [{"name": "Main Library", "latitude": "24.79", "longitude": "120.99"}]
    with fake_api(campus_map, locations) as get:
        messages = await campus_map.list_quick_reply(command())
    get.assert_awaited_once_with("/locations")
    assert messages[0].quick_reply.items[0].action.label == "Main Library"


async def test_map_exact_matches_still_take_priority_over_fuzzy_results():
    locations = [
        {"name": "Main Library Annex", "latitude": "24.80", "longitude": "121.00"},
        {"name": "Main Library", "latitude": "24.79", "longitude": "120.99"},
    ]
    with fake_api(campus_map, locations):
        messages = await campus_map.handle_location_command(command(query="Main_space_Library"))
    assert len(messages) == 1
    assert messages[0].title == "Main Library"


async def test_department_directory_fetches_canonical_directory():
    from src.app import _fetch_departments
    from src.utils import nthuapi

    units = [{"name": "資訊工程學系", "index": "CS"}]
    with patch.object(nthuapi, "get", new=AsyncMock(return_value=units)) as get:
        assert await _fetch_departments() == units
    get.assert_awaited_once_with("/directory")


# -- registration --
async def test_legacy_magic_share_command_uses_new_branding_without_duplicate_menu_entry():
    from src.app.handlers.command_handler import command_handler

    old = await command_handler.process_message("@神奇海螺/分享狗狗情報員", "user")
    new = await command_handler.process_message("@神奇海螺/分享清華校園情報員", "user")

    assert [message.to_dict() for message in old] == [message.to_dict() for message in new]
    assert old[0].text == "汪！歡迎分享給更多朋友認識我！"

    menu = await command_handler.process_message("@神奇海螺", "user")
    titles = [
        card.header.contents[0].text for message in menu for card in message.contents.contents
    ]
    assert titles.count("分享清華校園情報員") == 1
    assert "分享狗狗情報員" not in titles


def test_tzaiwu_module_is_removed():
    """載物書院功能已停用：指令前綴與模組都不能再被註冊。"""
    from src.app.handlers.command_handler import command_handler

    assert "tzaiwu" not in command_handler.modules
    assert "載物書院" not in command_handler.prefix_to_module_name


def test_menu_module_points_at_real_modules():
    from src.app.handlers.command_handler import command_handler
    from src.modules import menu  # noqa: F401 - registers commands

    for registered in command_handler.modules["menu"].commands.values():
        data = registered.menu_info.actions[0].data
        prefix = data[1:].split("/")[0]
        assert prefix in command_handler.prefix_to_module_name, data


def test_developer_test_module_is_not_exposed():
    """開發測試用的指令（會 sleep 佔住 worker）不能在正式環境被任何人觸發。"""
    from src.app.handlers.command_handler import command_handler

    assert "dev" not in command_handler.modules
    assert "開發者" not in command_handler.prefix_to_module_name


async def test_commands_are_logged_without_user_text():
    """隱私權政策承諾 @ 指令只記錄使用的功能：指令文字、參數與錯誤細節都不能進 log。"""
    from loguru import logger

    from src.app.handlers.command_handler import command_handler

    lines: list[str] = []
    sink = logger.add(lines.append, level="DEBUG", format="{message}")
    try:
        await command_handler.process_message("@公車 我的秘密行程 地點=宿舍 孤兒參數", "usr_x")
        await command_handler.process_message("@不存在的秘密模組 查詢", "usr_x")
    finally:
        logger.remove(sink)
    logged = "".join(lines)
    for secret in ("秘密", "宿舍", "孤兒參數"):
        assert secret not in logged
