import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from linebot.v3.messaging import ApiException
from linebot.v3.webhook import WebhookParser

from src.app import create_app
from src.application.services.chat_store import MemoryChatStore
from tests.fakes import TEST_LIFF_ID, make_settings

LIFF_BASE = f"https://liff.line.me/{TEST_LIFF_ID}"
PROCESS_MESSAGE = "src.app.routes.callback.command_handler.process_message"


@pytest.fixture
def webhook_app():
    app = create_app()
    app.state.settings = make_settings()
    app.state.parser = WebhookParser("test-secret")
    app.state.messaging_api = AsyncMock()
    app.state.store = AsyncMock()
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
        return client.post(
            "/callback", content=body, headers={"X-Line-Signature": signature}
        )

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


def test_signature_required_and_validated_before_processing(post, webhook_app):
    assert post.client.post("/callback", json={}).status_code == 400
    assert (
        post.client.post(
            "/callback", json={}, headers={"X-Line-Signature": "invalid"}
        ).status_code
        == 400
    )
    webhook_app.state.store.touch_user.assert_not_awaited()


def test_free_text_replies_liff_button_with_question(post, webhook_app):
    assert post([event(text="機器學習的課")]).status_code == 200
    webhook_app.state.store.touch_user.assert_awaited_once_with("user-1")
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


def test_command_uses_command_handler_and_loading_animation(post, webhook_app):
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="Command answer")) as process:
        assert post([event(text="@公車")]).status_code == 200
    process.assert_awaited_once_with("@公車", "user-1")
    webhook_app.state.messaging_api.show_loading_animation.assert_awaited_once()
    (message,) = replied_messages(webhook_app)
    assert message.text == "Command answer"


@pytest.mark.parametrize("text", ["說明", "help", "？"])
def test_help_keyword_routes_to_help_command(post, text):
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="help")) as process:
        post([event(text=text)])
    process.assert_awaited_once_with("@說明", "user-1")


def test_help_command_renders_usage_bubble(post, webhook_app):
    with patch.dict(os.environ, {"LIFF_ID": TEST_LIFF_ID}):
        post([event(text="@說明")])
    (message,) = replied_messages(webhook_app)
    assert message.type == "flex"
    assert message.alt_text == "狗狗情報員使用說明"
    assert message.contents.footer.contents[0].action.uri == LIFF_BASE
    body_text = str(message.contents.body.contents)
    assert "@公車" in body_text
    assert "@開發者" not in body_text
    assert "@說明" not in body_text


def test_follow_sends_welcome_and_liff_button(post, webhook_app):
    assert post([event(kind="follow")]).status_code == 200
    webhook_app.state.store.touch_user.assert_awaited_once_with("user-1", followed=True)
    messages = replied_messages(webhook_app)
    assert [m.type for m in messages] == ["text", "flex"]
    assert "狗狗情報員" in messages[0].text
    assert messages[1].contents.footer.contents[0].action.uri == LIFF_BASE


def test_unfollow_marks_user(post, webhook_app):
    unfollow = event(kind="unfollow")
    unfollow.pop("replyToken")
    assert post([unfollow]).status_code == 200
    webhook_app.state.store.touch_user.assert_awaited_once_with("user-1", followed=False)


def test_failure_is_sanitized_and_next_event_still_runs(post, webhook_app):
    webhook_app.state.store.touch_user.side_effect = [RuntimeError("secret-value"), None]
    assert post([event(), event(event_id="event-2")]).status_code == 200
    assert webhook_app.state.messaging_api.reply_message.await_count == 2
    first = webhook_app.state.messaging_api.reply_message.call_args_list[0].args[0]
    assert first.reply_token == "reply-event-1"
    assert "secret-value" not in first.messages[0].text
    assert replied_messages(webhook_app, 1)[0].type == "flex"


def test_loading_failure_does_not_block_command(post, webhook_app):
    webhook_app.state.messaging_api.show_loading_animation.side_effect = ApiException(
        status=503
    )
    with patch(PROCESS_MESSAGE, new=AsyncMock(return_value="Command answer")):
        post([event(text="@公車")])
    webhook_app.state.messaging_api.reply_message.assert_awaited_once()


# -- app lifecycle --
LIFECYCLE_ENVIRONMENT = {
    "OPENAI_API_KEY": "test-key",
    "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
    "LINE_CHANNEL_SECRET": "test-secret",
    "LINE_LOGIN_CHANNEL_ID": "1234567890",
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
            assert client.get("/api/config").json() == {"liff_id": TEST_LIFF_ID}
            assert isinstance(app.state.store, MemoryChatStore)
            assert app.state.agent_runner is runner
            runner.start.assert_awaited_once()
            runner.stop.assert_not_awaited()
        runner.stop.assert_awaited_once()
    database.assert_not_called()


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
            database.assert_called_once_with(project="demo-nthu-chatbot")
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
