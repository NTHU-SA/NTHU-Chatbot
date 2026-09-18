import asyncio
import time  # 引入 time 模組，雖然不是直接用 time.sleep，但 asyncio.to_thread 內部可能用到

from linebot.v3.messaging import PostbackAction, TextMessage

from src.app.handlers.command_handler import command_handler

command_1 = "@公車/查詢站點與方向 stop_name=綜二館 direction=down"


@command_handler.add_command_with_menu(
    name="command_1",
    title="查詢站點與方向",
    description=command_1,
    actions=[
        PostbackAction(
            label="command_1",
            data=command_1,
            displayText=command_1,
        )
    ],
)
def dev_command_1(event):
    return [TextMessage(text=command_1)]


# 測試 async
@command_handler.add_command_with_menu(
    name="command_2",
    title="測試 async",
    description="使用 async def 開發",
    actions=[
        PostbackAction(
            label="command_2",
            data="@開發者/command_2",
            displayText="command_2",
        )
    ],
)
async def dev_command_2(event):
    return [TextMessage(text="command_2")]


# 測試 IO 阻塞 (修復後應為非阻塞)
@command_handler.add_command_with_menu(
    name="command_3",
    title="測試 IO 阻塞 (修復)",
    description="使用 asyncio.to_thread(time.sleep, 10) 模擬非阻塞 IO",
    actions=[
        PostbackAction(
            label="command_3",
            data="@開發者/command_3",
            displayText="command_3",
        )
    ],
)
async def dev_command_3(event):
    # 使用 asyncio.to_thread 在獨立的執行緒中執行 time.sleep
    await asyncio.to_thread(
        time.sleep, 10
    )  # time.sleep 是同步阻塞函數，需要放到執行緒中執行
    return [TextMessage(text="command_3 完成 (非阻塞)")]


# 測試 IO 非阻塞 (修復後應為非阻塞)
@command_handler.add_command_with_menu(
    name="command_4",
    title="測試 IO 非阻塞 (修復)",
    description="使用 asyncio.sleep(10) 模擬非阻塞 IO",
    actions=[
        PostbackAction(
            label="command_4",
            data="@開發者/command_4",
            displayText="command_4",
        )
    ],
)
async def dev_command_4(event):
    await asyncio.sleep(10)  # asyncio.sleep 是非阻塞的協程
    return [TextMessage(text="command_4 完成 (非阻塞)")]
