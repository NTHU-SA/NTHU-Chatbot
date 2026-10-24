"""公車訊息 builder。

對話流程：選單 →（選方向 → 選站點）→ 到站結果。
到站結果依路線分組，一條線一張卡；沒有車時改回說明泡泡。
"""

import re
from datetime import datetime, timedelta, timezone

from .flex_theme import FLEX_THEME, text

TZ = timezone(timedelta(hours=8))
NTHUMODS_URL = "https://nthumods.com/zh/bus"

BUS_TYPE_NAMES = {
    "route_83": "83 路公車",
    "large-sized_bus": "大型車",
    "middle-sized_bus": "中型車",
}
ROUTE_COLORS = {
    "main_red": "red",
    "main_green": "green",
    "nanda_route_1": "blue",
    "nanda_route_2": "blue",
}
NANDA_ROUTE_NAMES = {"nanda_route_1": "路線一", "nanda_route_2": "路線二"}

# 卡片標題用短名；FLEX_THEME 的 label 留給方向泡泡那種有空間的地方
CARD_ROUTE_LABEL = {"red": "紅線", "green": "綠線", "blue": "藍線 · 南大"}
ROUTE_ORDER = ("red", "green", "blue")
# API 沒給 line 時不依車種猜測，另開一張中性卡，不讓班次憑空消失
UNKNOWN_ROUTE = "unknown"
UNKNOWN_LABEL = "路線待確認"

# 停靠站那行要塞進一張卡，全名會擠爆
SHORT_STOP = {
    "人社院&生科館": "人社院",
    "台積館": "台積",
    "奕園停車場": "奕園",
    "教育學院大樓&南門停車場": "教院南門",
    "南大校區校門口右側(食品路校牆邊)": "南大",
}

# description 混了三種東西：車型、停駛/加開、收費。分開處理，不要整串塞進灰字。
_BUS_TYPE_NOISE = re.compile(r"^[大中小]型巴士$")  # 與 bus_type 重複
_SUSPEND_ON = re.compile(r"週([一二三四五六日天])停駛")
_FARE_HINT = ("付費", "收費")
WEEKDAY_CHAR = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# 收費提醒需要自己的警示色：紅線的紅會在藍線卡上被誤讀成路線色
FARE_INK = "#B25E00"
FARE_TINT = "#FDF0E2"

NO_BUS_COPY = {
    # 末班時間不顯示：剩餘班次全停駛時也會走這條，那時末班其實還沒到，
    # 寫出來會變成「已過」配一個未來時間，自相矛盾。
    "after_last": ("末班車已過", None),
    "before_first": ("尚未發車", "今日首班"),
    "no_service": ("今日無班次", None),
}


def _color(name: str) -> str:
    return FLEX_THEME["colors"][name]


def _separator() -> dict:
    return {"type": "separator", "margin": "md", "color": _color("line")}


def short_stop(stop: str) -> str:
    return SHORT_STOP.get(stop, stop)


def runs_today(description: str | None, weekday: int | None = None) -> bool:
    """備註說今天停駛就回 False。只比對今天：「週六停駛」在星期二照常顯示。"""
    note = (description or "").strip()
    if "停駛" not in note:
        return True
    today = datetime.now(TZ).weekday() if weekday is None else weekday
    named = _SUSPEND_ON.findall(note)
    if named:
        return all(WEEKDAY_CHAR[char] != today for char in named)
    return False  # 沒指定星期的「停駛」一律當作不開


def split_note(description: str | None) -> tuple[str | None, str | None]:
    """把備註拆成（收費提醒, 其他備註）；與 bus_type 重複的車型字眼丟掉。"""
    note = (description or "").strip()
    if not note:
        return None, None
    parts = [part.strip() for part in re.split(r"[,，、]", note) if part.strip()]
    parts = [part for part in parts if not _BUS_TYPE_NOISE.match(part)]
    fare = [part for part in parts if any(hint in part for hint in _FARE_HINT)]
    other = [part for part in parts if part not in fare]
    return ("、".join(fare) or None), ("、".join(other) or None)


def service_state(
    first_time: str | None, last_time: str | None, now: datetime | None = None
) -> str:
    """比對現在時刻與今日首末班，決定是哪一種「沒有車」。"""
    if not first_time or not last_time:
        return "no_service"
    current = (now or datetime.now(TZ)).strftime("%H:%M")
    if current < first_time:
        return "before_first"
    return "after_last"


