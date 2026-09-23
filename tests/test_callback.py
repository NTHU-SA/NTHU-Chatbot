import base64
import hashlib
import hmac
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from linebot.v3.messaging import ApiException
from linebot.v3.webhook import WebhookParser

from src.app import create_app
from src.application.services.chat_store import MemoryChatStore
from tests.fakes import TEST_LIFF_ID, make_settings

LIFF_BASE = f"https://liff.line.me/{TEST_LIFF_ID}"


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.state.settings = make_settings()
        self.app.state.parser = WebhookParser("test-secret")
        self.app.state.messaging_api = AsyncMock()
        self.app.state.store = AsyncMock()
        self.client = TestClient(self.app)

    def post(self, events):
        body = json.dumps({"destination": "bot", "events": events})
        signature = base64.b64encode(
            hmac.new(b"test-secret", body.encode(), hashlib.sha256).digest()
        ).decode()
        return self.client.post(
            "/callback", content=body, headers={"X-Line-Signature": signature}
        )

    def event(self, source=None, event_id="event-1", text="Question", kind="message"):
        event = {
            "type": kind,
            "timestamp": 1,
            "mode": "active",
            "webhookEventId": event_id,
            "deliveryContext": {"isRedelivery": False},
            "replyToken": "reply-" + event_id,
            "source": source or {"type": "user", "userId": "user-1"},
        }
        if kind == "message":
            event["message"] = {
                "type": "text",
                "id": "message-1",
                "text": text,
                "quoteToken": "quote-1",
            }
        elif kind == "follow":
            event["follow"] = {"isUnblocked": False}
        return event

    def replied_messages(self, index=0):
        call = self.app.state.messaging_api.reply_message.call_args_list[index]
        return call.args[0].messages

    def test_signature_required_and_validated_before_processing(self):
        self.assertEqual(self.client.post("/callback", json={}).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/callback", json={}, headers={"X-Line-Signature": "invalid"}
            ).status_code,
            400,
        )
        self.app.state.store.touch_user.assert_not_awaited()

    def test_free_text_replies_liff_button_with_question(self):
        self.assertEqual(self.post([self.event(text="機器學習的課")]).status_code, 200)
        self.app.state.store.touch_user.assert_awaited_once_with("user-1")
        (message,) = self.replied_messages()
        self.assertEqual(message.type, "flex")
        uri = message.contents.footer.contents[0].action.uri
        # 泡泡以 webhook event id 綁定對話，並帶入 URL 編碼後的問題
        self.assertTrue(uri.startswith(LIFF_BASE + "?s=event-1&q="))
        self.assertIn("%E6%A9%9F%E5%99%A8", uri)  # url-encoded 機器
        self.app.state.messaging_api.show_loading_animation.assert_not_awaited()

    def test_group_text_gets_liff_button_without_question(self):
        self.post(
            [self.event({"type": "group", "groupId": "group-1", "userId": "user-1"})]
        )
        (message,) = self.replied_messages()
        self.assertEqual(message.type, "flex")
        self.assertEqual(message.contents.footer.contents[0].action.uri, LIFF_BASE)
        self.app.state.messaging_api.show_loading_animation.assert_not_awaited()

    def test_command_uses_command_handler_and_loading_animation(self):
        with patch(
            "src.app.routes.callback.command_handler.process_message",
            new=AsyncMock(return_value="Command answer"),
        ) as process:
            self.assertEqual(self.post([self.event(text="@公車")]).status_code, 200)
        process.assert_awaited_once_with("@公車", "user-1")
        self.app.state.messaging_api.show_loading_animation.assert_awaited_once()
        (message,) = self.replied_messages()
        self.assertEqual(message.text, "Command answer")

    def test_help_keyword_routes_to_help_command(self):
        for text in ("說明", "help", "？"):
            self.app.state.messaging_api.reply_message.reset_mock()
            with patch(
                "src.app.routes.callback.command_handler.process_message",
                new=AsyncMock(return_value="help"),
            ) as process:
                self.post([self.event(text=text)])
            process.assert_awaited_once_with("@說明", "user-1")

    def test_help_command_renders_usage_bubble(self):
        with patch.dict(os.environ, {"LIFF_ID": TEST_LIFF_ID}):
            self.post([self.event(text="@說明")])
        (message,) = self.replied_messages()
        self.assertEqual(message.type, "flex")
        self.assertEqual(message.alt_text, "狗狗情報員使用說明")
        self.assertEqual(message.contents.footer.contents[0].action.uri, LIFF_BASE)
        body_text = str(message.contents.body.contents)
        self.assertIn("@公車", body_text)
        self.assertNotIn("@開發者", body_text)
        self.assertNotIn("@說明", body_text)

    def test_follow_sends_welcome_and_liff_button(self):
        self.assertEqual(self.post([self.event(kind="follow")]).status_code, 200)
        self.app.state.store.touch_user.assert_awaited_once_with(
            "user-1", followed=True
        )
        messages = self.replied_messages()
        self.assertEqual([m.type for m in messages], ["text", "flex"])
        self.assertIn("狗狗情報員", messages[0].text)
        self.assertEqual(messages[1].contents.footer.contents[0].action.uri, LIFF_BASE)

    def test_unfollow_marks_user(self):
        event = self.event(kind="unfollow")
        event.pop("replyToken")
        self.assertEqual(self.post([event]).status_code, 200)
        self.app.state.store.touch_user.assert_awaited_once_with(
            "user-1", followed=False
        )

    def test_failure_is_sanitized_and_next_event_still_runs(self):
        self.app.state.store.touch_user.side_effect = [
            RuntimeError("secret-value"),
            None,
        ]
        self.assertEqual(
            self.post([self.event(), self.event(event_id="event-2")]).status_code, 200
        )
        self.assertEqual(self.app.state.messaging_api.reply_message.await_count, 2)
        first = self.app.state.messaging_api.reply_message.call_args_list[0].args[0]
        self.assertEqual(first.reply_token, "reply-event-1")
        self.assertNotIn("secret-value", first.messages[0].text)
        self.assertEqual(self.replied_messages(1)[0].type, "flex")

    def test_loading_failure_does_not_block_command(self):
        self.app.state.messaging_api.show_loading_animation.side_effect = ApiException(
            status=503
        )
        with patch(
            "src.app.routes.callback.command_handler.process_message",
            new=AsyncMock(return_value="Command answer"),
        ):
            self.post([self.event(text="@公車")])
        self.app.state.messaging_api.reply_message.assert_awaited_once()


