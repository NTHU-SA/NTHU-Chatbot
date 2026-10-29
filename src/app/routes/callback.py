import asyncio

from aiohttp import ClientError
from fastapi import APIRouter, HTTPException, Request
from google.api_core.exceptions import GoogleAPICallError
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiException,
    QuickReply,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    FollowEvent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
    UnfollowEvent,
)

from log import logger
from src.app.background import BackgroundWrites
from src.app.handlers.command_handler import command_handler
from src.application.models.identity import AccountDisabledError
from src.application.services.usage_stats import FOLLOW, MESSAGE, POSTBACK, UNFOLLOW
from templates.messages import open_web_chat
from templates.messages.onboarding_message import welcome_quick_reply, welcome_text

router = APIRouter()

# 不用加 @ 也能叫出使用說明的關鍵字
HELP_KEYWORDS = {"說明", "使用說明", "幫助", "help", "?", "？"}
MODULE_DISABLED_MESSAGE = "這個功能暫停使用中，請稍後再試。"
# Rich menu 的切換按鈕（RichMenuSwitchAction）也會送 postback；切換由 LINE 完成，不需要回覆
RICH_MENU_SWITCH_DATA = {"menu-main", "menu-more"}


async def resolve_user(event, state) -> str | None:
    """
    1 對 1 聊天的 LINE userId（已由 webhook 簽章驗證）→ 綁定它的 NTHUSA ID；沒有綁定時為 None。

    只查詢、不建立：聊天室不需要登入，不為沒有綁定的人（或群組裡的發言者）建立任何資料。
    綁定的帳號被停用時拋出 AccountDisabledError（事件不處理）。
    """
    source = event.source
    if getattr(source, "type", None) != "user" or not getattr(source, "user_id", None):
        return None
    return await state.identity_service.find_line_user(source.user_id)


def count(state, writes: BackgroundWrites, event: str) -> None:
    """聊天室的匿名統計（只有每天的總數，見 UsageStats）。"""
    writes.spawn(state.usage_stats.record(event), "usage_stats")


@router.post("/callback")
async def handle_callback(request: Request):
    """
    Webhook 回呼函式，接收 LINE Server 發送的訊息。

    驗證簽名並等待事件處理完成，避免 Cloud Run 回應後暫停 CPU。
    """
    signature = request.headers.get("X-Line-Signature")
    if not signature:
        raise HTTPException(status_code=400, detail="Missing signature")
    body = await request.body()
    decoded_body = body.decode()

    try:
        events = request.app.state.parser.parse(decoded_body, signature)
    except InvalidSignatureError:
        logger.error("無效的簽名。請檢查 Channel Access Token 和 Channel Secret。")
        raise HTTPException(status_code=400, detail="Invalid signature") from None

    state = request.app.state
    writes = BackgroundWrites()
    for event in events:
        try:
            user_id = await resolve_user(event, state)
            if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
                await handle_message(event, state, user_id, writes)
            elif isinstance(event, PostbackEvent):
                await handle_postback(event, state, user_id, writes)
            elif isinstance(event, FollowEvent):
                await handle_follow(event, state, writes)
            elif isinstance(event, UnfollowEvent):
                count(state, writes, UNFOLLOW)
        except AccountDisabledError:
            # 被停用的帳號：不回覆、不處理
            logger.warning("Webhook event from disabled account ignored")
        except (
            GoogleAPICallError,
            ApiException,
            ClientError,
            TimeoutError,
            ValueError,
            RuntimeError,
        ) as error:
            logger.error("Webhook event failed: {}", type(error).__name__)
            if getattr(event, "reply_token", None):
                try:
                    await state.messaging_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text="處理訊息時發生錯誤，請稍後再試。")],
                        )
                    )
                except (ApiException, ClientError, TimeoutError) as reply_error:
                    logger.error("Error reply failed: {}", type(reply_error).__name__)

    await writes.drain()
    return "OK"


async def run_command(text: str, user_id: str | None, state, writes: BackgroundWrites):
    """執行 `@` 指令；模組在 Firestore 被停用時回固定訊息，並非同步累計模組的使用總數。"""
    module = command_handler.parse_command(text)[0]
    if module and not await state.module_registry.is_enabled(module):
        return MODULE_DISABLED_MESSAGE
    if module:
        writes.spawn(state.usage_stats.record_module(module), "usage_stats")
    return await command_handler.process_message(text, user_id)