def _minutes_until(arrive_time: str, now: datetime) -> int | None:
    try:
        hour, minute = (int(part) for part in arrive_time.split(":"))
    except ValueError, AttributeError:
        return None
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target < now - timedelta(minutes=1):
        target += timedelta(days=1)
    return round((target - now).total_seconds() / 60)


def fare_badge(note: str) -> dict:
    """要花錢的車給橘色標籤，不能跟一般備註長得一樣。"""
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": FARE_TINT,
        "cornerRadius": FLEX_THEME["radii"]["badge"],
        "paddingAll": FLEX_THEME["spacing"]["row"],
        "margin": "md",
        "contents": [
            {
                "type": "text",
                "text": "💰 " + note,
                "size": FLEX_THEME["typography"]["caption"],
                "color": FARE_INK,
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def stops_line(stops: list[str], stop_name: str) -> dict:
    """停靠站序列，使用者所在的站標深色。"""
    spans = [{"type": "span", "text": "經過 ", "color": _color("muted")}]
    for index, stop in enumerate(stops):
        if index:
            spans.append({"type": "span", "text": " › ", "color": _color("line")})
        here = stop == stop_name
        spans.append(
            {
                "type": "span",
                "text": short_stop(stop),
                "color": _color("text") if here else _color("muted"),
                "weight": "bold" if here else "regular",
            }
        )
    return {
        "type": "text",
        "text": "經過 " + " › ".join(short_stop(stop) for stop in stops),
        "contents": spans,
        "size": FLEX_THEME["typography"]["caption"],
        "wrap": True,
    }


def _route_card(
    route_key: str,
    arrivals: list[dict],
    stop_name: str,
    direction_name: str,
    updated_at: str,
    now: datetime,
) -> dict:
    route = FLEX_THEME["routes"].get(route_key) or {
        "ink": _color("muted"),
        "tint": _color("background"),
    }
    label = CARD_ROUTE_LABEL.get(route_key, UNKNOWN_LABEL)
    first, rest = arrivals[0], arrivals[1:]
    minutes = _minutes_until(first["arrive_time"], now)
    if minutes is None:
        countdown = "時間未知"
    elif minutes <= 0:
        countdown = "即將進站"
    else:
        countdown = f"還有 {minutes} 分鐘"

    fare, note = split_note(first.get("description"))
    body = [
        {
            **text(f"{first['arrive_time']} 到站", size="arrival", weight="bold"),
            "color": route["ink"],
        },
        text(
            f"{countdown} · {BUS_TYPE_NAMES.get(first.get('bus_type'), '校園公車')}", size="value"
        ),
    ]
    if fare:
        body.append(fare_badge(fare))
    body += [
        _separator(),
        text(f"{short_stop(first['dep_stop'])} {first['dep_time']} 發車"),
    ]
    if rest:
        body.append(
            text("後續到站 " + " · ".join(item["arrive_time"] for item in rest[:3]), color="muted")
        )
    if note:
        body.append(text(note, size="caption", color="muted"))
    if first.get("stops"):
        body += [_separator(), stops_line(first["stops"], stop_name)]

    return {
        "type": "bubble",
        "size": FLEX_THEME["components"]["bubble_size"],
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["header"],
            "backgroundColor": route["tint"],
            "spacing": "xs",
            "contents": [
                {
                    **text(label, size="title", weight="bold"),
                    "color": route["ink"],
                },
                {
                    **text(f"{stop_name} · {direction_name} · {updated_at} 更新", size="caption"),
                    "color": route["ink"],
                },
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["body"],
            "spacing": FLEX_THEME["spacing"]["item_gap"],
            "backgroundColor": _color("surface"),
            "contents": body,
        },
        "styles": {
            "body": {"backgroundColor": _color("background")},
            "footer": {"backgroundColor": _color("surface")},
        },
    }


def bus_arrival_carousel(
    arrivals: list[dict],
    stop_name: str,
    direction_name: str,
    now: datetime | None = None,
) -> dict:
    """依路線分組，紅綠藍各一張。該站沒有的線不出現，所以張數是 1~3 不是固定 3。"""
    moment = now or datetime.now(TZ)
    grouped: dict[str, list[dict]] = {}
    for arrival in arrivals:
        key = ROUTE_COLORS.get(arrival.get("line"), UNKNOWN_ROUTE)
        grouped.setdefault(key, []).append(arrival)
    for items in grouped.values():
        items.sort(key=lambda item: item["arrive_time"])

    updated_at = moment.strftime("%H:%M")
    cards = [
        _route_card(key, grouped[key], stop_name, direction_name, updated_at, moment)
        for key in (*ROUTE_ORDER, UNKNOWN_ROUTE)
        if grouped.get(key)
    ]
    if not cards:
        raise ValueError("No arrivals to render")
    return {"type": "carousel", "contents": cards}


def no_bus_bubble(
    stop_name: str,
    direction_name: str,
    *,
    state: str,
    today_time: str | None = None,
    next_time: str | None = None,
    next_label: str = "明日首班",
) -> dict:
    """沒有車的三種情況共用一張泡泡。寫死「末班車已過」在清晨會變成假資訊。"""
    headline, time_label = NO_BUS_COPY[state]
    rows = [{**text(headline, size="title", weight="bold"), "color": _color("text")}]
    if time_label and today_time:
        rows.append(text(f"{time_label} {today_time}", color="muted"))
    if next_time:
        rows += [_separator(), text(f"{next_label} {next_time}")]

    return {
        "type": "bubble",
        "size": FLEX_THEME["components"]["notice_size"],
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["header"],
            "backgroundColor": _color("tint"),
            "contents": [text(f"{stop_name} · {direction_name}", size="caption", color="muted")],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["body"],
            "spacing": FLEX_THEME["spacing"]["item_gap"],
            "backgroundColor": _color("surface"),
            "contents": rows,
        },
        "styles": {
            "body": {"backgroundColor": _color("background")},
            "footer": {"backgroundColor": _color("surface")},
        },
    }


def bus_reply(
    arrivals: list[dict],
    stop_name: str,
    direction_name: str,
    *,
    first_time: str | None,
    last_time: str | None,
    tomorrow_first: str | None = None,
    now: datetime | None = None,
) -> dict:
    """回傳到站輪播，或在尚未發車／沒有車時回傳說明泡泡。

    清晨不顯示「還有 421 分鐘」的到站卡：比對現在是否早於今日首班就夠，
    不需要分鐘數門檻。有真實班次時相信班次，沒有才用推算的狀態。
    """
    moment = now or datetime.now(TZ)
    state = service_state(first_time, last_time, moment)
    if state == "before_first":
        return no_bus_bubble(stop_name, direction_name, state=state, today_time=first_time)

    runnable = [item for item in arrivals if runs_today(item.get("description"), moment.weekday())]
    if not runnable:
        return no_bus_bubble(stop_name, direction_name, state=state, next_time=tomorrow_first)
    return bus_arrival_carousel(runnable, stop_name, direction_name, moment)


def _menu_button(action: dict, *, primary: bool = False, external: bool = False) -> dict:
    if external:
        color = FLEX_THEME["routes"]["blue"]["tint"]
    else:
        color = _color("brand") if primary else _color("surface")
    return {
        "type": "button",
        "style": "primary" if primary else "secondary",
        "color": color,
        "height": FLEX_THEME["components"]["button_height"],
        "action": action,
    }


def bus_menu_bubble() -> dict:
    """五個選項收成一張卡，取代四張要滑的輪播。"""
    options = [
        ("動態查詢", "@公車/動態公車查詢"),
        ("最新資訊", "@公車/最新公車資訊公告"),
        ("校本部路線圖", "@公車/校本部公車"),
        ("南大專車", "@公車/南大專車"),
    ]
    contents = [
        _menu_button(
            {"type": "postback", "label": label, "data": data, "displayText": label},
            primary=index == 0,
        )
        for index, (label, data) in enumerate(options)
    ]
    contents.append(_separator())
    contents.append(
        _menu_button(
            {"type": "uri", "label": "NTHUMods 公車查詢 ↗", "uri": NTHUMODS_URL}, external=True
        )
    )
    return {
        "type": "bubble",
        "size": FLEX_THEME["components"]["bubble_size"],
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["header"],
            "backgroundColor": _color("tint"),
            "spacing": "xs",
            "contents": [
                text("🚌 公車", size="title", weight="bold"),
                text("想查什麼？", size="caption", color="muted"),
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["body"],
            "spacing": FLEX_THEME["spacing"]["item_gap"],
            "backgroundColor": _color("background"),
            "contents": contents,
        },
        "styles": {
            "body": {"backgroundColor": _color("background")},
            "footer": {"backgroundColor": _color("surface")},
        },
    }


def direction_prompt() -> str:
    """選方向只是二選一，用純文字泡泡加 Quick Reply 就夠，不必開一張 Flex 卡。"""
    labels = " · ".join(FLEX_THEME["routes"][key]["label"] for key in ROUTE_ORDER)
    return "搭校巴，先選方向\n上山含往南大校區，下山含往校本部\n" + labels
