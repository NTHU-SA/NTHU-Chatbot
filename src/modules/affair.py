from linebot.v3.messaging import URIAction

from src.app.handlers.command_handler import command_handler


@command_handler.add_command_with_menu(
    name="課務組各類課程查詢",
    title="課務組各類課程查詢",
    description="點擊這裡查詢各類課程。",
    actions=[
        URIAction(
            label="各類課程查詢",
            uri="https://curricul.site.nthu.edu.tw/p/404-1208-102114.php?Lang=zh-tw",
            altUri=None,
        )
    ],
)
def courses_service_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="清華助學系統",
    title="清華助學系統",
    description="點擊這裡進入清華助學系統。",
    actions=[
        URIAction(
            label="清華助學系統",
            uri="https://meo110.wwlc.nthu.edu.tw/",
            altUri=None,
        )
    ],
)
def scholar_service_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="清華社團列表",
    title="清華社團列表",
    description="點擊這裡查看清華社團列表。",
    actions=[
        URIAction(
            label="清華學生列表",
            uri="https://outrageous-savory-d52.notion.site/d33567eea7814fc6b91744351eb2ba6a",
            altUri=None,
        )
    ],
)
def cluds_service_menu(event):
    return None


@command_handler.add_command_with_menu(
    name="南大校區服務",
    title="南大校區服務選單",
    description="點擊這裡查詢南大校區教室資訊。",
    actions=[
        URIAction(
            label="南大校區服務",
            uri="https://curricul.site.nthu.edu.tw/p/412-1208-16075.php?Lang=zh-tw",
            altUri=None,
        )
    ],
)
def nanda_service_menu(event):
    return None
