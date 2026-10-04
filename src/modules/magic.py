import json
import random

from linebot.v3.messaging import (
    PostbackAction,
    QuickReply,
    QuickReplyItem,
    TextMessage,
)

from src.app.handlers.command_handler import command_handler
from templates.messages.friend_message import friend_message


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
@command_handler.add_command("分享狗狗情報員|分享給好友|新增情報員好友")
def share_template(event):
    return [TextMessage(text="汪！歡迎分享給更多朋友認識我！"), friend_message()]


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
