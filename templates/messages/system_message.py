from linebot.v3.messaging import FlexContainer, FlexMessage

from .flex_theme import FLEX_THEME, bubble, text


def system_message(title, info):
    system_message_json = bubble(title, [text(info)], size=FLEX_THEME["components"]["notice_size"])
    return [FlexMessage(alt_text="系統訊息", contents=FlexContainer.from_dict(system_message_json))]
