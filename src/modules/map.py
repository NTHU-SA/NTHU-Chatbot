import random

from linebot.v3.messaging import (
    LocationMessage,
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    TextMessage,
    URIAction,
)

from src.app.handlers.command_handler import command_handler
from src.utils import nthuapi


@command_handler.add_default_menu()
async def list_quick_reply(event):
    map_data = await nthuapi.get("/locations/")
    if not map_data:
        return [TextMessage(text="目前沒有地點資料，請稍後再試")]
    quick_replies = []
    for location in map_data:
        if len(location["name"]) > 20:
            continue
        quick_replies.append(
            QuickReplyItem(
                action=PostbackAction(
                    label=location["name"],
                    displayText=location["name"],
                    data=f"@地圖/地點 query={location['name'].replace(' ', '_space_')}",
                )
            )
        )
    if len(quick_replies) > 10:
        quick_replies = random.sample(quick_replies, 10)
    return [TextMessage(text="請選擇地點", quick_reply=QuickReply(items=quick_replies))]


@command_handler.add_command("地點")
async def handle_location_command(event):
    params = event.params
    query = params.get("query", "").strip()
    if not query:
        return [TextMessage(text="請提供要查詢的地點名稱")]
    query = query.replace("_space_", " ")
    map_data = await nthuapi.get("/locations/search", params={"query": query})
    if not map_data:
        return [TextMessage(text="找不到地點資料")]
    exact_matches = [location for location in map_data if location["name"] == query]
    messages = []
    for location in (exact_matches or map_data)[:5]:
        map_url = f"https://maps.google.com/?q={location['latitude']},{location['longitude']}"
        messages.append(
            LocationMessage(
                title=location["name"][:100],
                address=location["name"][:100],
                latitude=float(location["latitude"]),
                longitude=float(location["longitude"]),
                quickReply=QuickReply(
                    items=[QuickReplyItem(action=URIAction(label="地圖導航", uri=map_url))]
                ),
            )
        )
    return messages
