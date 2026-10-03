from linebot.v3.messaging import FlexContainer, FlexMessage

from .liff_message import BRAND_COLOR, liff_url

SESSION_LIMIT_NOTE = "網頁對話最多保留 50 則，超過時最久沒更新的會自動刪除。"


def _section(title: str, lines: list[str]) -> dict:
    contents = [
        {"type": "text", "text": title, "weight": "bold", "size": "sm", "color": BRAND_COLOR}
    ]
    contents += [
        {"type": "text", "text": line, "size": "sm", "color": "#333333", "wrap": True}
        for line in lines
    ]
    return {"type": "box", "layout": "vertical", "spacing": "xs", "contents": contents}


def help_message(prefixes: list[str], liff_id: str | None = None) -> FlexMessage:
    """
    使用說明泡泡。

    `prefixes` 是聊天室內可用的 `@` 指令前綴（依 bot_config.yaml 動態產生），
    `liff_id` 有值時附上直接開啟網頁對話的按鈕。
    """
    commands = "、".join(f"@{prefix}" for prefix in prefixes)
    sections = [
        _section(
            "💬 問本汪任何校園問題",
            [
                "直接輸入問題，本汪會回一顆按鈕，點開網頁就能和我聊天。",
                "網頁裡可以看到我正在查哪些資料，也能開好幾個對話分開聊。",
            ],
        ),
        _section(
            "⚡ 快速指令",
            [
                f"輸入 {commands}，或直接點下方選單。",
                "指令後面可以接功能名稱，例如「@圖書館/空間狀況」；只打前綴會列出該分類的選單。",
            ],
        ),
        _section(
            "📌 小提醒",
            [
                "群組裡只能用 @ 指令；想問 AI 請私訊本汪。",
                SESSION_LIMIT_NOTE,
                "覺得本汪太吵，可以關閉「提醒」，不要封鎖我嗚嗚。",
            ],
        ),
    ]

    bubble: dict = {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": BRAND_COLOR,
            "contents": [
                {
                    "type": "text",
                    "text": "清華校園情報員使用說明 ฅ'ω'ฅ",
                    "wrap": True,
                    "color": "#FFFFFF",
                    "weight": "bold",
                    "size": "md",
                }
            ],
        },
        "body": {"type": "box", "layout": "vertical", "spacing": "lg", "contents": sections},
    }
    if liff_id:
        bubble["footer"] = {
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
                        "label": "開始和本汪聊天",
                        "uri": liff_url(liff_id),
                    },
                }
            ],
        }
    return FlexMessage(alt_text="清華校園情報員使用說明", contents=FlexContainer.from_dict(bubble))
