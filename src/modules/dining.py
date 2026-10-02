import datetime
import random
import re
from urllib.parse import quote

from linebot.v3.messaging import (
    CarouselColumn,
    CarouselTemplate,
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    TemplateMessage,
    TextMessage,
    URIAction,
)

from src.app.handlers.command_handler import command_handler
from src.utils import nthuapi

DINING_API_ENDPOINT = "/dining/"
ERROR_MESSAGE = [TextMessage(text="😵‍💫 抱歉，目前無法取得餐廳資料，請稍後再試")]


def _normalize_phone_number(phone) -> str | None:
    """
    標準化電話號碼，移除多餘字符並處理台灣常見格式。
    Args:
        phone (str): 電話號碼。
    Returns:
        str: 標準化後的電話號碼，若格式錯誤則回傳 None。
    """
    if not phone:
        return None

    phone = re.split(r"[,，]", phone)[0]
    phone = re.sub(r"[^\d+]", "", phone)

    if phone.startswith("+886"):
        phone = "0" + phone[4:]

    if re.match(r"^0\d{8,9}$", phone) or re.match(r"^09\d{8}$", phone):
        return phone
    return None


def _get_today_day_of_week() -> str:
    """取得今天的星期幾，回傳今天是平日還是週六日(weekday, saturday, sunday)。"""
    weekday = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).weekday()
    if weekday == 5:
        return "saturday"
    elif weekday == 6:
        return "sunday"
    return "weekday"


def _create_restaurant_carousel_column(
    building: str | None, restaurant_info: dict, schedule: str | None = None
) -> CarouselColumn:
    """
    建立餐廳的 CarouselColumn。

    Args:
        building(str): 建築物名稱。
        restaurant_info (dict): 包含餐廳資訊的字典，應包含以下欄位：
            - "area"(str): 餐廳所在建築物名稱。
            - "image"(str): 餐廳圖片網址。
            - "name"(str): 餐廳名稱。
            - "note"(str): 餐廳備註。
            - "phone"(str): 餐廳電話號碼。
            - "schedule"(dict): 餐廳營業時間，應為 dict，key 為星期幾，value 為營業時間。
    """
    actions = []
    tel = _normalize_phone_number(restaurant_info.get("phone"))
    if tel:
        actions.append(URIAction(label="電話聯絡", uri=f"tel:{tel}"))
    else:
        actions.append(URIAction(label="餐廳資訊", uri="https://ddfm.site.nthu.edu.tw/"))
    if not building or building == "其他餐廳":
        building_url = restaurant_info["area"]
    else:
        building_url = building
    location_uri = f"https://www.google.com/maps/search/?api=1&query=國立清華大學{building_url}"
    location_uri = quote(location_uri, safe=":/?&=")
    actions.append(
        URIAction(
            label=f"📍 前往{building_url}"[:20],
            uri=location_uri,
        )
    )

    text_parts = {
        "🗒️": restaurant_info["note"],
        "\n🕛": restaurant_info["schedule"].get(schedule or _get_today_day_of_week()),
    }

    text = ""

    for key, value in text_parts.items():
        if not value:
            value = "無"
        text += f"{key}：{value}"

    text = text[:55] + "..." if len(text) > 55 else text  # 避免 text 過長

    return CarouselColumn(
        title=f"🍴 {restaurant_info['area']}/{restaurant_info['name']}"[:40],
        text=text,
        thumbnail_image_url=restaurant_info["image"],
        actions=actions,
    )


@command_handler.add_command_with_menu(
    name="隨機餐廳",
    title="🎲 隨機餐廳",
    description="讓系統隨機推薦一家餐廳，解決選擇困難！",
    actions=[
        PostbackAction(
            label="來個隨機推薦",
            data="@學生餐廳/隨機餐廳",
            displayText="隨機推薦一家餐廳吧！",
        )
    ],
)
async def handle_random_restaurant(event):
    """處理隨機餐廳指令。"""
    restaurant_data = await nthuapi.get(DINING_API_ENDPOINT)
    if not restaurant_data:
        return ERROR_MESSAGE

    restaurant_data = [building for building in restaurant_data if building.get("restaurants")]
    if not restaurant_data:
        return ERROR_MESSAGE
    random_building = random.choice(restaurant_data)
    random_restaurant = random.choice(random_building["restaurants"])

    restaurant_column = _create_restaurant_carousel_column(
        random_building["building"], random_restaurant
    )

    restaurant_card = TemplateMessage(
        alt_text=f"隨機推薦：{random_restaurant['name']}",
        template=CarouselTemplate(columns=[restaurant_column]),
        quick_reply=QuickReply(
            items=[
                QuickReplyItem(
                    action=PostbackAction(
                        label="🎲 再來一家",
                        data="@學生餐廳/隨機餐廳",
                        displayText="再來一家！",
                    )
                ),
                QuickReplyItem(
                    action=PostbackAction(
                        label="🏠 回到主選單",
                        data="@學生餐廳",
                        displayText="回到主選單",
                    )
                ),
            ]
        ),
    )
    return [restaurant_card]


