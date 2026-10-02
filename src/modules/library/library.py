import datetime
import json
import urllib.parse
from email.utils import parsedate_to_datetime

from jinja2 import Environment, FileSystemLoader
from linebot.v3.messaging import (
    CarouselColumn,
    CarouselTemplate,
    ConfirmTemplate,
    FlexContainer,
    FlexMessage,
    PostbackAction,
    Sender,
    TemplateMessage,
    TextMessage,
    URIAction,
)

from src.app.handlers.command_handler import command_handler
from src.utils import nthuapi

templates_path = "src/modules/library/templates"

# 初始化 Jinja 環境
env = Environment(loader=FileSystemLoader(templates_path))

LIBRARY_API_ENDPOINT = "/libraries"


@command_handler.add_command_with_menu(
    name="空間狀況",
    title="圖書館空間狀況",
    description="點擊這裡查詢圖書館各空間的使用狀況。",
    actions=[
        PostbackAction(
            label="空間狀況",
            data="@圖書館/空間狀況",
            displayText="空間狀況",
        )
    ],
)
async def lib_space_flex_message(event):
    data = await nthuapi.get(LIBRARY_API_ENDPOINT + "/space", cache=False)
    if not data:
        return [TextMessage(text="目前沒有圖書館空間資料，請稍後再試")]
    # 讀取並渲染 Jinja 模板
    template = env.get_template("library_space.json.jinja")
    flex_template_str = template.render(spaces=data)
    flex_template = json.loads(flex_template_str)
    result = FlexMessage(alt_text="圖書館空間現況", contents=FlexContainer.from_dict(flex_template))
    return [result]


@command_handler.add_command_with_menu(
    name="圖書館消息",
    title="圖書館最新消息",
    description="點擊這裡查看圖書館發布的最新消息。",
    actions=[
        PostbackAction(
            label="圖書館消息",
            data="@圖書館/rss type=news page=1",  # 預設顯示第一頁
            displayText="圖書館消息",
            inputOption=None,
            fillInText=None,
        )
    ],
)
def rss_news_menu(event):  # 這個函數只是為了創建菜單，實際消息由 rss 函數處理
    return None


@command_handler.add_command("rss")
async def rss(event):
    rss_type = event.params.get("type", "news")
    if rss_type not in {"news", "eresources", "exhibit", "branches"}:
        return [TextMessage(text="未知的圖書館消息分類")]
    try:
        page_num = max(1, int(event.params.get("page", 1)))
    except (TypeError, ValueError):
        return [TextMessage(text="頁碼必須是整數")]
    return await lib_news_message(rss_type, page_num)


async def lib_news_message(rss, page_num):
    data = await nthuapi.get(LIBRARY_API_ENDPOINT + "/rss/" + rss)
    template_list = []
    result = libxmltemplate(data=data, page_num=int(page_num))
    alttext = "清華圖書館資訊系統"
    if not result:
        return [TextMessage(text="這一頁沒有圖書館消息，請回到選單重新查詢")]

    template_list.append(
        TemplateMessage(
            alt_text=alttext,
            sender=Sender(name=alttext),
            template=CarouselTemplate(columns=result),
        )
    )
    if len(result) != 10:
        no_more_page = TemplateMessage(
            alt_text="要回到第一頁嗎？",
            template=ConfirmTemplate(
                text=f"你目前在第 {page_num} 頁，但沒有更多頁數了，要回到第一頁嗎？",
                actions=[
                    PostbackAction(
                        label="回到第一頁",
                        data=f"@圖書館/rss type={rss} page=1",
                    ),
                    PostbackAction(label="回到首頁", data="@圖書館"),
                ],
            ),
        )
        template_list.append(no_more_page)
    else:
        confirm_template_message = TemplateMessage(
            alt_text="要查看下一頁嗎？",
            template=ConfirmTemplate(
                text=f"你目前在第 {page_num} 頁，要查看下一頁嗎？",
                actions=[
                    PostbackAction(
                        label="查看下一頁",
                        data=f"@圖書館/rss type={rss} page={page_num + 1}",
                    ),
                    PostbackAction(label="回到首頁", data="@圖書館"),
                ],
            ),
        )
        template_list.append(confirm_template_message)
    return template_list


def convert_datetime_string(datetime_string):
    try:
        datetime_obj = parsedate_to_datetime(datetime_string)
    except (TypeError, ValueError, OverflowError):
        try:
            datetime_obj = datetime.datetime.fromisoformat(datetime_string)
        except (TypeError, ValueError):
            return "未知"
    return datetime_obj.strftime("%Y 年 %m 月 %d 日")


def libxmltemplate(data, page_num=1):
    # 分隔頁數
    start = (page_num - 1) * 10
    data = (data or [])[start : start + 10]

    if data == []:
        return []

    columns = []
    for item in data:
        image_url = (item.get("image") or {}).get("url")
        thumbnail_image_url = urllib.parse.quote(image_url, safe=":/?&=%") if image_url else None
        title = item.get("title") or "圖書館消息"
        if len(title) > 40:
            title = title[:37] + "..."
        published_date = convert_datetime_string(item.get("pubDate"))
        col = CarouselColumn(
            thumbnail_image_url=thumbnail_image_url,
            title=title,
            text="發佈日期： " + published_date,
            actions=[
                URIAction(
                    label="更多資訊",
                    uri=item.get("link") or "https://www.lib.nthu.edu.tw/",
                )
            ],
        )
        columns.append(col)
    return columns