async def handle_message(event: MessageEvent, state, user_id, writes):
    """
    處理文字訊息事件。

    `@` 開頭走聊天室內的指令模組；其餘文字回一則按鈕，讓使用者到 LIFF 網頁與 AI 對話。
    泡泡以 webhook event id 綁定對話：同一顆按鈕永遠開同一個對話，被刪除才重新建立。
    群組內不把訊息內容帶進 LIFF 網址。
    """
    message_text = event.message.text
    reply_token = event.reply_token
    liff_id = state.settings.liff_id

    count(state, writes, MESSAGE)

    if message_text.strip().lower() in HELP_KEYWORDS:
        message_text = f"{command_handler.command_prefix}說明"

    if message_text.startswith(command_handler.command_prefix):
        if event.source.type == "user":
            try:
                # loading 動畫要用 LINE 的 chat id（1 對 1 時就是對方的 LINE userId）
                await state.messaging_api.show_loading_animation(
                    ShowLoadingAnimationRequest(chatId=event.source.user_id)
                )
            except ApiException, ClientError, TimeoutError:
                logger.warning("Loading animation unavailable")
        messages = await run_command(message_text, user_id, state, writes)
    elif event.source.type == "user":
        messages = [
            open_web_chat(
                liff_id,
                question=message_text.strip() or None,
                session_key=event.webhook_event_id,
            )
        ]
    else:
        messages = [open_web_chat(liff_id)]

    if isinstance(messages, str):
        messages = [TextMessage(text=messages)]

    if messages:
        reply_message = ReplyMessageRequest(
            reply_token=reply_token,
            messages=messages,
            notificationDisabled=False,
        )
        try:
            await state.messaging_api.reply_message(reply_message)
        except (ApiException, ClientError, TimeoutError) as error:
            logger.error("Reply failed: {}", type(error).__name__)
    else:
        logger.warning("沒有產生任何回覆訊息。")


async def handle_postback(event: PostbackEvent, state, user_id, writes):
    """
    處理 Postback 事件。

    根據 postback data 判斷是否為命令，並產生相應回覆。
    """
    count(state, writes, POSTBACK)
    postback_data = event.postback.data
    reply_token = event.reply_token
    if postback_data in RICH_MENU_SWITCH_DATA:
        return

    messages = None

    if postback_data.startswith(command_handler.command_prefix):
        messages = await run_command(postback_data, user_id, state, writes)
    if isinstance(messages, str):
        messages = [TextMessage(text=messages)]

    if messages:
        reply_message = ReplyMessageRequest(
            reply_token=reply_token,
            messages=messages,
            notificationDisabled=False,
        )
        try:
            await state.messaging_api.reply_message(reply_message)
        except (ApiException, ClientError, TimeoutError) as error:
            logger.error("Postback reply failed: {}", type(error).__name__)
    else:
        logger.warning("Postback 事件沒有產生任何回覆訊息。")


PROFILE_TIMEOUT_SECONDS = 2


async def _display_name(state, event: FollowEvent) -> str | None:
    """拿使用者的顯示名稱，只用來稱呼，不存。

    要用 LINE 的 userId —— `resolve_user` 換出來的是內部 id，LINE 不認得。
    查不到或太慢就算了，歡迎訊息不該因此發不出去。
    """
    line_user_id = getattr(getattr(event, "source", None), "user_id", None)
    if not line_user_id:
        return None
    try:
        async with asyncio.timeout(PROFILE_TIMEOUT_SECONDS):
            profile = await state.messaging_api.get_profile(line_user_id)
    except (ApiException, ClientError, TimeoutError) as error:
        logger.warning(
            "Could not read the profile for the welcome message: {}", type(error).__name__
        )
        return None
    return getattr(profile, "display_name", None) or None


async def handle_follow(event: FollowEvent, state, writes):
    """
    處理追蹤事件（加入好友事件）。

    送新手任務的第一則：問候加三個任務。進度不存，編在按鈕裡。
    """
    count(state, writes, FOLLOW)
    display_name = await _display_name(state, event)
    reply_message = ReplyMessageRequest(
        reply_token=event.reply_token,
        messages=[
            TextMessage(
                text=welcome_text(display_name),
                quick_reply=QuickReply.from_dict(welcome_quick_reply()),
            )
        ],
        notificationDisabled=False,
    )
    await state.messaging_api.reply_message(reply_message)
