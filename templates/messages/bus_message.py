from .flex_theme import FLEX_THEME, bubble, inset, text

BUS_TYPE_NAMES = {
    "route_83": "83 路公車",
    "large-sized_bus": "大型校園公車",
    "middle-sized_bus": "中型校園公車",
}
ROUTE_COLORS = {
    "main_red": "red",
    "main_green": "green",
    "nanda_route_1": "blue",
    "nanda_route_2": "blue",
}
NANDA_ROUTE_NAMES = {"nanda_route_1": "路線一", "nanda_route_2": "路線二"}


def route_badge(line: str | None, status: str = "路線待確認", *, compact: bool = False) -> dict:
    key = ROUTE_COLORS.get(line, line)
    route = FLEX_THEME["routes"].get(key or "")
    label = route["label"] if route else status
    if compact and key == "blue":
        label = label.split(" · ", 1)[0]
    if not compact and line in NANDA_ROUTE_NAMES:
        label += " · " + NANDA_ROUTE_NAMES[line]
    badge = {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": route["tint"] if route else FLEX_THEME["colors"]["background"],
        "cornerRadius": FLEX_THEME["radii"]["badge"],
        "paddingAll": FLEX_THEME["spacing"]["row"],
        "contents": [
            {
                **text(label, size="badge" if compact else "caption", weight="bold"),
                "color": route["ink"] if route else FLEX_THEME["colors"]["muted"],
            }
        ],
    }
    if compact:
        badge.pop("paddingAll")
        badge.update(
            flex=0,
            paddingTop=FLEX_THEME["spacing"]["badge_y"],
            paddingBottom=FLEX_THEME["spacing"]["badge_y"],
            paddingStart=FLEX_THEME["spacing"]["badge_x"],
            paddingEnd=FLEX_THEME["spacing"]["badge_x"],
        )
    return badge


def bus_carousel(
    arrivals: list[dict],
    stop_name: str,
    direction_name: str,
    refresh_data: str,
    bus_type_names: dict[str, str] | None = None,
) -> dict:
    cards = []
    for arrival in arrivals:
        route = FLEX_THEME["routes"].get(ROUTE_COLORS.get(arrival.get("line"), ""))
        card = bubble(
            (bus_type_names if bus_type_names is not None else BUS_TYPE_NAMES).get(
                arrival["bus_type"], "校園公車"
            ),
            [
                inset(
                    [
                        text("到站時間", color="muted"),
                        {
                            **text(
                                arrival.get("arrive_time") or "未知", size="arrival", weight="bold"
                            ),
                            "color": route["ink"] if route else FLEX_THEME["colors"]["brand"],
                        },
                    ]
                )
            ],
            [
                {
                    "type": "postback",
                    "label": "更新到站資訊",
                    "data": refresh_data,
                    "displayText": "重新整理動態公車查詢",
                }
            ],
        )
        card["header"]["spacing"] = FLEX_THEME["spacing"]["item_gap"]
        subtitle = f"{stop_name} · {direction_name}"
        if arrival.get("line") in NANDA_ROUTE_NAMES:
            subtitle += " · 南大專車 · " + NANDA_ROUTE_NAMES[arrival["line"]]
        card["header"]["contents"] = [
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": FLEX_THEME["spacing"]["item_gap"],
                "alignItems": "flex-start",
                "contents": [
                    {**card["header"]["contents"][0], "flex": 1},
                    route_badge(
                        arrival.get("line"),
                        arrival.get("route_status", "路線待確認"),
                        compact=True,
                    ),
                ],
            },
            text(subtitle, size="caption", color="muted"),
        ]
        card["footer"]["contents"][0]["margin"] = FLEX_THEME["spacing"]["button_margin"]
        card["footer"]["contents"] = [
            text(f"發車站點：{arrival['dep_stop']}"),
            text(f"發車時間：{arrival['dep_time']}"),
            text(arrival.get("description") or "無備註", color="muted"),
            *card["footer"]["contents"],
        ]
        cards.append(card)
    return {"type": "carousel", "contents": cards}


def bus_direction_bubble() -> dict:
    return bubble(
        "搭校巴，先選方向",
        [
            text("上山含往南大校區，下山含往校本部。選好方向後，再選擇搭乘站點。", color="muted"),
            {
                "type": "box",
                "layout": "vertical",
                "spacing": FLEX_THEME["spacing"]["item_gap"],
                "contents": [route_badge(line) for line in ("red", "green", "blue")],
            },
        ],
        [
            {
                "type": "postback",
                "label": "我要上山",
                "data": "@公車/選擇站點 direction=up",
                "displayText": "上山",
            },
            {
                "type": "postback",
                "label": "我要下山",
                "data": "@公車/選擇站點 direction=down",
                "displayText": "下山",
            },
        ],
    )
