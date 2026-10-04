"""Generate the offline gallery from production builders, with synthetic API data."""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from linebot.v3.messaging import FlexContainer, FlexMessage  # noqa: E402

from src.app.handlers.command_handler import command_handler  # noqa: E402
from src.modules import dining  # noqa: E402
from src.modules.bus import bus  # noqa: E402
from src.modules.library import library  # noqa: E402
from src.utils import announcecrawler  # noqa: E402
from templates.messages import help_message, open_web_chat, system_message  # noqa: E402
from templates.messages.friend_message import friend_message  # noqa: E402

OUTPUT = ROOT / "frontend" / "flex-preview-data.js"
PREVIEW_LIFF_ID = "0000000000-preview"


async def build_samples() -> list[dict]:
    samples = []

    def add(key, title, description, messages):
        samples.append(
            {
                "id": key,
                "title": title,
                "description": description,
                "messages": [message.to_dict() for message in messages if message.type == "flex"],
            }
        )

    add(
        "ai",
        "AI 網頁對話",
        "保留問題預覽與原本的對話連結；示例 LIFF ID 不會開啟真實對話。",
        [
            open_web_chat(
                PREVIEW_LIFF_ID, "明天早上從校本部到南大，要在哪裡搭校巴？", session_key="preview"
            )
        ],
    )
    add(
        "welcome",
        "加好友歡迎",
        "聊天是主動作，分享是次要動作。",
        [open_web_chat(PREVIEW_LIFF_ID, greeting=True)],
    )
    add(
        "friends",
        "加入與分享",
        "不依賴外部 QR 圖。兩個按鈕分別負責加好友與分享邀請文字。",
        [friend_message()],
    )
    add(
        "bus-directions",
        "公車紅・綠・藍線",
        "紅／綠是校本部，藍線代表南大專車；選方向後沿用既有站點查詢。",
        bus.select_route(SimpleNamespace(params={})),
    )

    with (
        patch.object(
            bus.nthuapi,
            "get",
            new=AsyncMock(
                return_value=[
                    {
                        "bus_type": "large-sized_bus",
                        "line": "red",
                        "arrive_time": "08:10",
                        "dep_stop": "校門口",
                        "dep_time": "08:00",
                        "description": "往南大校區",
                    },
                    {
                        "bus_type": "middle-sized_bus",
                        "line": "green",
                        "arrive_time": "08:25",
                        "dep_stop": "校門口",
                        "dep_time": "08:15",
                        "description": "經綜二館",
                    },
                    {
                        "bus_type": "route_83",
                        "line": "route_1",
                        "arrive_time": "08:40",
                        "dep_stop": "清華大學",
                        "dep_time": "08:30",
                        "description": "示範班次，非即時資訊",
                    },
                ]
            ),
        ),
        patch.object(bus, "datetime") as clock,
    ):
        clock.now.return_value = datetime(2026, 10, 4, 8, 0)
        messages = await bus.query_stop_bus(
            SimpleNamespace(params={"stop_name": "校門口", "direction": "up"})
        )
    add(
        "bus",
        "公車到站",
        "紅／綠／藍標籤搭配路線文字，不只靠顏色辨識。藍線保留南大路線一／二。時間皆為示範資料。",
        messages,
    )
    for key, line, vehicle, title in [
        ("bus-red", "red", "middle-sized_bus", "紅線 · 校本部"),
        ("bus-green", "green", "middle-sized_bus", "綠線 · 校本部"),
        ("bus-blue", "route_2", "large-sized_bus", "藍線 · 南大路線二"),
        ("bus-unknown", "", "large-sized_bus", "路線待確認"),
    ]:
        with (
            patch.object(
                bus.nthuapi,
                "get",
                new=AsyncMock(
                    side_effect=[
                        [
                            {
                                "bus_type": vehicle,
                                "line": line,
                                "arrive_time": "09:10",
                                "dep_stop": "北校門口",
                                "dep_time": "09:00",
                                "description": "示範班次",
                            }
                        ],
                        [],
                    ]
                ),
            ),
            patch.object(bus, "datetime") as clock,
        ):
            clock.now.return_value = datetime(2026, 10, 4, 8, 0)
            messages = await bus.query_stop_bus(
                SimpleNamespace(params={"stop_name": "綜二館", "direction": "up"})
            )
        add(key, title, "依實際路線欄位呈現；無法對上時刻表時不猜測路線。", messages)

    with patch.object(
        library.nthuapi,
        "get",
        new=AsyncMock(
            return_value=[
                {"zonename": "總館 · 個人研究小間", "count": 12},
                {"zonename": "總館 · 團體討論室", "count": 0},
                {"zonename": "人社分館 · 閱讀區", "count": 8},
                {"zonename": "南大分館 · 團體視聽室（長名稱示例）", "count": 3},
            ]
        ),
    ):
        messages = await library.lib_space_flex_message(SimpleNamespace(params={}))
    add(
        "library",
        "圖書館空間",
        "數字右對齊，長名稱可換行；0 仍明確顯示，不誤認為資料缺失。",
        messages,
    )

    info = library.env.get_template("library_info.json.jinja").render(
        data={
            "今日日期": "2026 年 10 月 04 日（示範）",
            "總館開館": "08:00–22:00",
            "人社開館": "09:00–17:00",
            "南大開館": "休館",
        }
    )
    add(
        "hours",
        "圖書館資訊模板",
        "既有開館資訊模板也沿用同一套色票；目前沒有對應的上線指令。",
        [FlexMessage(alt_text="圖書館資訊", contents=FlexContainer.from_json(info))],
    )

    with patch.object(
        dining.nthuapi,
        "get",
        new=AsyncMock(
            return_value=[
                {
                    "building": "水木生活中心",
                    "restaurants": [
                        {
                            "area": "水木生活中心",
                            "name": "示範餐廳",
                            "note": "營業時間以店家公告為準",
                            "phone": "03-571-5131",
                            "image": None,
                            "schedule": {"sunday": "11:00–19:00"},
                        }
                    ],
                }
            ]
        ),
    ):
        messages = await dining.weekend_restaurants_command(
            SimpleNamespace(params={"schedule": "sunday"})
        )
    add(
        "dining",
        "餐廳資訊",
        "舊制輪播轉為 Flex，保留電話、地圖、日期篩選與所有指令。餐廳為示範資料。",
        messages,
    )

    with patch.object(
        announcecrawler.nthuapi,
        "get",
        new=AsyncMock(
            return_value=[
                {
                    "title": "最新公告",
                    "language": "zh-tw",
                    "articles": [
                        {
                            "title": "校園交通調整公告（示範）",
                            "date": "2026-10-04",
                            "link": "https://www.nthu.edu.tw/",
                        }
                    ],
                }
            ]
        ),
    ):
        message = await announcecrawler.get("事務組", "最新公告")
    add(
        "announcements",
        "公告",
        "標題、發布日期、原文入口使用和其他資訊卡相同的層級。內容為示範資料。",
        [message],
    )

    with patch.object(
        library.nthuapi,
        "get",
        new=AsyncMock(
            return_value=[
                {
                    "title": "圖書館最新消息（示範）",
                    "pubDate": "2026-10-04",
                    "link": "https://www.lib.nthu.edu.tw/",
                    "image": None,
                }
            ]
        ),
    ):
        messages = await library.rss(SimpleNamespace(params={"type": "news", "page": "1"}))
    add(
        "news",
        "圖書館消息與分頁",
        "消息和分頁確認不再混用舊式 Template；首頁、下一頁等動作保持不變。",
        messages,
    )

    prefixes = [prefix for prefix in command_handler.prefix_to_module_name if prefix != "說明"]
    add(
        "help",
        "使用說明",
        "分段資訊、AI 入口、加入好友與分享都使用共用元件。",
        [help_message(prefixes, PREVIEW_LIFF_ID)],
    )
    add(
        "system",
        "系統訊息",
        "小型通知保留較緊湊的尺寸，但使用同樣的底色、標題與內文。",
        system_message("目前無法取得資料", "請稍後再試，或回到選單重新查詢。"),
    )
    for module, prefix in command_handler.module_name_to_prefix.items():
        if command_handler.has_menu_info(module):
            messages = await command_handler.auto_generate_default_menu(module)
            add(
                f"menu-{module}",
                f"{prefix}選單",
                "實際註冊的功能與按鈕；預覽只顯示指令，不會送出 LINE 訊息。",
                messages,
            )
    return samples


