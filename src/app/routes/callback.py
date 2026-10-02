from aiohttp import ClientError
from fastapi import APIRouter, HTTPException, Request
from google.api_core.exceptions import GoogleAPICallError
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiException,
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
from templates.messages import open_web_chat

router = APIRouter()

# 不用加 @ 也能叫出使用說明的關鍵字
HELP_KEYWORDS = {"說明", "使用說明", "幫助", "help", "?", "？"}
MODULE_DISABLED_MESSAGE = "這個功能暫停使用中，請稍後再試。"


async def resolve_user(event, state) -> str | None:
    """
    1 對 1 聊天的 LINE userId（已由 webhook 簽章驗證）→ 內部 user id。

    群組與多人聊天室裡的發言者沒有和 bot 建立關係，不為他們建立任何資料。
    """
    source = event.source
    if getattr(source, "type", None) != "user" or not getattr(source, "user_id", None):
        return None
    return await state.identity_service.resolve_line_user(source.user_id)


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
            if isinstance(event, MessageEvent) and isinstance(
                event.message, TextMessageContent
            ):
                await handle_message(event, state, user_id, writes)
            elif isinstance(event, PostbackEvent):
                await handle_postback(event, state, user_id, writes)
            elif isinstance(event, FollowEvent):
                await handle_follow(event, state, user_id, writes)
            elif isinstance(event, UnfollowEvent) and user_id:
                await state.user_store.update_identity_metadata(
                    user_id, "line", {"followed": False}
                )
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
                            messages=[
                                TextMessage(text="處理訊息時發生錯誤，請稍後再試。")
                            ],
                        )
                    )
                except (ApiException, ClientError, TimeoutError) as reply_error:
                    logger.error("Error reply failed: {}", type(reply_error).__name__)

    await writes.drain()
    return "OK"


async def run_command(text: str, user_id: str | None, state, writes: BackgroundWrites):
    """執行 `@` 指令；模組在 Firestore 被停用時回固定訊息，並非同步記錄模組使用次數。"""
    module = command_handler.parse_command(text)[0]
    if module and not await state.module_registry.is_enabled(module):
        return MODULE_DISABLED_MESSAGE
    if module and user_id:
        writes.spawn(state.user_store.record_module_use(user_id, module), "record_module_use")
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

    if user_id:
        writes.spawn(state.user_store.touch_activity(user_id), "touch_activity")

    if message_text.strip().lower() in HELP_KEYWORDS:
        message_text = f"{command_handler.command_prefix}說明"

    if message_text.startswith(command_handler.command_prefix):
        if user_id:
            try:
                # loading 動畫要用 LINE 的 chat id（1 對 1 時就是對方的 LINE userId）
                await state.messaging_api.show_loading_animation(
                    ShowLoadingAnimationRequest(chatId=event.source.user_id)
                )
            except (ApiException, ClientError, TimeoutError):
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
    if user_id:
        writes.spawn(state.user_store.touch_activity(user_id), "touch_activity")
    postback_data = event.postback.data
    reply_token = event.reply_token

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


async def handle_follow(event: FollowEvent, state, user_id, writes):
    """
    處理追蹤事件（加入好友事件）。

    當使用者加入好友時，發送歡迎訊息。
    """
    if user_id:
        writes.spawn(
            state.user_store.update_identity_metadata(user_id, "line", {"followed": True}),
            "followed",
        )

    reply_token = event.reply_token
    welcome_text = """初次見面！我是清華校園情報員，你可以叫我狗狗情報員！清華生活中的大小事，只要是你遇到的問題，我都會努力幫你解決唷！
你可以點擊下方的選單~~ฅ'ω'ฅ
🚩索取校巴時刻表
🚩詢問校務相關問題💬
🚩或讓本汪帶你在清大趴趴走！

偷偷告訴你，你可以用左下角的鍵盤直接問我問題，我會給你一顆按鈕，點開就能和我聊天！
隨時輸入「說明」可以查看使用方式。

或是你感到無聊，想找找小遊戲，可以到下方選單點擊〝神奇海螺〞看看額外的功能唷！

作為一隻狗狗，有時候會太熱情，如果覺得我有點吵的話，請將「提醒」功能關閉就好📵，千萬不要封鎖本汪好嗎，我一定會乖乖的喔！"""
    messages = [
        TextMessage(text=welcome_text),
        open_web_chat(state.settings.liff_id, greeting=True),
    ]

    reply_message = ReplyMessageRequest(
        reply_token=reply_token,
        messages=messages,
        notificationDisabled=False,
    )
    await state.messaging_api.reply_message(reply_message)
