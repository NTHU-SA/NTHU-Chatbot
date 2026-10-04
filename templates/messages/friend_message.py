from urllib.parse import quote

from linebot.v3.messaging import FlexContainer, FlexMessage

from .flex_theme import bubble, inset, text

ADD_FRIEND_URL = "https://line.me/R/ti/p/@741vdfol"
SHARE_TEXT = f"清華校園情報員｜查公車、餐廳、圖書館，也能問 AI 校園問題。\n{ADD_FRIEND_URL}"
SHARE_URL = f"https://line.me/R/share?text={quote(SHARE_TEXT, safe='')}"


def friend_actions() -> list[dict]:
    return [
        {"type": "uri", "label": "加入情報員好友", "uri": ADD_FRIEND_URL},
        {"type": "uri", "label": "分享給 LINE 好友", "uri": SHARE_URL},
    ]


def friend_message() -> FlexMessage:
    card = bubble(
        "清華校園情報員",
        [
            text("把校園生活的小幫手，介紹給你的朋友。"),
            inset(
                [
                    text("公車・餐廳・圖書館", weight="bold"),
                    text("快速指令查資訊，AI 網頁對話陪你找答案。", color="muted"),
                    text("LINE ID：@741vdfol", color="brand"),
                ]
            ),
        ],
        friend_actions(),
    )
    return FlexMessage(alt_text="加入或分享清華校園情報員", contents=FlexContainer.from_dict(card))
