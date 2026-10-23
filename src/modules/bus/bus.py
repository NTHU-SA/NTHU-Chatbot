from datetime import datetime, timedelta, timezone

from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
    ImageMessage,
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    TextMessage,
)

from src.app.handlers.command_handler import command_handler
from src.utils import announcecrawler, nthuapi
from templates.messages.bus_message import (
    bus_menu_bubble,
    bus_reply,
    direction_prompt,
)

STATIC_URL = "https://data.nthusa.tw"
TZ = timezone(timedelta(hours=8))
DIRECTION_NAMES = {"up": "上山", "down": "下山", "all": "雙向"}
DAY_NAMES = {"current": "即時", "weekday": "平日", "weekend": "假日"}


def _stop_arrivals(schedules: list[dict], stop_name: str) -> list[dict]:
    """Convert canonical detailed schedules to the arrival-card view model."""
    if not isinstance(schedules, list):
        raise ValueError("Invalid bus schedule response")
    arrivals = []
    for schedule in schedules:
        departure = schedule["dep_info"]
        stops = [item["stop"] for item in schedule["stops_time"]]
        stop = next((item for item in schedule["stops_time"] if item["stop"] == stop_name), None)
        if stop is None:
            raise ValueError("Bus schedule omitted the requested stop")
        arrivals.append(
            {
                "arrive_time": stop["arrive_time"],
                "dep_time": departure["time"],
                "dep_stop": departure["dep_stop"],
                "description": departure["description"],
                "bus_type": departure["bus_type"],
                "line": departure.get("line", ""),
                "stops": stops,
            }
        )
    return arrivals


def _day_kind(moment: datetime) -> str:
    return "weekend" if moment.isoweekday() >= 6 else "weekday"


async def _day_bounds(stop_name: str, direction: str, day: str) -> tuple[str | None, str | None]:
    """今日首末班到站時間。

    `day=current` 只回「從現在起還剩的車」，所以判斷「尚未發車」或「末班車已過」
    必須另外問整天的班表。這份班表是靜態的，沿用預設快取。
    """
    try:
        schedules = await nthuapi.get(
            "/buses/schedule",
            params={
                "day": day,
                "limit": 200,
                "route": "all",
                "direction": direction,
                "stop": stop_name,
                "details": True,
            },
        )
    except ValueError:
        return None, None
    if not isinstance(schedules, list):
        return None, None
    times = sorted(
        item["arrive_time"]
        for schedule in schedules
        for item in schedule["stops_time"]
        if item["stop"] == stop_name
    )
    return (times[0], times[-1]) if times else (None, None)


@command_handler.add_default_menu()
def bus_menu(event):
    """五個選項收在一張卡裡，不必左右滑。"""
    return [
        FlexMessage(
            alt_text="公車：動態查詢、最新資訊、路線圖",
            contents=FlexContainer.from_dict(bus_menu_bubble()),
        )
    ]


@command_handler.add_command_with_menu(
    name="動態公車查詢",
    title="🚌 動態公車查詢",
    description="點擊這裡查詢各站點公車到站時間",
    actions=[
        PostbackAction(
            label="動態公車查詢",
            data="@公車/動態公車查詢",
            displayText="動態公車查詢",
        )
    ],
)
def select_route(params):
    """方向只是二選一，用泡泡加 Quick Reply 就夠，不開 Flex 卡。"""
    quick_reply = QuickReply(
        items=[
            QuickReplyItem(
                action=PostbackAction(
                    label="🚍 我要上山",
                    data="@公車/選擇站點 direction=up",
                    displayText="我要上山",
                )
            ),
            QuickReplyItem(
                action=PostbackAction(
                    label="🚍 我要下山",
                    data="@公車/選擇站點 direction=down",
                    displayText="我要下山",
                )
            ),
        ]
    )
    return [TextMessage(text=direction_prompt(), quick_reply=quick_reply)]


@command_handler.add_command("選擇站點")
async def select_stops(events):
    stops_data = await nthuapi.get("/buses/stops")
    params = events.params
    direction = params.get("direction", "up")
    if not stops_data:
        return [TextMessage(text="😵‍💫 抱歉，目前無法取得站點資料，請稍後再試")]

    quick_reply_items = []
    for stop in stops_data:
        stop_name = stop["name"]
        quick_reply_items.append(
            QuickReplyItem(
                action=PostbackAction(
                    label=stop_name[:20],
                    displayText=f"查詢{stop_name}站點公車",
                    data=f"@公車/查詢站點與方向 stop_name={stop_name} direction={direction}",
                )
            )
        )

    quick_reply = QuickReply(items=quick_reply_items)
    text_message = TextMessage(text="請選擇你要搭乘的公車站點", quick_reply=quick_reply)
    return [text_message]


