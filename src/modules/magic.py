import json
import random

from linebot.v3.messaging import (
    FlexContainer,
    FlexMessage,
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    TextMessage,
)

from src.app.handlers.command_handler import command_handler


@command_handler.add_command_with_menu(
    name="分享清華校園情報員",
    title="分享清華校園情報員",
    description="快讓更多朋友認識情報員吧！\n我會很開心哦！",
    actions=[
        PostbackAction(
            label="分享給好友",
            data="@神奇海螺/分享給好友",
            displayText="分享給好友",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def share_template(event):
    # 分享給好友
    share_template_json = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "baseline",
            "contents": [
                {
                    "type": "icon",
                    "url": "https://scdn.line-apps.com/n/channel_devcenter/img/fx/review_gold_star_28.png",
                    "offsetStart": "10px",
                },
                {
                    "type": "text",
                    "text": "掃描加入 — 清華校園情報員",
                    "offsetStart": "25px",
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "adjustMode": "shrink-to-fit",
                },
            ],
            "backgroundColor": "#6F00D2",
        },
        "hero": {
            "type": "image",
            "url": "https://i.imgur.com/40t9Qo0.png",
            "position": "relative",
            "align": "center",
            "gravity": "center",
            "offsetTop": "lg",
            "size": "5xl",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "@741vdfol",
                    "color": "#7B7B7B",
                    "align": "center",
                    "size": "md",
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "contents": [],
                    "justifyContent": "center",
                    "alignItems": "center",
                    "margin": "lg",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "uri",
                        "label": "分享給LINE好友",
                        "uri": "https://line.me/R/nv/recommendOA/@741vdfol",
                    },
                    "style": "secondary",
                    "height": "sm",
                    "offsetTop": "sm",
                },
            ],
            "paddingBottom": "xxl",
        },
    }
    response = []
    response.append(TextMessage(text="汪！歡迎分享給更多朋友認識我！"))
    response.append(
        FlexMessage(
            type="flex",
            altText="分享QRcode",
            contents=FlexContainer.from_dict(share_template_json),
        )
    )
    return response


@command_handler.add_command_with_menu(
    name="笑一下",
    title="笑話",
    description="聽聽四散在校園的奇聞軼事吧！讓自己輕鬆一下！",
    actions=[
        PostbackAction(
            label="笑一下",
            data="@神奇海螺/笑一下",
            displayText="來個笑話聽聽！",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def joke_response(event):
    with open("data/anecdotes.json", encoding="utf-8") as f:
        anecdotes_data = json.load(f)
        anecdote = random.choice(anecdotes_data)["content"]
    return [
        TextMessage(
            text=anecdote,
            quick_reply=QuickReply(
                items=[
                    QuickReplyItem(
                        action=PostbackAction(
                            label="再一個！",
                            data="@神奇海螺/笑一下",
                            displayText="再一個！",
                        ),
                    ),
                    QuickReplyItem(
                        action=PostbackAction(
                            label="回到選單", data="@神奇海螺", displayText="回到選單"
                        ),
                    ),
                ]
            ),
        )
    ]


@command_handler.add_command_with_menu(
    name="今日運勢",
    title="今日運勢",
    description="狗狗結合最新的人工智慧\n告訴你今天要做什麼！（僅供娛樂）",
    actions=[
        PostbackAction(
            label="今日運勢",
            data="@神奇海螺/今日運勢",
            displayText="查詢今日運勢",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def fortune(event):
    random_fortune = random.choice(["大吉", "中吉", "小吉", "吉", "末吉", "凶", "大凶"])
    return [TextMessage(text=f"今天你的運勢是：{random_fortune}！")]
