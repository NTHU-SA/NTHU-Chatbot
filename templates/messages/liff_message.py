from urllib.parse import quote

from linebot.v3.messaging import FlexContainer, FlexMessage

BRAND_COLOR = "#6F00D2"
MAX_QUESTION_CHARS = 500
MAX_SESSION_KEY_CHARS = 64
PREVIEW_CHARS = 120


def liff_url(
    liff_id: str, question: str | None = None, session_key: str | None = None
) -> str:
    """
    LIFF 網址。

    `session_key` 讓同一則泡泡永遠開啟同一個對話（後端 get-or-create）；
    `question` 以 `q` 傳遞，前端在對話是新的時候自動送出。
    """
    base = f"https://liff.line.me/{liff_id}"
    params = []
    if session_key:
        params.append(f"s={quote(session_key[:MAX_SESSION_KEY_CHARS], safe='')}")
    if question:
        params.append(f"q={quote(question[:MAX_QUESTION_CHARS], safe='')}")
    return f"{base}?{'&'.join(params)}" if params else base


def open_web_chat(
    liff_id: str,
    question: str | None = None,
    *,
    session_key: str | None = None,
    greeting: bool = False,
) -> FlexMessage:
    """
    一顆按鈕開啟 LIFF 對話的 Flex 泡泡。

    `question` 會顯示預覽並帶進 LIFF；`session_key` 綁定這則泡泡對應的對話；
    `greeting=True` 用於加好友時的歡迎版本。
    """
    if greeting:
        title = "歡迎加入 — 狗狗情報員"
        subtitle = "點下方按鈕就能和本汪聊天，查公車、課程、公告、餐廳都可以！"
        label = "開始和本汪聊天"
    else:
        title = "交給本汪查！ฅ'ω'ฅ"
        subtitle = "這個問題本汪會在網頁裡幫你查校園資料，還能看到我正在用哪些工具。"
        label = "在網頁中詢問"

    body_contents = [
        {"type": "text", "text": subtitle, "size": "sm", "color": "#666666", "wrap": True},
    ]
    if question:
        body_contents.append(
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "paddingAll": "8px",
                "backgroundColor": "#EDE7F6",
                "cornerRadius": "6px",
                "contents": [
                    {
                        "type": "text",
                        "text": question[:PREVIEW_CHARS],
                        "size": "sm",
                        "color": "#333333",
                        "wrap": True,
                        "maxLines": 3,
                    }
                ],
            }
        )

    bubble = {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": BRAND_COLOR,
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": BRAND_COLOR,
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": label,
                        "uri": liff_url(liff_id, question, session_key),
                    },
                }
            ],
        },
    }
    return FlexMessage(alt_text=title, contents=FlexContainer.from_dict(bubble))
