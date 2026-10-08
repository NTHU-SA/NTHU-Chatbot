import os
from unittest.mock import patch

import pytest

from src.core.config import DEFAULT_MCP_TOOLS, Settings, parse_cors_origins

BASE = {
    "LINE_CHANNEL_SECRET": "test-secret",
    "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
    "LINE_LOGIN_CHANNEL_ID": "1234567890",
    "LIFF_ID": "1234567890-abcdefgh",
    "OPENAI_API_KEY": "test-key",
}
MEMORY = {**BASE, "CHAT_STORE": "memory"}


def load(environment: dict[str, str]) -> Settings:
    with patch.dict(os.environ, environment, clear=True):
        return Settings.from_env()


def test_missing_variables_are_listed_by_name_only():
    with pytest.raises(RuntimeError) as error:
        load({"CHAT_STORE": "memory"})
    message = str(error.value)
    assert "LINE_CHANNEL_SECRET" in message
    assert "OPENAI_API_KEY" in message
    assert "GOOGLE_CLOUD_PROJECT" not in message


def test_firestore_store_requires_project():
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        load(BASE)


def test_memory_store_defaults():
    settings = load(MEMORY)
    assert settings.chat_store == "memory"
    assert settings.google_cloud_project is None
    assert settings.firestore_database == "(default)"
    assert settings.openai_model == "gpt-6-luna"
    assert not settings.openai_use_responses_api
    assert settings.mcp_allowed_tools == DEFAULT_MCP_TOOLS
    assert "get_bus_schedule" in settings.mcp_allowed_tools
    assert "get_next_buses" not in settings.mcp_allowed_tools
    assert "get_bus_stops" not in settings.mcp_allowed_tools
    assert settings.daily_message_limit == 100
    assert settings.max_output_tokens == 2000
    assert settings.max_output_chars == 8000
    assert settings.liff_url == "https://liff.line.me/1234567890-abcdefgh"


def test_parsing_of_optional_values():
    settings = load(
        {
            **BASE,
            "CHAT_STORE": "Firestore",
            "GOOGLE_CLOUD_PROJECT": "demo",
            "OPENAI_USE_RESPONSES_API": "True",
            "OPENAI_BASE_URL": "https://example.test/v1",
            "MCP_ALLOWED_TOOLS": " get_bus_schedule, search_campus ,",
            "DAILY_MESSAGE_LIMIT": "5",
            "MCP_TIMEOUT_SECONDS": "12.5",
            "MAX_OUTPUT_TOKENS": "3000",
            "MAX_OUTPUT_CHARS": "9000",
        }
    )
    assert settings.chat_store == "firestore"
    assert settings.openai_use_responses_api
    assert settings.openai_base_url == "https://example.test/v1"
    assert settings.mcp_allowed_tools == ("get_bus_schedule", "search_campus")
    assert settings.daily_message_limit == 5
    assert settings.mcp_timeout_seconds == 12.5
    assert settings.max_output_tokens == 3000
    assert settings.max_output_chars == 9000


@pytest.mark.parametrize("value", ["prod", "(default)", "stage-01"])
def test_firestore_database_is_configurable(value):
    assert load({**MEMORY, "FIRESTORE_DATABASE": value}).firestore_database == value


@pytest.mark.parametrize("value", ["Prod", "a", "has space", "../x", "-bad", "(other)"])
def test_invalid_firestore_database_fails_fast(value):
    with pytest.raises(RuntimeError, match="FIRESTORE_DATABASE"):
        load({**MEMORY, "FIRESTORE_DATABASE": value})


POSITIVE_LIMITS = (
    "HISTORY_WINDOW",
    "HISTORY_MESSAGE_CHARS",
    "MAX_TOOL_OUTPUT_CHARS",
    "MAX_MESSAGE_CHARS",
    "MAX_AGENT_TURNS",
    "MAX_OUTPUT_TOKENS",
    "MAX_OUTPUT_CHARS",
)


@pytest.mark.parametrize("name", POSITIVE_LIMITS)
@pytest.mark.parametrize("value", ["0", "-1", "1.5"])
def test_positive_integer_limits_reject_invalid(name, value):
    with pytest.raises(RuntimeError, match=name):
        load({**MEMORY, name: value})


@pytest.mark.parametrize("name", POSITIVE_LIMITS)
def test_positive_integer_limits_accept_one(name):
    assert getattr(load({**MEMORY, name: "1"}), name.lower()) == 1


