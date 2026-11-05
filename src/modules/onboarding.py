"""新手任務：每顆「開始任務」按鈕都打到這裡。

把目標功能的回覆原樣轉出去，再接一張進度卡。進度編在按鈕裡，不存。
"""

from copy import copy

from linebot.v3.messaging import TextMessage

from src.app.handlers.command_handler import command_handler
from templates.messages.onboarding_message import (
    TASK_BY_MODULE,
    next_task,
    parse_done,
    progress_message,
    reward_text,
    welcome_messages,
)

MAX_REPLY_MESSAGES = 5  # LINE 一次回覆最多五則


@command_handler.add_default_menu()
def restart(event):
    """直接輸入「@新手」從頭來。指令事件拿不到 LINE 的顯示名稱，用沒有名字的問候。"""
    return welcome_messages(None)


@command_handler.add_command("任務")
async def start_task(event):
    """`@新手/任務 n=bus done=library`：跑 bus 的功能，再接上把 bus 勾起來的進度卡。"""
    module = event.params.get("n")
    task = TASK_BY_MODULE.get(module)
    if task is None:
        return [TextMessage(text="🤔 沒有這個任務，輸入「@新手」重新開始")]

    previous = parse_done(event.params.get("done"))
    _module, _label, _button, command = task
    run = getattr(event, "run", None) or (
        lambda inner: command_handler.process_message(inner, event.user_id)
    )
    feature = await run(command)
    if isinstance(feature, str):
        # 指令處理器只在功能沒跑成時回純文字（停用、找不到指令、發生錯誤）：
        # 不打勾，同一顆按鈕再給一次
        return [TextMessage(text=feature), progress_message(previous)]

    done = previous | {module}
    messages = list(feature or [])

    tail = TextMessage(text=reward_text()) if next_task(done) is None else progress_message(done)
    # LINE 只顯示最後一則的 Quick Reply。功能的下一步（例如公車的上山／下山）
    # 常常只在 Quick Reply 上，後面接了進度卡就會被藏起來，所以搬到最後一則。
    quick_reply = getattr(messages[-1], "quick_reply", None) if messages else None
    if quick_reply is not None:
        # 複製而不是直接改：功能回覆可能是快取的共用物件（例如自動產生的選單）
        messages[-1] = copy(messages[-1])
        messages[-1].quick_reply = None
        tail.quick_reply = quick_reply
    # 進度卡一定要留：超過上限時截掉的是功能回覆的尾巴，不是下一步的按鈕
    return messages[: MAX_REPLY_MESSAGES - 1] + [tail]
