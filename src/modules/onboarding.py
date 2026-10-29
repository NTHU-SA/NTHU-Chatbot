"""新手任務：每顆「開始任務」按鈕都打到這裡。

把目標功能的回覆原樣轉出去，再接一張進度卡。進度編在按鈕裡，不存。
"""

from linebot.v3.messaging import FlexContainer, FlexMessage, QuickReply, TextMessage

from src.app.handlers.command_handler import command_handler
from templates.messages.onboarding_message import (
    TASK_BY_MODULE,
    next_task,
    parse_done,
    progress_card,
    reward_text,
    welcome_quick_reply,
    welcome_text,
)


@command_handler.add_default_menu()
def restart(event):
    """直接輸入「@新手」從頭來。指令事件拿不到 LINE 的顯示名稱，用沒有名字的問候。"""
    return [
        TextMessage(
            text=welcome_text(None),
            quick_reply=QuickReply.from_dict(welcome_quick_reply()),
        )
    ]


@command_handler.add_command("任務")
async def start_task(event):
    """`@新手/任務 n=bus done=library`：跑 bus 的功能，再接上把 bus 勾起來的進度卡。"""
    module = event.params.get("n")
    task = TASK_BY_MODULE.get(module)
    if task is None:
        return [TextMessage(text="🤔 沒有這個任務，輸入「@新手」重新開始")]

    done = parse_done(event.params.get("done")) | {module}
    _module, _label, _button, command = task
    feature = await command_handler.process_message(command, event.user_id)
    messages = [TextMessage(text=feature)] if isinstance(feature, str) else list(feature or [])

    if next_task(done) is None:
        messages.append(TextMessage(text=reward_text(None)))
    else:
        messages.append(
            FlexMessage(
                alt_text="新手任務進度",
                contents=FlexContainer.from_dict(progress_card(done)),
            )
        )
    return messages[:5]  # LINE 一次最多五則