@command_handler.add_command_with_menu(
    name="建築餐廳目錄",
    title="🏢 建築餐廳目錄",
    description="依建築物瀏覽餐廳列表。",
    actions=[
        PostbackAction(
            label="查看餐廳目錄",
            data="@學生餐廳/建築餐廳目錄",
            displayText="查看建築餐廳目錄",
        )
    ],
)
async def handle_building_directory(event):
    """處理建築餐廳目錄指令。"""
    restaurant_data = await nthuapi.get(DINING_API_ENDPOINT)
    if not restaurant_data:
        return ERROR_MESSAGE

    quick_replies = []

    for building_info in restaurant_data:
        quick_replies.append(
            QuickReplyItem(
                action=PostbackAction(
                    label=f"🏢 {building_info['building']}"[:20],
                    displayText=f"查看{building_info['building']}的餐廳",
                    data=f"@學生餐廳/建築餐廳 building_name={building_info['building']}",
                )
            )
        )

    if not quick_replies:
        return [TextMessage(text="😔 目前沒有餐廳資料")]

    building_directory_message = TextMessage(
        text="請選擇建築物", quick_reply=QuickReply(items=quick_replies)
    )
    return [building_directory_message]


@command_handler.add_command("建築餐廳")
async def building_restaurant_command(event):
    """處理建築餐廳指令，顯示特定建築的餐廳列表。"""
    params = event.params
    building_info = await nthuapi.get(DINING_API_ENDPOINT, params=params)
    if not building_info:
        return ERROR_MESSAGE
    building_info = building_info[0]  # 回傳的是 list，只取第一個 dict

    columns = []
    building_name = building_info["building"]
    for restaurant in building_info["restaurants"]:
        columns.append(_create_restaurant_carousel_column(building_name, restaurant))

    if not columns:
        return ERROR_MESSAGE
    building_restaurants_carousel = TemplateMessage(
        alt_text=f"{building_name}餐廳列表",
        template=CarouselTemplate(columns=columns[:10]),
    )
    return [building_restaurants_carousel]


@command_handler.add_command_with_menu(
    name="週末餐廳選單",
    title="☀️ 週末營業餐廳",
    description="查看週末有營業的餐廳。",
    actions=[
        PostbackAction(
            label="查看週末營業",
            data="@學生餐廳/週末餐廳選單",
            displayText="查看週末有營業的餐廳！",
        )
    ],
)
def handle_weekend_restaurants_menu(event):
    """處理週末營業餐廳指令。"""
    # 要求使用者選取週六或週日
    quick_replies = [
        QuickReplyItem(
            action=PostbackAction(
                label="⭐ 週六",
                displayText="查看週六營業的餐廳",
                data="@學生餐廳/週末餐廳 schedule=saturday",
            )
        ),
        QuickReplyItem(
            action=PostbackAction(
                label="⭐ 週日",
                displayText="查看週日營業的餐廳",
                data="@學生餐廳/週末餐廳 schedule=sunday",
            )
        ),
    ]
    return [TextMessage(text="請選擇要查看星期幾", quick_reply=QuickReply(items=quick_replies))]


@command_handler.add_command("週末餐廳")
async def weekend_restaurants_command(event):
    """處理週末營業餐廳指令，顯示週末有營業的餐廳列表。"""
    params = event.params
    schedule = params.get("schedule", "today")
    if schedule not in {"today", "weekday", "saturday", "sunday"}:
        return [TextMessage(text="未知的營業日，請重新選擇")]
    restaurant_data = await nthuapi.get(
        "/dining/open", params={"schedule": schedule}, cache=schedule != "today"
    )
    if not restaurant_data:
        return ERROR_MESSAGE

    open_restaurants_columns = []
    for restaurant in restaurant_data:
        open_restaurants_columns.append(
            _create_restaurant_carousel_column(
                building=None,
                restaurant_info=restaurant,
                schedule=None if schedule == "today" else schedule,
            )
        )

    messages = []
    for offset in range(0, len(open_restaurants_columns), 10):
        carousel = TemplateMessage(
            alt_text="營業餐廳",
            template=CarouselTemplate(columns=open_restaurants_columns[offset : offset + 10]),
        )
        messages.append(carousel)
    messages = messages[:5]
    return messages
