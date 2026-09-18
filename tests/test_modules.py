import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.modules import dining
from src.modules import map as campus_map
from src.modules.bus import bus
from src.modules.library import library
from src.utils import announcecrawler


class BusTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_picker_uses_v2_names_without_mutating_cached_data(self):
        stops = [
            {
                "name": "North Gate",
                "name_en": "North Gate",
                "latitude": "24.79",
                "longitude": "120.99",
            }
        ]
        original = copy.deepcopy(stops)
        with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=stops)) as get:
            messages = await bus.select_stops(
                SimpleNamespace(params={"direction": "down"})
            )

        get.assert_awaited_once_with("/buses/info/stops")
        action = messages[0].quick_reply.items[0].action
        self.assertEqual(action.label, "North Gate")
        self.assertIn("stop_name=North Gate direction=down", action.data)
        self.assertEqual(stops, original)

    async def test_arrivals_use_flat_v2_fields_and_escape_json(self):
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
        with patch.object(
            bus.nthuapi, "get", new=AsyncMock(return_value=arrivals)
        ) as get:
            messages = await bus.query_stop_bus(
                SimpleNamespace(params={"stop_name": "North Gate"})
            )

        get.assert_awaited_once_with(
            "/buses/stops/North%20Gate",
            params={
                "day": "current",
                "limits": 5,
                "bus_type": "all",
                "direction": "up",
            },
            cache=False,
        )
        self.assertEqual(len(messages), 2)
        bubbles = messages[1].contents.contents
        self.assertEqual(len(bubbles), 1)
        self.assertIn('North "Gate"', bubbles[0].footer.contents[0].text)
        self.assertIn("12:00", bubbles[0].footer.contents[1].text)
        self.assertEqual(bubbles[0].footer.contents[2].text, "First line\nSecond line")

    async def test_null_arrivals_return_text_instead_of_empty_carousel(self):
        with patch.object(bus.nthuapi, "get", new=AsyncMock(return_value=[None])):
            messages = await bus.query_stop_bus(
                SimpleNamespace(params={"stop_name": "North Gate"})
            )
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].type, "text")


class LibraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_space_preserves_zero_and_escapes_zone_names(self):
        spaces = [
            {
                "spacetype": 1,
                "spacetypename": "Room",
                "zoneid": "1",
                "zonename": 'Room "A"',
                "count": 0,
            }
        ]
        with patch.object(
            library.nthuapi, "get", new=AsyncMock(return_value=spaces)
        ) as get:
            messages = await library.lib_space_flex_message(SimpleNamespace(params={}))
        get.assert_awaited_once_with("/libraries/space", cache=False)
        row = messages[0].contents.body.contents[1]
        self.assertEqual(row.contents[0].text, 'Room "A"')
        self.assertEqual(row.contents[1].text, "0")

    async def test_rss_accepts_command_event_and_does_not_mutate_cache(self):
        entries = [
            {
                "title": "News" * 20,
                "pubDate": "Tue, 15 Sep 2026 11:20:55 +0800",
                "link": None,
                "image": {"url": "https://www.lib.nthu.edu.tw/image.jpg"},
            }
        ]
        original = copy.deepcopy(entries)
        with patch.object(
            library.nthuapi, "get", new=AsyncMock(return_value=entries)
        ) as get:
            first = await library.rss(
                SimpleNamespace(params={"type": "branches", "page": "1"})
            )
            second = await library.rss(
                SimpleNamespace(params={"type": "branches", "page": "1"})
            )
        self.assertEqual(get.await_count, 2)
        self.assertEqual(entries, original)
        self.assertEqual(first[0].to_dict(), second[0].to_dict())
        self.assertIn("type=branches", first[1].template.actions[0].data)
        self.assertEqual(
            first[0].template.columns[0].actions[0].uri, "https://www.lib.nthu.edu.tw/"
        )

    async def test_empty_rss_page_returns_text(self):
        with patch.object(library.nthuapi, "get", new=AsyncMock(return_value=[])):
            messages = await library.rss(SimpleNamespace(params={}))
        self.assertEqual(messages[0].type, "text")

    async def test_empty_space_returns_text(self):
        with patch.object(library.nthuapi, "get", new=AsyncMock(return_value=[])):
            messages = await library.lib_space_flex_message(SimpleNamespace(params={}))
        self.assertEqual(messages[0].type, "text")


class DiningTests(unittest.IsolatedAsyncioTestCase):
    async def test_weekend_uses_open_endpoint_and_selected_day(self):
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
        with patch.object(
            dining.nthuapi, "get", new=AsyncMock(return_value=restaurants)
        ) as get:
            messages = await dining.weekend_restaurants_command(
                SimpleNamespace(params={"schedule": "saturday"})
            )
        get.assert_awaited_once_with(
            "/dining/open", params={"schedule": "saturday"}, cache=True
        )
        column = messages[0].template.columns[0]
        self.assertIn("10:00-14:00", column.text)
        self.assertNotIn("09:00-17:00", column.text)
        self.assertEqual(len(column.actions), 2)
        self.assertIn("Food%20Court", column.actions[1].uri)

    async def test_random_restaurant_handles_empty_buildings(self):
        with patch.object(
            dining.nthuapi,
            "get",
            new=AsyncMock(return_value=[{"building": "Empty", "restaurants": []}]),
        ):
            messages = await dining.handle_random_restaurant(SimpleNamespace(params={}))
        self.assertEqual(messages[0].type, "text")


