import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from linebot.v3.messaging import ApiException
from linebot.v3.webhook import WebhookParser

from src.app import create_app
from src.app.routes.callback import MODULE_DISABLED_MESSAGE
from src.application.models.identity import AccountDisabledError
from src.application.services.chat_store import MemoryChatStore
from src.application.services.module_registry import StaticModuleRegistry
from src.application.services.usage_stats import MemoryUsageStats
from src.application.services.user_store import MemoryUserStore
from tests.fakes import TEST_LIFF_ID, TEST_USR, make_settings

LIFF_BASE = f"https://liff.line.me/{TEST_LIFF_ID}"
PROCESS_MESSAGE = "src.app.routes.callback.command_handler.process_message"


@pytest.fixture
def webhook_app():
    app = create_app()
    app.state.settings = make_settings()
    app.state.parser = WebhookParser("test-secret")
    app.state.messaging_api = AsyncMock()
    app.state.user_store = AsyncMock()
    app.state.identity_service = AsyncMock()
    # 預設：這個 LINE 已綁定 NTHUSA ID
    app.state.identity_service.find_line_user.return_value = TEST_USR
    app.state.module_registry = StaticModuleRegistry()
    app.state.usage_stats = MemoryUsageStats()
    return app


@pytest.fixture
def post(webhook_app):
    """以正確簽章送出 webhook 事件。"""
    client = TestClient(webhook_app)

    def send(events):
        body = json.dumps({"destination": "bot", "events": events})
        signature = base64.b64encode(
            hmac.new(b"test-secret", body.encode(), hashlib.sha256).digest()
        ).decode()
        return client.post("/callback", content=body, headers={"X-Line-Signature": signature})

    send.client = client
    return send


def event(source=None, event_id="event-1", text="Question", kind="message"):
    payload = {
        "type": kind,
        "timestamp": 1,
        "mode": "active",
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": False},
        "replyToken": "reply-" + event_id,
        "source": source or {"type": "user", "userId": "user-1"},
    }
    if kind == "message":
        payload["message"] = {
            "type": "text",
            "id": "message-1",
            "text": text,
            "quoteToken": "quote-1",
        }
    elif kind == "follow":
        payload["follow"] = {"isUnblocked": False}
    return payload


def replied_messages(app, index=0):
    call = app.state.messaging_api.reply_message.call_args_list[index]
    return call.args[0].messages


def counted(app) -> dict[str, int]:
    """今天的匿名統計（欄位 → 次數）。"""
    return {field: count for (_, field), count in app.state.usage_stats.counts.items()}


def assert_nothing_stored_per_user(app):
    """聊天室只累計總數：不寫入任何個人資料（綁定了也一樣）。"""
    assert app.state.user_store.mock_calls == []


def test_signature_required_and_validated_before_processing(post, webhook_app):
    assert post.client.post("/callback", json={}).status_code == 400
    assert (
        post.client.post("/callback", json={}, headers={"X-Line-Signature": "invalid"}).status_code
        == 400
    )
    webhook_app.state.identity_service.find_line_user.assert_not_awaited()


def test_free_text_replies_liff_button_with_question(post, webhook_app):
    assert post([event(text="機器學習的課")]).status_code == 200
    webhook_app.state.identity_service.find_line_user.assert_awaited_once_with("user-1")
    assert_nothing_stored_per_user(webhook_app)
    assert counted(webhook_app) == {"messages": 1}
    (message,) = replied_messages(webhook_app)
    assert message.type == "flex"
    uri = message.contents.footer.contents[0].action.uri
    # 泡泡以 webhook event id 綁定對話，並帶入 URL 編碼後的問題
    assert uri.startswith(LIFF_BASE + "?s=event-1&q=")
    assert "%E6%A9%9F%E5%99%A8" in uri  # url-encoded 機器
    webhook_app.state.messaging_api.show_loading_animation.assert_not_awaited()


def test_group_text_gets_liff_button_without_question(post, webhook_app):
    post([event({"type": "group", "groupId": "group-1", "userId": "user-1"})])
    (message,) = replied_messages(webhook_app)
    assert message.type == "flex"
    assert message.contents.footer.contents[0].action.uri == LIFF_BASE
    webhook_app.state.messaging_api.show_loading_animation.assert_not_awaited()
    # 群組發言者沒有和 bot 建立關係：不查詢、不寫入任何使用者資料
    webhook_app.state.identity_service.find_line_user.assert_not_awaited()
    assert_nothing_stored_per_user(webhook_app)
    assert counted(webhook_app) == {"messages": 1}


def test_command_uses_command_handler_and_loading_animation(post, webhook_app):
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="Command answer")) as process:
        assert post([event(text="@公車")]).status_code == 200
    process.assert_awaited_once_with("@公車", TEST_USR)
    loading = webhook_app.state.messaging_api.show_loading_animation.await_args.args[0]
    assert loading.chat_id == "user-1"
    (message,) = replied_messages(webhook_app)
    assert message.text == "Command answer"
    assert counted(webhook_app) == {"messages": 1, "modules.bus": 1}
    assert_nothing_stored_per_user(webhook_app)


