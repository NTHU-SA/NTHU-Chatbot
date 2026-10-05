from linebot.v3.messaging import (
    CarouselColumn,
    Sender,
    TextMessage,
    URIAction,
)

from src.utils import nthuapi
from templates.messages.flex_theme import carousel_message


def _normalize(title) -> str:
    """API 的佈告欄名稱有時含多餘空白（如「最新公告  - 清華書院」），比對前先壓平。"""
    return " ".join(str(title or "").split())


async def get(
    department_name,
    announcement_title,
    language: str = "zh-tw",
    alttext: str = "清華校園情報員",
):
    """
    取得 Rpage 公告的資料，並且轉換成 Soft Glass Flex 輪播。
    Args:
        department_name (str): 系所名稱
        announcement_title (str): 佈告欄名稱（不是 API 的文章標題篩選）
        alttext (str): Line 在無法顯示 FlexMessage 時的替代文字
    Returns:
        LINE FlexMessage，沒有公告時回傳 TextMessage。
    """
    params = {
        "department": department_name,
        "language": language,
    }
    data = await nthuapi.get("/announcements", params=params)
    wanted = _normalize(announcement_title)
    columns = []
    for board in data or []:
        if board.get("language") != language:
            continue
        if wanted and _normalize(board.get("title")) != wanted:
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

    carousel_template = carousel_message(
        alt_text=alttext,
        sender=Sender(name=alttext),
        columns=columns[:10],
    )

    return carousel_template