def write_preview(samples: list[dict]) -> None:
    nodes = []
    indexes = {}

    def encode(value):
        if not isinstance(value, (dict, list)):
            return value
        if isinstance(value, dict):
            node = {key: encode(child) for key, child in value.items()}
        else:
            node = [encode(child) for child in value]
        signature = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if signature not in indexes:
            indexes[signature] = len(nodes)
            nodes.append(node)
        return {"$ref": indexes[signature]}

    root = encode(samples)
    payload = json.dumps(nodes, ensure_ascii=False, separators=(",", ":"))
    OUTPUT.write_text(
        "// Generated by scripts/build_flex_preview.py; do not edit.\n"
        "window.FLEX_PREVIEW = (() => {\n"
        f"  const nodes = {payload};\n"
        "  const expand = (value) => {\n"
        "    if (Array.isArray(value)) return value.map(expand);\n"
        "    if (value && typeof value === 'object') {\n"
        "      if (Object.keys(value).length === 1 && '$ref' in value) return expand(nodes[value.$ref]);\n"
        "      return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, expand(child)]));\n"
        "    }\n"
        "    return value;\n"
        "  };\n"
        f"  return expand({json.dumps(root)});\n"
        "})();\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    write_preview(asyncio.run(build_samples()))
    print(OUTPUT)