LIFECYCLE_ENVIRONMENT = {
    "OPENAI_API_KEY": "test-key",
    "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
    "LINE_CHANNEL_SECRET": "test-secret",
    "LINE_LOGIN_CHANNEL_ID": "1234567890",
    "LIFF_ID": TEST_LIFF_ID,
}


class AppLifecycleTests(unittest.TestCase):
    def test_memory_store_startup_skips_firestore_and_manages_runner(self):
        environment = {**LIFECYCLE_ENVIRONMENT, "CHAT_STORE": "memory"}
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("src.app.firestore.AsyncClient") as database,
            patch("src.app.AgentRunner") as runner_cls,
        ):
            runner = runner_cls.return_value
            runner.start = AsyncMock()
            runner.stop = AsyncMock()
            app = create_app()
            with TestClient(app) as client:
                self.assertEqual(client.get("/ping").json(), {"message": "pong"})
                self.assertEqual(
                    client.get("/api/config").json(), {"liff_id": TEST_LIFF_ID}
                )
                self.assertIsInstance(app.state.store, MemoryChatStore)
                self.assertIs(app.state.agent_runner, runner)
                runner.start.assert_awaited_once()
                runner.stop.assert_not_awaited()
            runner.stop.assert_awaited_once()
            database.assert_not_called()

    def test_firestore_store_startup_wires_and_closes_client(self):
        environment = {
            **LIFECYCLE_ENVIRONMENT,
            "CHAT_STORE": "firestore",
            "GOOGLE_CLOUD_PROJECT": "demo-nthu-chatbot",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("src.app.firestore.AsyncClient") as database,
            patch("src.app.AgentRunner") as runner_cls,
        ):
            runner_cls.return_value.start = AsyncMock()
            runner_cls.return_value.stop = AsyncMock()
            app = create_app()
            with TestClient(app):
                database.assert_called_once_with(project="demo-nthu-chatbot")
                self.assertIs(app.state.store._db, database.return_value)
            database.return_value.close.assert_called_once()

    def test_missing_configuration_fails_startup(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(RuntimeError, "Missing environment variables"),
            TestClient(create_app()),
        ):
            pass

    def test_rich_menu_missing_assets_does_not_call_line(self):
        from scripts import rich_menu

        with (
            TemporaryDirectory() as directory,
            patch.object(rich_menu, "ApiClient") as api_client,
            patch.object(rich_menu.configuration, "access_token", "test-token"),
            patch.object(rich_menu, "LIFF_ID", TEST_LIFF_ID),
            patch.object(
                rich_menu,
                "rich_menu_list",
                [{"richmenu_image": Path(directory) / "missing.png"}],
            ),
        ):
            with self.assertRaises(FileNotFoundError):
                rich_menu.set_rich_menu()
            api_client.assert_not_called()
