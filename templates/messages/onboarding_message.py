"""新手任務：加好友後用「做一次」認識三個功能，做完給稱號。

進度不存在任何地方 —— 聊天室刻意不留個人紀錄（見 usage_stats）。
已完成的任務編在下一顆按鈕的 postback 裡，所以勾的時機是「開始做」而不是
「做完」；文案用「開始任務」，不說謊。
"""

from linebot.v3.messaging import FlexContainer, FlexMessage, TextMessage

from .flex_theme import FLEX_THEME, text

TITLE = "清華通"
PREFIX = "@新手/任務"

# (module id, 清單上的字, 按鈕, 按下去跑的指令)
# 清單上的字要和指令真正給的東西一致：餐廳沒有「今天有什麼」的指令，所以是逛選單
TASKS = (
    ("bus", "查一次校車", "🚌 開始任務 1", "@公車/動態公車查詢"),
    ("library", "看圖書館剩幾個位子", "📚 開始任務 2", "@圖書館/空間狀況"),
    ("dining", "逛一次學生餐廳", "🍜 開始任務 3", "@學生餐廳"),
)
TASK_BY_MODULE = {task[0]: task for task in TASKS}


def parse_done(raw: str | None) -> set[str]:
    """postback 帶的 `done=bus,library` → 已完成的模組集合；壞字串當作沒做過。"""
    return {part for part in (raw or "").split(",") if part in TASK_BY_MODULE}


def encode_done(done: set[str]) -> str:
    return ",".join(module for module, *_ in TASKS if module in done)


def next_task(done: set[str]):
    return next((task for task in TASKS if task[0] not in done), None)


def task_postback(done: set[str], task) -> dict:
    """下一顆按鈕：帶著目前進度，點了就把這個任務加進去。"""
    module, _label, button, _command = task
    data = f"{PREFIX} n={module}"
    if done:
        data += f" done={encode_done(done)}"
    return {"type": "postback", "label": button, "data": data, "displayText": button[2:]}


def _checklist_lines(done: set[str]) -> list[str]:
    rows = []
    for index, (module, label, *_rest) in enumerate(TASKS, 1):
        tick = "✅" if module in done else "⬜"
        rows.append(f"{tick} 任務 {index}　{label}")
    return rows


def welcome_text(display_name: str | None) -> str:
    greeting = f"{display_name}，初次見面！ฅ'ω'ฅ" if display_name else "嗨！初次見面 ฅ'ω'ฅ"
    return (
        f"{greeting}\n"
        "我是清華校園情報員，學生會做的校園助手。\n\n"
        "本汪準備了三個新手任務，做完就是清華通了 👇\n"
        "其他功能都在下方選單。"
    )


def progress_message(done: set[str]) -> FlexMessage:
    return FlexMessage(
        alt_text="新手任務進度", contents=FlexContainer.from_dict(progress_card(done))
    )


def welcome_messages(display_name: str | None) -> list:
    """問候加一張任務卡。

    「開始任務 1」放在卡上不放 Quick Reply：Quick Reply 只要使用者先點了別的
    （例如下方選單）就消失，而 `@新手` 不在說明裡，消失了就回不來。
    """
    return [TextMessage(text=welcome_text(display_name)), progress_message(set())]


def progress_card(done: set[str]) -> dict:
    """做完一個任務後的進度卡。

    用卡不用純文字，是因為 Quick Reply 只活在最後一則訊息上：使用者接著去點
    公車選單、選方向，那排按鈕就不見了。卡會留在聊天室，往上滑還找得到下一步。
    """
    task = next_task(done)
    remaining = len(TASKS) - len(done)
    cheer = {2: "還有兩個，加油 ฅ'ω'ฅ", 1: "最後一個了！"}.get(remaining, "")
    rows = [
        {
            **text(line, weight="bold" if line.startswith("✅") else "regular"),
            "color": FLEX_THEME["colors"]["text" if line.startswith("✅") else "muted"],
        }
        for line in _checklist_lines(done)
    ]
    if cheer:
        rows.append(text(cheer, size="caption", color="muted", margin="md"))
    card = {
        "type": "bubble",
        "size": FLEX_THEME["components"]["notice_size"],
        "header": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["header"],
            "backgroundColor": FLEX_THEME["colors"]["tint"],
            "contents": [text("新手任務", size="title", weight="bold")],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["body"],
            "spacing": FLEX_THEME["spacing"]["item_gap"],
            "backgroundColor": FLEX_THEME["colors"]["surface"],
            "contents": rows,
        },
        "styles": {
            "body": {"backgroundColor": FLEX_THEME["colors"]["background"]},
            "footer": {"backgroundColor": FLEX_THEME["colors"]["surface"]},
        },
    }
    if task:
        card["footer"] = {
            "type": "box",
            "layout": "vertical",
            "paddingAll": FLEX_THEME["spacing"]["footer"],
            "backgroundColor": FLEX_THEME["colors"]["surface"],
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": FLEX_THEME["colors"]["brand"],
                    "height": FLEX_THEME["components"]["button_height"],
                    "action": task_postback(done, task),
                }
            ],
        }
    return card


def reward_text() -> str:
    """任務按鈕是 postback，拿不到顯示名稱，所以稱「你」。"""
    return (
        "🎉 三個任務全部完成！\n\n"
        "本汪授予你稱號：\n"
        f"🎓 {TITLE}\n\n"
        "從今天起，校園大小事問本汪就對了。"
    )
