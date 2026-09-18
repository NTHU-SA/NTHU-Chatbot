from linebot.v3.messaging import PostbackAction

from src.app.handlers.command_handler import command_handler
from src.utils import announcecrawler


@command_handler.add_command_with_menu(
    name="清華校內工讀",
    title="清華校內工讀",
    description="點擊這裡查看清華校內工讀資訊",
    actions=[
        PostbackAction(
            label="查看清華校內工讀！",
            data="@公佈欄/清華校內工讀",  # 修改 data 以符合實際 command
            displayText="查看清華校內工讀！",
            inputOption=None,
            fillInText=None,
        )
    ],
)
async def recruitment_carousel(event):
    alttext = "清華校內工讀"
    announce = await announcecrawler.get("清華公佈欄", "校內徵才", alttext=alttext)
    return [announce]


@command_handler.add_command_with_menu(
    name="清華藝文活動",
    title="清華藝文活動",
    description="點擊這裡尋找清華藝文活動",
    actions=[
        PostbackAction(
            label="找找清華藝文活動！",
            data="@公佈欄/清華藝文活動",
            displayText="找找清華藝文活動！",
            inputOption=None,
            fillInText=None,
        )
    ],
)
async def art_event(event):
    alttext = "清華藝文活動"
    announce = await announcecrawler.get("清華公佈欄", "藝文活動", alttext=alttext)
    return [announce]