def test_unbound_line_user_gets_the_same_service_and_nothing_is_created(post, webhook_app):
    """聊天室不需要登入：沒綁定 NTHUSA ID 的人一樣能用指令，只是不會有任何個人資料。"""
    webhook_app.state.identity_service.find_line_user.return_value = None
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="Command answer")) as process:
        assert post([event(text="@公車")]).status_code == 200
    process.assert_awaited_once_with("@公車", None)
    webhook_app.state.messaging_api.show_loading_animation.assert_awaited_once()
    assert replied_messages(webhook_app)[0].text == "Command answer"
    assert counted(webhook_app) == {"messages": 1, "modules.bus": 1}
    assert_nothing_stored_per_user(webhook_app)


def test_disabled_module_replies_fixed_message(post, webhook_app):
    webhook_app.state.module_registry = StaticModuleRegistry(disabled={"bus"})
    with patch(PROCESS_MESSAGE, new=AsyncMock()) as process:
        post([event(text="@公車")])
    process.assert_not_awaited()
    (message,) = replied_messages(webhook_app)
    assert message.text == MODULE_DISABLED_MESSAGE
    assert counted(webhook_app) == {"messages": 1}


def test_disabled_account_gets_no_reply(post, webhook_app):
    webhook_app.state.identity_service.find_line_user.side_effect = AccountDisabledError("x")
    assert post([event(text="@公車")]).status_code == 200
    webhook_app.state.messaging_api.reply_message.assert_not_awaited()


@pytest.mark.parametrize("text", ["說明", "help", "？"])
def test_help_keyword_routes_to_help_command(post, text):
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="help")) as process:
        post([event(text=text)])
    process.assert_awaited_once_with("@說明", TEST_USR)


def test_help_command_renders_usage_bubble(post, webhook_app):
    with patch.dict(os.environ, {"LIFF_ID": TEST_LIFF_ID}):
        post([event(text="@說明")])
    (message,) = replied_messages(webhook_app)
    assert message.type == "flex"
    assert message.alt_text == "清華校園情報員使用說明"
    assert message.contents.header.contents[0].wrap is True
    assert message.contents.footer.contents[0].action.uri == LIFF_BASE
    body_text = str(message.contents.body.contents)
    assert "@公車" in body_text
    assert "@開發者" not in body_text
    assert "@說明" not in body_text


def test_follow_sends_the_quest_welcome_and_stores_nothing(post, webhook_app):
    """加好友只送一則文字：問候加三個任務。進度編在按鈕裡，不留個人紀錄。"""
    assert post([event(kind="follow")]).status_code == 200
    assert counted(webhook_app) == {"follows": 1}
    assert_nothing_stored_per_user(webhook_app)
    (message,) = replied_messages(webhook_app)
    assert message.type == "text"
    assert "清華校園情報員" in message.text
    assert "⬜ 任務 1" in message.text
    assert message.quick_reply.items[0].action.data == "@新手/任務 n=bus"


def test_welcome_greets_by_name_using_the_line_user_id(post, webhook_app):
    """要用 LINE userId 查名字 —— 內部 id LINE 不認得，傳錯會永遠查不到。"""
    webhook_app.state.messaging_api.get_profile.return_value = SimpleNamespace(display_name="小明")
    assert post([event(kind="follow")]).status_code == 200
    webhook_app.state.messaging_api.get_profile.assert_awaited_once_with("user-1")
    (message,) = replied_messages(webhook_app)
    assert message.text.startswith("小明，初次見面")


@pytest.mark.parametrize("failure", [ApiException(status=500), TimeoutError()])
def test_welcome_still_goes_out_when_the_profile_cannot_be_read(post, webhook_app, failure):
    webhook_app.state.messaging_api.get_profile.side_effect = failure
    assert post([event(kind="follow")]).status_code == 200
    (message,) = replied_messages(webhook_app)
    assert message.text.startswith("嗨！初次見面")


def test_share_postback_returns_working_friend_and_share_links(post, webhook_app):
    from templates.messages.friend_message import ADD_FRIEND_URL, SHARE_URL

    payload = event(kind="postback")
    payload["postback"] = {"data": "@神奇海螺/分享給好友"}
    assert post([payload]).status_code == 200
    messages = replied_messages(webhook_app)
    assert [message.type for message in messages] == ["text", "flex"]
    actions = messages[1].contents.footer.contents
    assert actions[0].action.uri == ADD_FRIEND_URL
    assert actions[1].action.uri == SHARE_URL


def test_unfollow_is_only_counted(post, webhook_app):
    unfollow = event(kind="unfollow")
    unfollow.pop("replyToken")
    assert post([unfollow]).status_code == 200
    assert counted(webhook_app) == {"unfollows": 1}
    assert_nothing_stored_per_user(webhook_app)