@command_handler.add_command("查詢站點與方向")  # 處理使用者選擇站點後的查詢
async def query_stop_bus(event):
    """查詢指定站點的公車到站資訊"""
    params = event.params
    stop_name = params.get("stop_name")
    if not stop_name:
        return [TextMessage(text="🤔 站點名稱錯誤，請重新選擇")]

    try:
        limit = min(12, max(1, int(params.get("limit", params.get("limits", 5)))))
    except TypeError, ValueError:
        return [TextMessage(text="班次數量必須是整數，請重新查詢")]

    query_params = {
        "day": params.get("day", "current"),
        "limit": limit,
        "route": params.get("route", params.get("bus_type", "all")),
        "direction": params.get("direction", "up"),
        "stop": stop_name,
        "details": True,
    }
    if query_params["route"] not in {"all", "main", "nanda"}:
        return [TextMessage(text="未知的公車路線，請重新選擇")]
    if query_params["day"] not in DAY_NAMES:
        return [TextMessage(text="未知的公車營運日，請重新選擇")]
    if query_params["direction"] not in DIRECTION_NAMES:
        return [TextMessage(text="未知的公車方向，請重新選擇")]
    if params.get("time"):
        query_params["time"] = params["time"]
    schedules = await nthuapi.get("/buses/schedule", params=query_params, cache=False)
    bus_stop_info = _stop_arrivals(schedules, stop_name)[:limit]

    now = datetime.now(TZ)
    direction = query_params["direction"]
    direction_zh = DIRECTION_NAMES[direction]
    first_time, last_time = await _day_bounds(stop_name, direction, _day_kind(now))
    tomorrow_first, _ = await _day_bounds(stop_name, direction, _day_kind(now + timedelta(days=1)))

    container = bus_reply(
        bus_stop_info,
        stop_name,
        direction_zh,
        first_time=first_time,
        last_time=last_time,
        tomorrow_first=tomorrow_first,
        now=now,
    )

    day_zh = DAY_NAMES[query_params["day"]]
    info_message = (
        f"🚌 【{stop_name}】{day_zh}{direction_zh}公車資訊\n"
        f"更新時間：{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # 製作新的 data for postbackaction
    new_params = ""
    for param in params:
        new_params += f" {param}={params[param]}"
    refresh_data = "@公車/查詢站點與方向" + new_params

    flex_message = FlexMessage(
        alt_text=info_message,
        contents=FlexContainer.from_dict(container),
        quickReply=QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label="🔁 重新整理",
                        data=refresh_data,
                        displayText="重新整理動態公車查詢",
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="🚍 重新選擇路線",
                        data="@公車/動態公車查詢",
                        displayText="重新選擇路線",
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="🚏 重新選擇站點",
                        data="@公車/選擇站點 direction=" + direction,
                        displayText="重新選擇站點",
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="🚍 回到選單",
                        data="@公車",
                        displayText="回到選單",
                    )
                ),
            ]
        ),
    )
    return [TextMessage(text=info_message), flex_message]


@command_handler.add_command_with_menu(
    name="最新公車資訊公告",
    title="🚌 最新公車資訊公告",
    description="點擊這裡查詢最新的公車資訊公告",
    actions=[
        PostbackAction(
            label="最新公車資訊公告",
            data="@公車/最新公車資訊公告",
            displayText="最新公車資訊公告",
        )
    ],
)
async def bus_announce(event):  # 最新公車資訊公告
    # 「最新公告」是事務組的大雜燴看板，前幾筆常是垃圾清運與環境消毒；
    # 公車消息有自己的專屬看板。
    announce = await announcecrawler.get("事務組", "校園公車暨巡迴公車公告")
    return [announce]


@command_handler.add_command_with_menu(
    name="校本部公車",
    title="🚌 校本部公車",
    description="點擊這裡查詢校本部公車路線圖",
    actions=[
        PostbackAction(
            label="校本部公車",
            data="@公車/校本部公車",
            displayText="校本部公車",
        )
    ],
)
def main_campus_bus_img(event):  # 校本部公車
    main_campus_bus_img_list = [
        "/buses/images/main_0.jpg",
        "/buses/images/main_1.jpg",
    ]
    for img in main_campus_bus_img_list:
        yield ImageMessage(
            original_content_url=STATIC_URL + img,
            preview_image_url=STATIC_URL + img,
        )


@command_handler.add_command_with_menu(
    name="南大專車",
    title="🚌 南大專車",
    description="點擊這裡查詢南大專車路線圖",
    actions=[
        PostbackAction(
            label="南大專車",
            data="@公車/南大專車",
            displayText="南大專車",
        )
    ],
)
def minor_campus_bus_img(event):  # 南大專車
    minor_campus_bus_img_list = [
        "/buses/images/nanda_0.jpg",
        "/buses/images/nanda_1.jpg",
    ]
    for img in minor_campus_bus_img_list:
        yield ImageMessage(
            original_content_url=STATIC_URL + img,
            preview_image_url=STATIC_URL + img,
        )
