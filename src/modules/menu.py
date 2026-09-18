from linebot.v3.messaging import PostbackAction

from src.app.handlers.command_handler import command_handler


@command_handler.add_command_with_menu(
    name="公車時刻表",
    title="公車時刻表",
    description="點擊這裡查詢公車時刻表",
    actions=[
        PostbackAction(
            label="公車",
            data="@選單/公車時間表",  # 請根據實際 command handler 設定調整 data
            displayText="公車時刻表",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def bus_schedule_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="清華校內工讀",
    title="清華校內工讀",
    description="點擊這裡查詢清華校內工讀資訊",
    actions=[
        PostbackAction(
            label="清華校內工讀",
            data="@選單/清華校內工讀",  # 請根據實際 command handler 設定調整 data
            displayText="清華校內工讀",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def campus_job_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="校務專區",
    title="校務專區",
    description="點擊這裡查詢校務專區",
    actions=[
        PostbackAction(
            label="校務專區",
            data="@選單/校務專區",  # 請根據實際 command handler 設定調整 data
            displayText="校務專區",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def academic_affairs_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="校園地圖查詢",
    title="校園地圖查詢",
    description="點擊這裡查詢校園地圖",
    actions=[
        PostbackAction(
            label="校園地圖查詢",
            data="@選單/校園地圖查詢",  # 請根據實際 command handler 設定調整 data
            displayText="校園地圖查詢",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def campus_map_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="神奇海螺",
    title="神奇海螺",
    description="點擊這裡使用神奇海螺",
    actions=[
        PostbackAction(
            label="神奇海螺",
            data="@選單/神奇海螺",  # 請根據實際 command handler 設定調整 data
            displayText="神奇海螺",
            inputOption=None,
            fillInText=None,
        ),
    ],
)
def magic_conch_menu(event):
    return None