def test_failure_is_sanitized_and_next_event_still_runs(post, webhook_app):
    webhook_app.state.identity_service.find_line_user.side_effect = [
        RuntimeError("secret-value"),
        TEST_USR,
    ]
    assert post([event(), event(event_id="event-2")]).status_code == 200
    assert webhook_app.state.messaging_api.reply_message.await_count == 2
    first = webhook_app.state.messaging_api.reply_message.call_args_list[0].args[0]
    assert first.reply_token == "reply-event-1"
    assert "secret-value" not in first.messages[0].text
    assert replied_messages(webhook_app, 1)[0].type == "flex"


def test_loading_failure_does_not_block_command(post, webhook_app):
    webhook_app.state.messaging_api.show_loading_animation.side_effect = ApiException(status=503)
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="Command answer")):
        post([event(text="@公車")])
    webhook_app.state.messaging_api.reply_message.assert_awaited_once()


# -- app lifecycle --
LIFECYCLE_ENVIRONMENT = {
    "OPENAI_API_KEY": "test-key",
    "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
    "LINE_CHANNEL_SECRET": "test-secret",
    "LIFF_ID": TEST_LIFF_ID,
}


@pytest.fixture
def lifecycle_mocks():
    """替換 Firestore client 與 AgentRunner，避免啟動時連到外部服務。"""
    with (
        patch("src.app.firestore.AsyncClient") as database,
        patch("src.app.AgentRunner") as runner_cls,
    ):
        runner_cls.return_value.start = AsyncMock()
        runner_cls.return_value.stop = AsyncMock()
        yield database, runner_cls.return_value


def test_memory_store_startup_skips_firestore_and_manages_runner(lifecycle_mocks):
    database, runner = lifecycle_mocks
    environment = {**LIFECYCLE_ENVIRONMENT, "CHAT_STORE": "memory"}
    with patch.dict(os.environ, environment, clear=True):
        app = create_app()
        with TestClient(app) as client:
            assert client.get("/ping").json() == {"message": "pong"}
            assert isinstance(app.state.store, MemoryChatStore)
            assert isinstance(app.state.user_store, MemoryUserStore)
            assert app.state.authenticators == {}  # 沒設定 Auth0：/api 一律 401
            assert app.state.agent_runner is runner
            runner.start.assert_awaited_once()
            runner.stop.assert_not_awaited()
        runner.stop.assert_awaited_once()
    database.assert_not_called()


def test_auth0_is_the_only_authenticator(lifecycle_mocks):
    environment = {
        **LIFECYCLE_ENVIRONMENT,
        "CHAT_STORE": "memory",
        "AUTH0_DOMAIN": "auth.example.test",
        "AUTH0_AUDIENCE": "https://chat.example.test/api",
        "AUTH0_CLIENT_ID": "chatClientId123",
    }
    with patch.dict(os.environ, environment, clear=True):
        app = create_app()
        with TestClient(app):
            assert set(app.state.authenticators) == {"auth0"}
            assert isinstance(app.state.usage_stats, MemoryUsageStats)


def test_firestore_store_startup_wires_and_closes_client(lifecycle_mocks):
    database, _ = lifecycle_mocks
    environment = {
        **LIFECYCLE_ENVIRONMENT,
        "CHAT_STORE": "firestore",
        "GOOGLE_CLOUD_PROJECT": "demo-nthu-chatbot",
    }
    with patch.dict(os.environ, environment, clear=True):
        app = create_app()
        with TestClient(app):
            database.assert_called_once_with(project="demo-nthu-chatbot", database="(default)")
            assert app.state.store._db is database.return_value
    database.return_value.close.assert_called_once()


def test_missing_configuration_fails_startup():
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(RuntimeError, match="Missing environment variables"),
        TestClient(create_app()),
    ):
        pass


def test_rich_menu_missing_assets_does_not_call_line(tmp_path):
    from scripts import rich_menu

    with (
        patch.object(rich_menu, "ApiClient") as api_client,
        patch.object(rich_menu.configuration, "access_token", "test-token"),
        patch.object(rich_menu, "LIFF_ID", TEST_LIFF_ID),
        patch.object(
            rich_menu, "rich_menu_list", [{"richmenu_image": Path(tmp_path) / "missing.png"}]
        ),
        pytest.raises(FileNotFoundError),
    ):
        rich_menu.set_rich_menu()
    api_client.assert_not_called()


@pytest.mark.parametrize("data", ["menu-main", "menu-more"])
def test_rich_menu_switch_postbacks_are_ignored_quietly(post, webhook_app, data):
    switch = event(kind="postback")
    switch["postback"] = {"data": data}
    with patch("src.app.routes.callback.logger") as log:
        assert post([switch]).status_code == 200
    webhook_app.state.messaging_api.reply_message.assert_not_awaited()
    log.warning.assert_not_called()
