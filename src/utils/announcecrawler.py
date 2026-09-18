from linebot.v3.messaging import (
    CarouselColumn,
    CarouselTemplate,
    Sender,
    TemplateMessage,
    TextMessage,
    URIAction,
)

from src.utils import nthuapi


async def get(
    department_name,
    announcement_title,
    language: str = "zh-tw",
    alttext: str = "清華校園情報員",
):
    """
    取得 Rpage 公告的資料，並且轉換成 Line 的 CarouselTemplate 格式
    Args:
        department_name (str): 系所名稱
        announcement_title (str): 佈告欄名稱（不是 API 的文章標題篩選）
        alttext (str): Line 在無法顯示 FlexMessage 時的替代文字
    Returns:
        Line 的 CarouselTemplate
    """
    params = {
        "department": department_name,
        "language": language,
    }
    data = await nthuapi.get("/announcements/", params=params)
    columns = []
    for board in data or []:
        if board.get("language") != language:
            continue
        if announcement_title and board.get("title") != announcement_title:
            continue
        for article in board.get("articles", []):
            link = article.get("link") or board.get("link")
            if not link:
                continue
            title = article.get("title") or "未命名公告"
            if len(title) > 40:
                title = title[:37] + "..."
            columns.append(
                CarouselColumn(
                    title=title,
                    text="發佈日期： " + (article.get("date") or "未知"),
                    actions=[URIAction(label="更多資訊", uri=link)],
                )
            )
    if not columns:
        return TextMessage(text="目前沒有符合條件的公告，請稍後再試")

    carousel_template = TemplateMessage(
        alt_text=alttext,
        sender=Sender(name=alttext),
        template=CarouselTemplate(columns=columns[:10]),
    )

    return carousel_template
