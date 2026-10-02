from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader
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

STATIC_URL = "https://data.nthusa.tw"
JINJA_ENV = Environment(loader=FileSystemLoader("src/modules/bus/templates"))


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
    quick_reply = QuickReply(
        items=[
            QuickReplyItem(
                action=PostbackAction(
                    label="🚍 我要上山",
                    data="@公車/選擇站點 direction=up",
                    displayText="上山",
                )
            ),
            QuickReplyItem(
                action=PostbackAction(
                    label="🚍 我要下山",
                    data="@公車/選擇站點 direction=down",
                    displayText="下山",
                )
            ),
        ]
    )
    return [
        TextMessage(
            text="請問你想要上山還是下山呢！\n（上山含往南大校區，下山含往校本部）",
            quick_reply=quick_reply,
        )
    ]


@command_handler.add_command("選擇站點")
async def select_stops(events):
    stops_data = await nthuapi.get("/buses/info/stops")
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
        limits = min(12, max(1, int(params.get("limits", 5))))
    except (TypeError, ValueError):
        return [TextMessage(text="班次數量必須是整數，請重新查詢")]

    query_params = {
        "day": params.get("day", "current"),
        "limits": limits,
        "bus_type": params.get("bus_type", "all"),
        "direction": params.get("direction", "up"),
    }
    bus_stop_info = await nthuapi.get(
        f"/buses/stops/{quote(stop_name, safe='')}", params=query_params, cache=False
    )
    bus_stop_info = [item for item in bus_stop_info or [] if item][:limits]

    if not bus_stop_info:
        return [TextMessage(text=f"🚌 抱歉，目前 {stop_name} 站點沒有公車資訊")]

    day_mapping = {"current": "即時", "weekday": "平日", "weekend": "假日"}
    day_zh = day_mapping.get(query_params["day"], "")
    direction_zh = "上山" if query_params["direction"] == "up" else "下山"
    now_time = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    info_message = (
        f"🚌 【{stop_name}】{day_zh}{direction_zh}公車資訊\n更新時間：{now_time}"
    )

    # 製作新的 data for postbackaction
    new_params = ""
    for param in params:
        new_params += f" {param}={params[param]}"

    template = JINJA_ENV.get_template("bus_flex_message.json.jinja")
    rendered_json = template.render(
        bus_stop_info=bus_stop_info,
        bus_type_names={
            "route_83": "83 路公車",
            "large-sized_bus": "大型校園公車",
            "middle-sized_bus": "中型校園公車",
        },
    )
    flex_message = FlexMessage(
        alt_text=info_message,
        contents=FlexContainer.from_json(rendered_json),
        quickReply=QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label="🔁 重新整理",
                        data="@公車/查詢站點與方向" + new_params,
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
                # 選擇站點
                QuickReplyItem(
                    action=PostbackAction(
                        label="🚏 重新選擇站點",
                        data="@公車/選擇站點 direction=" + query_params["direction"],
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
    announce = await announcecrawler.get("事務組", "最新公告")
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
