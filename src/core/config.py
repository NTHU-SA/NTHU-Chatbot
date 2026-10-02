"""
應用程式設定。

所有機密（LINE token、OpenAI 金鑰）只從環境變數讀取：本機用 git-ignored 的 `.env`，
Cloud Run 由 Secret Manager 注入。這裡不提供任何機密的預設值，設定錯誤時在啟動階段直接失敗。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

DEFAULT_MCP_TOOLS = (
    "search_campus",
    "get_next_buses",
    "get_bus_stops",
    "search_courses",
    "get_announcements",
    "find_dining",
    "get_library_info",
    "get_newsletters",
    "get_energy_usage",
)

CHAT_STORES = ("firestore", "memory")

# 只接受 https 網域（本機開發可用 http://localhost / 127.0.0.1），不接受萬用字元、路徑或結尾斜線
_ORIGIN = re.compile(
    r"^(https://[a-z0-9-]+(\.[a-z0-9-]+)+|http://(localhost|127\.0\.0\.1))(:\d{1,5})?$"
)


_POLICY_VERSION = re.compile(r"^[0-9A-Za-z.-]{1,20}$")


def _policy_version(value: str | None) -> str:
    version = (value or "1").strip()
    if not _POLICY_VERSION.match(version):
        raise RuntimeError("Invalid PRIVACY_POLICY_VERSION")
    return version


_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")


def _domains(value: str | None) -> tuple[str, ...]:
    """網路搜尋允許的網域（逗號分隔）；只接受裸網域，子網域自動包含。"""
    domains = _csv((value or "").lower(), ("nthu.edu.tw",))
    invalid = [domain for domain in domains if not _DOMAIN.match(domain)]
    if invalid:
        raise RuntimeError("Invalid WEB_SEARCH_DOMAINS entry: " + ", ".join(invalid))
    return domains


def parse_cors_origins(value: str | None) -> tuple[str, ...]:
    """
    解析 `CORS_ALLOWED_ORIGINS`（逗號分隔）。

    格式不符時啟動失敗：寧可擋下也不要意外放行其他網站呼叫 API。
    """
    origins = tuple(item.strip().lower() for item in (value or "").split(",") if item.strip())
    invalid = [origin for origin in origins if not _ORIGIN.match(origin)]
    if invalid:
        raise RuntimeError("Invalid CORS_ALLOWED_ORIGINS entry: " + ", ".join(invalid))
    return origins


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise RuntimeError(f"Invalid integer for {name}") from None
    if parsed < minimum:
        raise RuntimeError(f"Invalid {name}, expected an integer >= {minimum}")
    return parsed


def _float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise RuntimeError(f"Invalid number for {name}") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise RuntimeError(f"Invalid {name}, expected a finite number > 0")
    return parsed


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None or value.strip() == "":
        return default
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    # LINE Messaging API channel（機密）
    line_channel_secret: str = field(repr=False)
    line_channel_access_token: str = field(repr=False)
    # LINE Login channel / LIFF
    line_login_channel_id: str
    liff_id: str
    # LLM（任何 OpenAI 相容端點）
    openai_api_key: str = field(repr=False)
    openai_base_url: str | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_use_responses_api: bool = False
    # 串流模型的思考摘要給前端（僅 reasoning 模型 + Responses API 有效）
    reasoning_summary: bool = False
    # MCP
    mcp_server_url: str = "https://api.nthusa.tw/mcp"
    mcp_allowed_tools: tuple[str, ...] = DEFAULT_MCP_TOOLS
    mcp_timeout_seconds: float = 30.0
    # LIFF 前端（Firebase Hosting）的網域；API 只允許這些網域跨站呼叫
    cors_allowed_origins: tuple[str, ...] = ()
    # 隱私權政策版本；改版時遞增，使用者需重新同意才能使用 AI 對話
    privacy_policy_version: str = "1"
    # 儲存
    chat_store: str = "firestore"
    google_cloud_project: str | None = None
    # 限制
    history_window: int = 10
    history_message_chars: int = 1500
    max_tool_output_chars: int = 6000
    max_message_chars: int = 2000
    max_output_tokens: int = 2000
    max_output_chars: int = 8000
    daily_message_limit: int = 100
    max_agent_turns: int = 8
    # 每則訊息最多幾次外部工具呼叫（MCP + 網路搜尋）
    max_tool_calls_per_message: int = 6
    # 網路搜尋（只在官方 OpenAI Responses API 下可用；限定網域）
    web_search_enabled: bool = False
    web_search_model: str | None = None
    web_search_domains: tuple[str, ...] = ("nthu.edu.tw",)
    max_web_searches_per_message: int = 2
    tool_result_preview_chars: int = 500

    @property
    def liff_url(self) -> str:
        return f"https://liff.line.me/{self.liff_id}"

    @classmethod
    def from_env(cls) -> Settings:
        """
        從環境變數建立設定。

        缺少必要變數時只列出變數名稱，不會印出任何值。
        """
        chat_store = (os.getenv("CHAT_STORE") or "firestore").strip().lower()
        if chat_store not in CHAT_STORES:
            raise RuntimeError(
                "Invalid CHAT_STORE, expected one of: " + ", ".join(CHAT_STORES)
            )

        required = [
            "LINE_CHANNEL_SECRET",
            "LINE_CHANNEL_ACCESS_TOKEN",
            "LINE_LOGIN_CHANNEL_ID",
            "LIFF_ID",
            "OPENAI_API_KEY",
        ]
        if chat_store == "firestore":
            required.append("GOOGLE_CLOUD_PROJECT")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise RuntimeError("Missing environment variables: " + ", ".join(missing))

        return cls(
            line_channel_secret=os.environ["LINE_CHANNEL_SECRET"],
            line_channel_access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"],
            line_login_channel_id=os.environ["LINE_LOGIN_CHANNEL_ID"],
            liff_id=os.environ["LIFF_ID"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_base_url=os.getenv("OPENAI_BASE_URL") or None,
            openai_model=os.getenv("OPENAI_MODEL") or "gpt-4.1-mini",
            openai_use_responses_api=_bool(os.getenv("OPENAI_USE_RESPONSES_API"), False),
            reasoning_summary=_bool(os.getenv("REASONING_SUMMARY"), False),
            mcp_server_url=os.getenv("MCP_SERVER_URL") or "https://api.nthusa.tw/mcp",
            mcp_allowed_tools=_csv(os.getenv("MCP_ALLOWED_TOOLS"), DEFAULT_MCP_TOOLS),
            mcp_timeout_seconds=_float("MCP_TIMEOUT_SECONDS", 30.0),
            cors_allowed_origins=parse_cors_origins(os.getenv("CORS_ALLOWED_ORIGINS")),
            privacy_policy_version=_policy_version(os.getenv("PRIVACY_POLICY_VERSION")),
            chat_store=chat_store,
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT") or None,
            history_window=_int("HISTORY_WINDOW", 10),
            history_message_chars=_int("HISTORY_MESSAGE_CHARS", 1500),
            max_tool_output_chars=_int("MAX_TOOL_OUTPUT_CHARS", 6000),
            max_message_chars=_int("MAX_MESSAGE_CHARS", 2000),
            max_output_tokens=_int("MAX_OUTPUT_TOKENS", 2000),
            max_output_chars=_int("MAX_OUTPUT_CHARS", 8000),
            daily_message_limit=_int("DAILY_MESSAGE_LIMIT", 100, minimum=0),
            max_agent_turns=_int("MAX_AGENT_TURNS", 8),
            max_tool_calls_per_message=_int("MAX_TOOL_CALLS_PER_MESSAGE", 6),
            web_search_enabled=_bool(os.getenv("WEB_SEARCH_ENABLED"), False),
            web_search_model=os.getenv("WEB_SEARCH_MODEL") or None,
            web_search_domains=_domains(os.getenv("WEB_SEARCH_DOMAINS")),
            max_web_searches_per_message=_int("MAX_WEB_SEARCHES_PER_MESSAGE", 2),
            tool_result_preview_chars=_int("TOOL_RESULT_PREVIEW_CHARS", 500, minimum=0),
        )
