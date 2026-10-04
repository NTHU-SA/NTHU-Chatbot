from .flex_theme import FLEX_THEME, bubble, inset, text


def library_space_bubble(spaces: list[dict]) -> dict:
    rows = [
        {
            "type": "box",
            "layout": "horizontal",
            "contents": [
                text("空間區域", color="muted", flex=3),
                text("尚有空間", color="muted", align="end", flex=1),
            ],
        }
    ]
    for space in spaces:
        count = space["count"] if "count" in space else "-"
        rows.append(
            inset(
                [
                    text(space.get("zonename") or "-", flex=3),
                    text(
                        str(count), size="value", weight="bold", color="brand", align="end", flex=1
                    ),
                ],
                layout="horizontal",
                paddingAll=FLEX_THEME["spacing"]["row"],
                cornerRadius=FLEX_THEME["radii"]["row"],
            )
        )
    card = bubble(
        "圖書館空間現況",
        rows,
        [
            {"type": "uri", "label": "預約空間", "uri": "https://libsms.lib.nthu.edu.tw/build/"},
            {"type": "message", "label": "更新資訊", "text": "@圖書館/空間狀況"},
            {"type": "postback", "label": "回到首頁", "data": "@圖書館"},
        ],
    )
    card["header"]["contents"][0]["action"] = {
        "type": "uri",
        "label": "圖書館預約系統",
        "uri": "https://libsms.lib.nthu.edu.tw/build/",
    }
    card["body"]["spacing"] = FLEX_THEME["spacing"]["item_gap"]
    return card


def library_info_bubble(data: dict[str, str]) -> dict:
    rows = [text("本日開館", size="section", weight="bold")]
    for name in ("總館", "人社", "南大"):
        rows.append(
            {
                "type": "box",
                "layout": "horizontal",
                "spacing": FLEX_THEME["spacing"]["item_gap"],
                "contents": [
                    text(name, color="muted", flex=1),
                    text(data.get(f"{name}開館") or "N/A", align="end", flex=3),
                ],
            }
        )
    card = bubble(
        "圖書館資訊",
        [text(data["今日日期"], color="muted"), inset(rows)],
        [{"type": "postback", "label": "回到首頁", "data": "@圖書館"}],
    )
    card["header"]["contents"][0]["action"] = {
        "type": "uri",
        "label": "圖書館首頁",
        "uri": "https://www.lib.nthu.edu.tw/",
    }
    return card
