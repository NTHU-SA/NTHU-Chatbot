import asyncio
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
async def test_bus_stop_picker_uses_v2_names_without_mutating_cached_data():
    stops = [
        {"name": "North Gate", "name_en": "North Gate", "latitude": "24.79", "longitude": "120.99"}
    ]
    original = copy.deepcopy(stops)
    with fake_api(bus, stops) as get:
        messages = await bus.select_stops(command(direction="down"))

    get.assert_awaited_once_with("/buses/info/stops")
    action = messages[0].quick_reply.items[0].action
    assert action.label == "North Gate"
    assert "stop_name=North Gate direction=down" in action.data
    assert stops == original


async def test_bus_arrivals_use_flat_v2_fields_and_escape_json():
    arrivals = [
        None,
        {
            "arrive_time": "12:05",
            "dep_time": "12:00",
            "dep_stop": 'North "Gate"',
            "description": "First line\nSecond line",
            "bus_type": "large-sized_bus",
        },
    ]
    with fake_api(bus, arrivals) as get:
        messages = await bus.query_stop_bus(command(stop_name="North Gate"))

    get.assert_awaited_once_with(
        "/buses/stops/North%20Gate",
        params={"day": "current", "limits": 5, "bus_type": "all", "direction": "up"},
        cache=False,
    )
    assert len(messages) == 2
    bubbles = messages[1].contents.contents
    assert len(bubbles) == 1
    assert 'North "Gate"' in bubbles[0].footer.contents[0].text
    assert "12:00" in bubbles[0].footer.contents[1].text
    assert bubbles[0].footer.contents[2].text == "First line\nSecond line"


async def test_bus_null_arrivals_return_text_instead_of_empty_carousel():
    with fake_api(bus, [None]):
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
    get.assert_awaited_once_with("/libraries/space", cache=False)
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
    assert entries == original
    assert first[0].to_dict() == second[0].to_dict()
    assert "type=branches" in first[1].template.actions[0].data
    assert first[0].template.columns[0].actions[0].uri == "https://www.lib.nthu.edu.tw/"


async def test_library_empty_rss_page_returns_text():
    with fake_api(library, []):
        messages = await library.rss(command())
    assert messages[0].type == "text"


async def test_library_empty_space_returns_text():
    with fake_api(library, []):
        messages = await library.lib_space_flex_message(command())
    assert messages[0].type == "text"


# -- dining --
async def test_dining_weekend_uses_open_endpoint_and_selected_day():
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
    with fake_api(dining, restaurants) as get:
        messages = await dining.weekend_restaurants_command(command(schedule="saturday"))
    get.assert_awaited_once_with("/dining/open", params={"schedule": "saturday"}, cache=True)
    column = messages[0].template.columns[0]
    assert "10:00-14:00" in column.text
    assert "09:00-17:00" not in column.text
    assert len(column.actions) == 2
    assert "Food%20Court" in column.actions[1].uri


async def test_dining_random_restaurant_handles_empty_buildings():
    with fake_api(dining, [{"building": "Empty", "restaurants": []}]):
        messages = await dining.handle_random_restaurant(command())
    assert messages[0].type == "text"


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
    assert message.template.columns[0].title == "公告一"


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
    get.assert_awaited_with("/announcements/", params=ANNOUNCEMENT_PARAMS)
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
            "articles": [{"title": "Bus schedule", "link": "https://nthu.edu.tw/bus", "date": None}],
        },
    ]
    with fake_api(announcecrawler, boards) as get:
        message = await announcecrawler.get("Office", "News")
    get.assert_awaited_once_with("/announcements/", params=ANNOUNCEMENT_PARAMS)
    assert len(message.template.columns) == 1
    assert message.template.columns[0].title == "Bus schedule"


# -- map --
async def test_map_fuzzy_locations_are_returned():
    locations = [{"name": "Main Library", "latitude": "24.79", "longitude": "120.99"}]
    with fake_api(campus_map, locations) as get:
        messages = await campus_map.handle_location_command(command(query="Library"))
    get.assert_awaited_once_with("/locations/search", params={"query": "Library"})
    assert messages[0].title == "Main Library"
    assert messages[0].latitude == 24.79


async def test_map_missing_location_query_does_not_call_api():
    with fake_api(campus_map) as get:
        messages = await campus_map.handle_location_command(command())
    get.assert_not_awaited()
    assert messages[0].type == "text"


# -- registration --
def test_tzaiwu_announcement_is_registered_once_with_menu():
    from src.app.handlers.command_handler import command_handler
    from src.modules import tzaiwu  # noqa: F401 - registers commands

    registered = command_handler.modules["tzaiwu"].commands["書院公告"]
    assert registered.menu_info is not None
    assert asyncio.iscoroutinefunction(registered.function)


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