@pytest.mark.parametrize("name", ["DAILY_MESSAGE_LIMIT", "TOOL_RESULT_PREVIEW_CHARS"])
def test_non_negative_limits_allow_zero(name):
    assert getattr(load({**MEMORY, name: "0"}), name.lower()) == 0
    with pytest.raises(RuntimeError, match=name):
        load({**MEMORY, name: "-1"})


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf", "1e999", "invalid"])
def test_timeout_must_be_finite_and_positive(value):
    with pytest.raises(RuntimeError, match="MCP_TIMEOUT_SECONDS"):
        load({**MEMORY, "MCP_TIMEOUT_SECONDS": value})


def test_invalid_values_fail_fast():
    with pytest.raises(RuntimeError, match="CHAT_STORE"):
        load({**BASE, "CHAT_STORE": "redis"})
    with pytest.raises(RuntimeError, match="HISTORY_WINDOW"):
        load({**MEMORY, "HISTORY_WINDOW": "ten"})


def test_secrets_are_not_in_repr():
    text = repr(load(MEMORY))
    assert "test-secret" not in text
    assert "test-token" not in text
    assert "test-key" not in text


def test_cors_origins_are_parsed_and_normalised():
    settings = load(
        {
            **MEMORY,
            "CORS_ALLOWED_ORIGINS": " https://NTHUSA-chatbot.web.app ,https://nthusa-chatbot.firebaseapp.com,",
        }
    )
    assert settings.cors_allowed_origins == (
        "https://nthusa-chatbot.web.app",
        "https://nthusa-chatbot.firebaseapp.com",
    )
    assert load(MEMORY).cors_allowed_origins == ()
    assert parse_cors_origins("http://localhost:5500") == ("http://localhost:5500",)


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "https://a.web.app/",
        "https://a.web.app/path",
        "http://evil.example",
        "null",
        "https://localhost",
    ],
)
def test_unsafe_cors_origins_fail_fast(value):
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        load({**MEMORY, "CORS_ALLOWED_ORIGINS": value})


AUTH0 = {
    "AUTH0_DOMAIN": "Auth.Example.com",
    "AUTH0_AUDIENCE": "https://chat.example.com/api",
    "AUTH0_CLIENT_ID": "abcDEF123_-xyz",
}


def test_auth0_is_disabled_by_default():
    settings = load(MEMORY)
    assert settings.auth0_domain is None
    assert settings.auth0_audience is None
    assert settings.auth0_client_id is None


def test_auth0_settings_are_parsed():
    settings = load({**MEMORY, **AUTH0})
    assert settings.auth0_domain == "auth.example.com"
    assert settings.auth0_audience == "https://chat.example.com/api"
    assert settings.auth0_client_id == "abcDEF123_-xyz"


def test_auth0_line_connection_is_optional_and_parsed():
    assert load({**MEMORY, **AUTH0}).auth0_line_connection is None
    settings = load({**MEMORY, **AUTH0, "AUTH0_LINE_CONNECTION": "line-chat"})
    assert settings.auth0_line_connection == "line-chat"


@pytest.mark.parametrize(
    "environment",
    [
        {"AUTH0_LINE_CONNECTION": "line"},  # 沒有 Auth0
        {**AUTH0, "AUTH0_LINE_CONNECTION": "bad name"},
        {**AUTH0, "AUTH0_LINE_CONNECTION": "-line"},
    ],
)
def test_invalid_auth0_line_connection_fails_fast(environment):
    with pytest.raises(RuntimeError, match="AUTH0_LINE_CONNECTION"):
        load({**MEMORY, **environment})


def test_partial_auth0_settings_fail_fast():
    with pytest.raises(RuntimeError, match="AUTH0_CLIENT_ID"):
        load({**MEMORY, **AUTH0, "AUTH0_CLIENT_ID": ""})


@pytest.mark.parametrize(
    "name,value",
    [
        ("AUTH0_DOMAIN", "https://auth.example.com"),
        ("AUTH0_DOMAIN", "auth.example.com/"),
        ("AUTH0_CLIENT_ID", "bad id!"),
        ("AUTH0_AUDIENCE", "x" * 201),
    ],
)
def test_invalid_auth0_settings_fail_fast(name, value):
    with pytest.raises(RuntimeError, match=name):
        load({**MEMORY, **AUTH0, name: value})