class AnnouncementTests(unittest.IsolatedAsyncioTestCase):
    async def test_board_title_matching_ignores_extra_whitespace(self):
        boards = [
            {
                "title": "最新公告  - 清華書院",
                "language": "zh-tw",
                "link": "https://example.test/board",
                "articles": [{"title": "公告一", "date": "2026-09-18", "link": "https://example.test/1"}],
            }
        ]
        with patch.object(announcecrawler.nthuapi, "get", new=AsyncMock(return_value=boards)):
            message = await announcecrawler.get("清華學院住宿書院", "最新公告 - 清華書院")
        self.assertEqual(message.template.columns[0].title, "公告一")

    async def test_repeated_calls_handle_nullable_articles_without_mutation(self):
        boards = [
            {
                "title": "News",
                "language": "zh-tw",
                "link": "https://nthu.edu.tw/",
                "articles": [{"title": None, "link": None, "date": None}],
            }
        ]
        original = copy.deepcopy(boards)
        with patch.object(
            announcecrawler.nthuapi, "get", new=AsyncMock(return_value=boards)
        ) as get:
            first = await announcecrawler.get("Office", "News")
            second = await announcecrawler.get("Office", "News")
        self.assertEqual(get.await_count, 2)
        get.assert_awaited_with(
            "/announcements/",
            params={"department": "Office", "language": "zh-tw"},
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(boards, original)

    async def test_empty_announcements_return_text(self):
        with patch.object(
            announcecrawler.nthuapi, "get", new=AsyncMock(return_value=[])
        ):
            message = await announcecrawler.get("Office", "News")
        self.assertEqual(message.type, "text")

    async def test_board_name_is_filtered_locally_not_as_article_title(self):
        boards = [
            {
                "title": "Other",
                "language": "zh-tw",
                "articles": [
                    {
                        "title": "Unrelated",
                        "link": "https://nthu.edu.tw/other",
                        "date": None,
                    }
                ],
            },
            {
                "title": "News",
                "language": "zh-tw",
                "articles": [
                    {
                        "title": "Bus schedule",
                        "link": "https://nthu.edu.tw/bus",
                        "date": None,
                    }
                ],
            },
        ]
        with patch.object(
            announcecrawler.nthuapi, "get", new=AsyncMock(return_value=boards)
        ) as get:
            message = await announcecrawler.get("Office", "News")
        get.assert_awaited_once_with(
            "/announcements/", params={"department": "Office", "language": "zh-tw"}
        )
        self.assertEqual(len(message.template.columns), 1)
        self.assertEqual(message.template.columns[0].title, "Bus schedule")


class MapTests(unittest.IsolatedAsyncioTestCase):
    async def test_fuzzy_locations_are_returned(self):
        locations = [
            {"name": "Main Library", "latitude": "24.79", "longitude": "120.99"}
        ]
        with patch.object(
            campus_map.nthuapi, "get", new=AsyncMock(return_value=locations)
        ) as get:
            messages = await campus_map.handle_location_command(
                SimpleNamespace(params={"query": "Library"})
            )
        get.assert_awaited_once_with("/locations/search", params={"query": "Library"})
        self.assertEqual(messages[0].title, "Main Library")
        self.assertEqual(messages[0].latitude, 24.79)

    async def test_missing_location_query_does_not_call_api(self):
        with patch.object(campus_map.nthuapi, "get", new=AsyncMock()) as get:
            messages = await campus_map.handle_location_command(
                SimpleNamespace(params={})
            )
        get.assert_not_awaited()
        self.assertEqual(messages[0].type, "text")


class RegistrationTests(unittest.TestCase):
    def test_tzaiwu_announcement_is_registered_once_with_menu(self):
        from src.app.handlers.command_handler import command_handler
        from src.modules import tzaiwu  # noqa: F401 - registers commands

        command = command_handler.modules["tzaiwu"].commands["書院公告"]
        self.assertIsNotNone(command.menu_info)
        self.assertTrue(asyncio.iscoroutinefunction(command.function))

    def test_menu_module_points_at_real_modules(self):
        from src.app.handlers.command_handler import command_handler
        from src.modules import menu  # noqa: F401 - registers commands

        for command in command_handler.modules["menu"].commands.values():
            data = command.menu_info.actions[0].data
            prefix = data[1:].split("/")[0]
            self.assertIn(prefix, command_handler.prefix_to_module_name, data)
