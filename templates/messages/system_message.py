from linebot.v3.messaging import FlexContainer, FlexMessage


def system_message(title, info):
    system_message_json = {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": title, "weight": "bold", "size": "md"}],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": info, "wrap": True, "size": "xs"}],
        },
        "styles": {
            "header": {"backgroundColor": "#D1C4E9"},
            "body": {"backgroundColor": "#EDE7F6"},
        },
    }
    return [FlexMessage(alt_text="系統訊息", contents=FlexContainer.from_dict(system_message_json))]
