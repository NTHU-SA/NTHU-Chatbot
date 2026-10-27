import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest
from linebot.v3.messaging import CarouselColumn, FlexContainer, PostbackAction, URIAction

from src.app.handlers.command_handler import CommandHandler
from src.modules.library.library import env
from templates.messages import help_message, open_web_chat, system_message
from templates.messages.flex_theme import FLEX_THEME, carousel_message
from templates.messages.friend_message import ADD_FRIEND_URL, SHARE_TEXT, SHARE_URL, friend_message

COLORS = FLEX_THEME["colors"]


def test_global_theme_has_all_editable_style_groups():
    assert set(FLEX_THEME) == {"colors", "typography", "spacing", "radii", "components", "routes"}
    assert set(FLEX_THEME["routes"]) == {"red", "green", "blue"}


def test_palette_has_readable_body_and_button_contrast():
    def luminance(color):
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(
            value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
        )

    def contrast(first, second):
        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    for foreground in ("text", "muted"):
        for background in ("surface", "background", "tint"):
            assert contrast(COLORS[foreground], COLORS[background]) >= 4.5
    for background in ("surface", "background"):
        assert contrast(COLORS["brand"], COLORS[background]) >= 4.5
    assert contrast(COLORS["surface"], COLORS["brand"]) >= 4.5
    for route in FLEX_THEME["routes"].values():
        assert contrast(route["ink"], route["tint"]) >= 4.5
        assert contrast(route["ink"], COLORS["surface"]) >= 4.5


@pytest.mark.parametrize(
    "message",
    [
        open_web_chat("123-example", "公車何時到？"),
        open_web_chat("123-example", greeting=True),
        help_message(["公車", "圖書館"], "123-example"),
        help_message(["公車"]),
        system_message("系統通知", "請稍後再試")[0],
        friend_message(),
    ],
)
def test_python_cards_use_shared_theme_and_round_trip(message):
    card = message.contents.to_dict()
    assert card["header"]["backgroundColor"] == COLORS["tint"]
    assert card["header"]["contents"][0]["color"] == COLORS["text"]
    assert card["styles"]["body"]["backgroundColor"] == COLORS["background"]
    assert FlexContainer.from_dict(card).to_dict() == card
    for component in card.get("footer", {}).get("contents", []):
        assert component["color"] == COLORS["brand"]


def test_friend_links_use_supplied_account_and_percent_encoded_share_text():
    actions = friend_message().contents.footer.contents
    assert actions[0].action.uri == ADD_FRIEND_URL == "https://line.me/R/ti/p/@741vdfol"
    assert actions[1].action.uri == SHARE_URL
    parsed = urlparse(SHARE_URL)
    assert parsed.path == "/R/share"
    assert parse_qs(parsed.query) == {"text": [SHARE_TEXT]}
    assert ADD_FRIEND_URL in SHARE_TEXT
    assert "hero" not in friend_message().contents.to_dict()


async def test_share_postback_and_old_aliases_resolve_to_same_card():
    from src.app.handlers.command_handler import command_handler

    expected = await command_handler.process_message("@神奇海螺/分享清華校園情報員", "user")
    for alias in ["分享給好友", "分享狗狗情報員", "新增情報員好友"]:
        messages = await command_handler.process_message(f"@神奇海螺/{alias}", "user")
        assert [m.to_dict() for m in messages] == [m.to_dict() for m in expected]
    menu = await command_handler.process_message("@神奇海螺", "user")
    action = menu[0].contents.contents[0].footer.contents[0].action
    assert action.data == "@神奇海螺/分享給好友"


def test_carousel_preserves_actions_images_and_does_not_mutate_source():
    column = CarouselColumn(
        title="A long campus title",
        text="First line\nSecond line",
        thumbnail_image_url="https://example.test/image.jpg",
        actions=[
            URIAction(label="Call", uri="tel:035715131"),
            PostbackAction(label="Go back", data="@公車", display_text="Bus menu"),
        ],
    )
    original = copy.deepcopy(column.to_dict())
    card = carousel_message("Campus menu", [column]).contents.contents[0]
    assert card.hero.url == column.thumbnail_image_url
    assert card.body.contents[0].text == column.text
    assert [b.action.to_dict() for b in card.footer.contents] == original["actions"]
    assert column.to_dict() == original


