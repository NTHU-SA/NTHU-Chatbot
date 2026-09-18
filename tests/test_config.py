import os
import unittest
from unittest.mock import patch

from src.core.config import DEFAULT_MCP_TOOLS, Settings

BASE = {
    "LINE_CHANNEL_SECRET": "test-secret",
    "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
    "LINE_LOGIN_CHANNEL_ID": "1234567890",
    "LIFF_ID": "1234567890-abcdefgh",
    "OPENAI_API_KEY": "test-key",
}


class SettingsTests(unittest.TestCase):
    def test_missing_variables_are_listed_by_name_only(self):
        with (
            patch.dict(os.environ, {"CHAT_STORE": "memory"}, clear=True),
            self.assertRaises(RuntimeError) as context,
        ):
            Settings.from_env()
        message = str(context.exception)
        self.assertIn("LINE_CHANNEL_SECRET", message)
        self.assertIn("OPENAI_API_KEY", message)
        self.assertNotIn("GOOGLE_CLOUD_PROJECT", message)

    def test_firestore_store_requires_project(self):
        with (
            patch.dict(os.environ, BASE, clear=True),
            self.assertRaisesRegex(RuntimeError, "GOOGLE_CLOUD_PROJECT"),
        ):
            Settings.from_env()

    def test_memory_store_defaults(self):
        with patch.dict(os.environ, {**BASE, "CHAT_STORE": "memory"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.chat_store, "memory")
        self.assertIsNone(settings.google_cloud_project)
        self.assertEqual(settings.openai_model, "gpt-4.1-mini")
        self.assertFalse(settings.openai_use_responses_api)
        self.assertEqual(settings.mcp_allowed_tools, DEFAULT_MCP_TOOLS)
        self.assertEqual(settings.daily_message_limit, 100)
        self.assertEqual(settings.liff_url, "https://liff.line.me/1234567890-abcdefgh")

    def test_parsing_of_optional_values(self):
        environment = {
            **BASE,
            "CHAT_STORE": "Firestore",
            "GOOGLE_CLOUD_PROJECT": "demo",
            "OPENAI_USE_RESPONSES_API": "True",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "MCP_ALLOWED_TOOLS": " get_next_buses, search_campus ,",
            "DAILY_MESSAGE_LIMIT": "5",
            "MCP_TIMEOUT_SECONDS": "12.5",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.chat_store, "firestore")
        self.assertTrue(settings.openai_use_responses_api)
        self.assertEqual(settings.openai_base_url, "https://example.test/v1")
        self.assertEqual(settings.mcp_allowed_tools, ("get_next_buses", "search_campus"))
        self.assertEqual(settings.daily_message_limit, 5)
        self.assertEqual(settings.mcp_timeout_seconds, 12.5)

    def test_invalid_values_fail_fast(self):
        with (
            patch.dict(os.environ, {**BASE, "CHAT_STORE": "redis"}, clear=True),
            self.assertRaisesRegex(RuntimeError, "CHAT_STORE"),
        ):
            Settings.from_env()
        with (
            patch.dict(
                os.environ,
                {**BASE, "CHAT_STORE": "memory", "HISTORY_WINDOW": "ten"},
                clear=True,
            ),
            self.assertRaisesRegex(RuntimeError, "HISTORY_WINDOW"),
        ):
            Settings.from_env()

    def test_secrets_are_not_in_repr(self):
        with patch.dict(os.environ, {**BASE, "CHAT_STORE": "memory"}, clear=True):
            settings = Settings.from_env()
        text = repr(settings)
        self.assertNotIn("test-secret", text)
        self.assertNotIn("test-token", text)
        self.assertNotIn("test-key", text)
