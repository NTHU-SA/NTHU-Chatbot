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


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.state.parser = WebhookParser("test-secret")
        self.app.state.messaging_api = AsyncMock()
        self.app.state.users = AsyncMock()
        self.app.state.chat = AsyncMock()
        self.app.state.chat.respond.return_value = "Answer"
        self.client = TestClient(self.app)

    def post(self, events):
        body = json.dumps({"destination": "bot", "events": events})
        signature = base64.b64encode(
            hmac.new(b"test-secret", body.encode(), hashlib.sha256).digest()
        ).decode()
        return self.client.post(
            "/callback", content=body, headers={"X-Line-Signature": signature}
        )

    def event(self, source=None, event_id="event-1"):
        return {
            "type": "message",
            "timestamp": 1,
            "mode": "active",
            "webhookEventId": event_id,
            "deliveryContext": {"isRedelivery": False},
            "replyToken": "reply-" + event_id,
            "source": source or {"type": "user", "userId": "user-1"},
            "message": {
                "type": "text",
                "id": "message-1",
                "text": "Question",
                "quoteToken": "quote-1",
            },
        }

    def test_signature_required_and_validated_before_processing(self):
        self.assertEqual(self.client.post("/callback", json={}).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/callback", json={}, headers={"X-Line-Signature": "invalid"}
            ).status_code,
            400,
        )
        self.app.state.users.touch.assert_not_awaited()

    def test_signed_message_uses_persistent_chat(self):
        self.assertEqual(self.post([self.event()]).status_code, 200)
        self.app.state.chat.respond.assert_awaited_once_with(
            "user-1", "Question", "event-1"
        )
        self.app.state.users.touch.assert_awaited_once_with("user-1")
        self.app.state.messaging_api.reply_message.assert_awaited_once()

    def test_group_does_not_access_private_history(self):
        self.post(
            [self.event({"type": "group", "groupId": "group-1", "userId": "user-1"})]
        )
        self.app.state.chat.respond.assert_not_awaited()
        self.app.state.messaging_api.show_loading_animation.assert_not_awaited()

    def test_failure_is_sanitized_and_next_event_still_runs(self):
        self.app.state.chat.respond.side_effect = [
            RuntimeError("secret-value"),
            "Answer",
        ]
        self.assertEqual(
            self.post([self.event(), self.event(event_id="event-2")]).status_code, 200
        )
        self.assertEqual(self.app.state.messaging_api.reply_message.await_count, 2)
        first = self.app.state.messaging_api.reply_message.call_args_list[0].args[0]
        self.assertEqual(first.reply_token, "reply-event-1")
        self.assertNotIn("secret-value", first.messages[0].text)

    def test_loading_failure_does_not_block_answer(self):
        self.app.state.messaging_api.show_loading_animation.side_effect = ApiException(
            status=503
        )
        self.post([self.event()])
        self.app.state.chat.respond.assert_awaited_once()


class AppLifecycleTests(unittest.TestCase):
    def test_startup_wires_clients_and_shutdown_closes_them(self):
        environment = {
            "OPENAI_API_KEY": "test-key",
            "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
            "LINE_CHANNEL_SECRET": "test-secret",
            "GOOGLE_CLOUD_PROJECT": "demo-nthu-chatbot",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("src.app.firestore.AsyncClient") as database,
        ):
            app = create_app()
            with TestClient(app) as client:
                self.assertEqual(client.get("/ping").json(), {"message": "pong"})
                self.assertIs(app.state.chat.users, app.state.users)
                database.assert_called_once_with(project="demo-nthu-chatbot")
                ai_client = app.state.chat.agent.client
                self.assertFalse(ai_client.is_closed())
            self.assertTrue(ai_client.is_closed())
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
            patch.object(
                rich_menu,
                "rich_menu_list",
                [{"richmenu_image": Path(directory) / "missing.png"}],
            ),
        ):
            with self.assertRaises(FileNotFoundError):
                rich_menu.set_rich_menu()
            api_client.assert_not_called()