@pytest.mark.parametrize("count", [0, 13])
def test_carousel_rejects_invalid_bubble_count(count):
    columns = [
        CarouselColumn(
            text="Description", actions=[URIAction(label="Read", uri="https://nthu.edu.tw")]
        )
    ] * count
    with pytest.raises(ValueError):
        carousel_message("Menu", columns)


async def test_menu_splits_at_line_carousel_limit_and_deduplicates_aliases():
    handler = CommandHandler()

    def command(event):
        return []

    command.__module__ = "src.modules.bus"
    for index in range(13):
        handler.add_command_with_menu(
            name=[f"command-{index}", f"alias-{index}"],
            title=f"Feature {index}",
            description="Description",
            actions=[PostbackAction(label="Open", data=f"@公車/command-{index}")],
        )(command)
    messages = await handler.auto_generate_default_menu("bus")
    assert [len(message.contents.contents) for message in messages] == [12, 1]
    assert await handler.auto_generate_default_menu("bus") is messages


def test_jinja_cards_escape_dynamic_data_and_preserve_zero():
    library_card = json.loads(
        env.get_template("library_space.json.jinja").render(
            spaces=[{"zonename": '討論室 "A"\n樓上', "count": 0}]
        )
    )
    assert FlexContainer.from_dict(library_card).to_dict() == library_card
    assert library_card["body"]["contents"][1]["contents"][1]["text"] == "0"
    info_card = json.loads(
        env.get_template("library_info.json.jinja").render(
            data={"今日日期": '日期 "A"', "總館開館": "08:00\n22:00", "人社開館": "休館"}
        )
    )
    assert FlexContainer.from_dict(info_card).to_dict() == info_card


async def test_global_theme_changes_reach_all_builders_templates_and_cached_menus(monkeypatch):
    from scripts.build_flex_preview import build_samples
    from src.app.handlers.command_handler import command_handler

    previous = await command_handler.auto_generate_default_menu("bus")
    changed = copy.deepcopy(FLEX_THEME)
    changed["colors"].update(brand="#4D4480", tint="#ECE8F6", background="#F6F4FA")
    changed["typography"].update(title="xl", body="md")
    changed["spacing"].update(header="24px", body="24px", footer="20px", inset="20px")
    changed["radii"].update(inset="20px", row="16px")
    changed["components"].update(bubble_size="giga", button_height="md")
    for group, values in changed.items():
        monkeypatch.setitem(FLEX_THEME, group, values)
    samples = await build_samples()
    for sample in samples:
        for message in sample["messages"]:
            content = message["contents"]
            cards = content["contents"] if content["type"] == "carousel" else [content]
            for card in cards:
                # 公車卡刻意用路線色當標題底，說明泡泡則把標題讓給內文
                route_tints = {route["tint"] for route in FLEX_THEME["routes"].values()}
                # 路線待確認的卡沒有專屬色，退回中性底
                allowed = {"#ECE8F6", FLEX_THEME["colors"]["background"], *route_tints}
                assert card["header"]["backgroundColor"] in allowed
                assert card["header"]["paddingAll"] == "24px"
                heading = card["header"]["contents"][0]
                if heading["type"] == "box":
                    heading = heading["contents"][0]
                assert heading["size"] in {"xl", "sm"}
                assert card["body"]["paddingAll"] == "24px"
                assert card["styles"]["body"]["backgroundColor"] == "#F6F4FA"
                # 系統訊息與公車說明泡泡用較窄的 notice_size
                if sample["id"] not in {"system", "bus-none"}:
                    assert card["size"] == "giga"
                else:
                    assert card["size"] == FLEX_THEME["components"]["notice_size"]
                # 公車到站卡的「重新整理」刻意染成該線的顏色
                route_inks = {route["ink"] for route in FLEX_THEME["routes"].values()}
                for component in card.get("footer", {}).get("contents", []):
                    if component["type"] == "button":
                        assert component["color"] in {
                            "#4D4480",
                            FLEX_THEME["colors"]["surface"],
                            FLEX_THEME["colors"]["brand"],
                            FLEX_THEME["colors"]["muted"],
                            *route_inks,
                        }
                        assert component["height"] == "md"
    assert await command_handler.auto_generate_default_menu("bus") is not previous
