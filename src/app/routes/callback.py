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
from openai import OpenAIError

from log import logger
from src.app.handlers.command_handler import command_handler
from src.infrastructure.firebase.user_repository import ConversationBusyError

router = APIRouter()


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
        raise HTTPException(status_code=400, detail="Invalid signature")

    state = request.app.state
    for event in events:
        try:
            if isinstance(event, MessageEvent) and isinstance(
                event.message, TextMessageContent
            ):
                await handle_message(event, state)
            elif isinstance(event, PostbackEvent):
                await handle_postback(event, state)
            elif isinstance(event, FollowEvent):
                await handle_follow(event, state)
            elif isinstance(event, UnfollowEvent) and event.source.user_id:
                await state.users.touch(event.source.user_id, followed=False)
        except (
            GoogleAPICallError,
            OpenAIError,
            ApiException,
            ClientError,
            TimeoutError,
            ValueError,
            RuntimeError,
            ConversationBusyError,
        ) as error:
            logger.error("Webhook event failed: {}", type(error).__name__)
            if getattr(event, "reply_token", None):
                message = (
                    "上一則訊息仍在處理中，請稍後再試。"
                    if isinstance(error, ConversationBusyError)
                    else "處理訊息時發生錯誤，請稍後再試。"
                )
                try:
                    await state.messaging_api.reply_message(
                        ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=message)],
                        )
                    )
                except (ApiException, ClientError, TimeoutError) as reply_error:
                    logger.error("Error reply failed: {}", type(reply_error).__name__)

    return "OK"


async def handle_message(event: MessageEvent, state):
    """
    處理文字訊息事件。

    根據使用者發送的文字內容，決定使用命令處理或是由 AI 產生回覆。
    """
    user_id = event.source.user_id
    message_text = event.message.text
    reply_token = event.reply_token

    if user_id:
        await state.users.touch(user_id)
    if event.source.type == "user" and user_id:
        try:
            await state.messaging_api.show_loading_animation(
                ShowLoadingAnimationRequest(chatId=user_id)
            )
        except (ApiException, ClientError, TimeoutError):
            logger.warning("Loading animation unavailable")

    if message_text.startswith(command_handler.command_prefix):
        messages = await command_handler.process_message(message_text, user_id)
    elif event.source.type == "user" and user_id:
        response = await state.chat.respond(
            user_id, message_text, event.webhook_event_id
        )
        messages = [TextMessage(text=response)]
    else:
        messages = [TextMessage(text="請在一對一聊天中使用 AI 問答。")]

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


async def handle_postback(event: PostbackEvent, state):
    """
    處理 Postback 事件。

    根據 postback data 判斷是否為命令，並產生相應回覆。
    """
    user_id = event.source.user_id
    if user_id:
        await state.users.touch(user_id)
    postback_data = event.postback.data
    reply_token = event.reply_token

    messages = None

    if postback_data.startswith(command_handler.command_prefix):
        messages = await command_handler.process_message(postback_data, user_id)
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


async def handle_follow(event: FollowEvent, state):
    """
    處理追蹤事件（加入好友事件）。

    當使用者加入好友時，發送歡迎訊息。
    """
    if event.source.user_id:
        await state.users.touch(event.source.user_id, followed=True)

    reply_token = event.reply_token
    welcome_text = """初次見面！我是清華校園情報員，你可以叫我狗狗情報員！清華生活中的大小事，只要是你遇到的問題，我都會努力幫你解決唷！
你可以點擊下方的選單~~ฅ'ω'ฅ
🚩索取校巴時刻表
🚩詢問校務相關問題💬
🚩或讓本汪帶你在清大趴趴走！

偷偷告訴你，你可以用左下角的鍵盤和我說悄悄話哦！
身為一個好的情報員，有任何消息我都會盡快回報的！！

或是你感到無聊，想找找小遊戲，可以到下方選單點擊〝神奇海螺〞看看額外的功能唷！

作為一隻狗狗，有時候會太熱情，如果覺得我有點吵的話，請將「提醒」功能關閉就好📵，千萬不要封鎖本汪好嗎，我一定會乖乖的喔！"""
    messages = [TextMessage(text=welcome_text)]

    reply_message = ReplyMessageRequest(
        reply_token=reply_token,
        messages=messages,
        notificationDisabled=False,
    )
    await state.messaging_api.reply_message(reply_message)
