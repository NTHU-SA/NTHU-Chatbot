"""Soft Glass tokens and components shared by every LINE card."""

import json
from typing import TypedDict

from linebot.v3.messaging import CarouselColumn, FlexContainer, FlexMessage, QuickReply, Sender


class FlexTheme(TypedDict):
    colors: dict[str, str]
    typography: dict[str, str]
    spacing: dict[str, str]
    radii: dict[str, str]
    components: dict[str, str]
    routes: dict[str, dict[str, str]]


FLEX_THEME: FlexTheme = {
    "colors": {
        "background": "#F3F1F7",
        "surface": "#FFFFFF",
        "tint": "#E9E4F1",
        "selected": "#E6E0F0",
        "brand": "#7362A2",
        "text": "#2D2935",
        "muted": "#696173",
        "line": "#DED9E7",
    },
    "typography": {
        "body": "sm",
        "caption": "sm",
        "badge": "xs",
        "title": "lg",
        "section": "md",
        "value": "md",
        "arrival": "xxl",
    },
    "spacing": {
        "header": "20px",
        "body": "20px",
        "footer": "16px",
        "inset": "16px",
        "row": "12px",
        "badge_y": "4px",
        "badge_x": "8px",
        "section_gap": "lg",
        "item_gap": "sm",
        "button_margin": "md",
    },
    "radii": {"inset": "16px", "row": "12px", "badge": "12px"},
    "components": {
        "bubble_size": "mega",
        "notice_size": "kilo",
        "border_width": "1px",
        "button_height": "sm",
        "primary_button_style": "primary",
        "secondary_button_style": "link",
        "hero_aspect_ratio": "20:13",
    },
    "routes": {
        "red": {"label": "紅線", "ink": "#A23B50", "tint": "#F8E9ED"},
        "green": {"label": "綠線", "ink": "#256745", "tint": "#E7F2EB"},
        "blue": {"label": "藍線 · 南大專車", "ink": "#315DA0", "tint": "#E7EEFA"},
    },
}


def theme_signature() -> str:
    return json.dumps(FLEX_THEME, sort_keys=True)


def text(content: str, *, size: str = "body", color: str = "text", **options) -> dict:
    return {
        "type": "text",
        "text": content,
        "size": FLEX_THEME["typography"][size],
        "color": FLEX_THEME["colors"][color],
        "wrap": True,
        **options,
    }


def header(title: str) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": FLEX_THEME["spacing"]["header"],
        "backgroundColor": FLEX_THEME["colors"]["tint"],
        "contents": [text(title, size="title", weight="bold")],
    }


def inset(contents: list[dict], **options) -> dict:
    return {
        "type": "box",
        "layout": "vertical",
        "paddingAll": FLEX_THEME["spacing"]["inset"],
        "backgroundColor": FLEX_THEME["colors"]["surface"],
        "borderColor": FLEX_THEME["colors"]["line"],
        "borderWidth": FLEX_THEME["components"]["border_width"],
        "cornerRadius": FLEX_THEME["radii"]["inset"],
        "spacing": FLEX_THEME["spacing"]["item_gap"],
        "contents": contents,
        **options,
    }


def button(action: dict, *, primary: bool = False) -> dict:
    return {
        "type": "button",
        "style": FLEX_THEME["components"][
            "primary_button_style" if primary else "secondary_button_style"
        ],
        "color": FLEX_THEME["colors"]["brand"],
        "height": FLEX_THEME["components"]["button_height"],
        "action": action,
    }


def bubble(
    title: str,
    contents: list[dict],
    actions: list[dict] | None = None,
    *,
    size: str | None = None,
) -> dict:
    result = {
        "type": "bubble",
        "size": size or FLEX_THEME["components"]["bubble_size"],
        "header": header(title),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["body"],
            "spacing": FLEX_THEME["spacing"]["section_gap"],
            "contents": contents,
        },
        "styles": {
            "body": {"backgroundColor": FLEX_THEME["colors"]["background"]},
            "footer": {"backgroundColor": FLEX_THEME["colors"]["surface"]},
        },
    }
    if actions:
        result["footer"] = {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["footer"],
            "spacing": FLEX_THEME["spacing"]["item_gap"],
            "contents": [
                button(action, primary=index == 0) for index, action in enumerate(actions)
            ],
        }
    return result


def carousel_message(
    alt_text: str,
    columns: list[CarouselColumn],
    *,
    quick_reply: QuickReply | None = None,
    sender: Sender | None = None,
) -> FlexMessage:
    """Retain existing menu actions/images while replacing unstyleable templates."""
    if not 1 <= len(columns) <= 12:
        raise ValueError("A Flex carousel requires 1 to 12 cards")
    cards = []
    for column in columns:
        card = bubble(
            column.title or alt_text,
            [text(column.text)],
            [action.to_dict() for action in column.actions],
        )
        if column.thumbnail_image_url:
            card["hero"] = {
                "type": "image",
                "url": column.thumbnail_image_url,
                "size": "full",
                "aspectRatio": FLEX_THEME["components"]["hero_aspect_ratio"],
                "aspectMode": "cover",
            }
        cards.append(card)
    return FlexMessage(
        alt_text=alt_text[:400],
        contents=FlexContainer.from_dict({"type": "carousel", "contents": cards}),
        quick_reply=quick_reply,
        sender=sender,
    )
