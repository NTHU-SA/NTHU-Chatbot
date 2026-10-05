import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from src.application.models.identity import lookup_key
from src.application.services.chat_store import MAX_SESSIONS_PER_USER, MemoryChatStore
from src.infrastructure.ai.agent_runner import AgentEvent
from src.infrastructure.ai.prompts import PROMPT_VERSION
from tests.fakes import (
    AUTH,
    TEST_IDENTITY,
    TEST_LIFF_ID,
    FakeRunner,
    make_settings,
    parse_sse,
)

OTHER = {"Authorization": "Bearer other-token"}


@pytest.fixture
def other_user():
    """第二位使用者（FakeAuthenticator 認得 other-token）的 headers。"""
    return OTHER


def internal_id(chat_app) -> str:
    """測試使用者對應到的內部 user id。"""
    users = chat_app.state.user_store
    return users.lookup[lookup_key("line", TEST_IDENTITY.provider_user_id)]


def accept_policy(client, headers=AUTH):
    """每個 client 只同意一次：避免大量建立對話的測試撞上限流。"""
    if getattr(client, "_policy_accepted", False):
        return
    response = client.post("/api/consents/privacy_policy", headers=headers, json={"version": "1"})
    assert response.status_code == 200
    client._policy_accepted = True


def new_session(client) -> str:
    accept_policy(client)
    response = client.post("/api/sessions", headers=AUTH, json={})
    assert response.status_code == 201
    return response.json()["id"]


def send(client, session_id: str, text: str):
    return client.post(f"/api/sessions/{session_id}/messages", headers=AUTH, json={"text": text})


def messages_of(client, session_id: str, headers=AUTH):
    return client.get(f"/api/sessions/{session_id}/messages", headers=headers)


def session_ids(client) -> list[str]:
    return [s["id"] for s in client.get("/api/sessions", headers=AUTH).json()]


# -- auth / headers --
@pytest.mark.parametrize("path", ["/api/config", "/liff/", "/liff/index.html"])
def test_frontend_is_not_served_by_the_api(client, path):
    """LIFF 頁面與設定改由 Firebase Hosting 提供。"""
    assert client.get(path).status_code == 404


def test_api_requires_valid_bearer(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers={"Authorization": "Bearer nope"}).status_code == 401
    response = client.get("/api/me", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["display_name"] == "測試者"


def test_unknown_auth_provider_is_rejected(client):
    headers = {**AUTH, "X-Auth-Provider": "google"}
    response = client.get("/api/sessions", headers=headers)
    assert response.status_code == 401
    # 不區分「provider 不存在」與「token 無效」
    assert response.json()["detail"] == "invalid credentials"


def test_me_exposes_no_identifiers(client, chat_app):
    body = client.get("/api/me", headers=AUTH).json()
    assert set(body) == {"display_name", "picture_url", "liff_id", "consent"}
    assert TEST_IDENTITY.provider_user_id not in str(body)
    assert internal_id(chat_app) not in str(body)


def test_first_request_creates_internal_user_once(client, chat_app):
    client.get("/api/me", headers=AUTH)
    client.get("/api/sessions", headers=AUTH)
    users = chat_app.state.user_store
    user_id = internal_id(chat_app)
    assert user_id.startswith("usr_")
    assert list(users.users) == [user_id]
    assert users.identities[user_id]["line"]["providerUserId"] == TEST_IDENTITY.provider_user_id


def test_disabled_account_is_forbidden(client, chat_app):
    client.get("/api/me", headers=AUTH)
    chat_app.state.user_store.users[internal_id(chat_app)]["status"] = "blocked"
    chat_app.state.identity_service._status.clear()  # 略過 60 秒的狀態快取
    assert client.get("/api/sessions", headers=AUTH).status_code == 403


def test_me_post_stores_liff_client_info_as_metadata(client, chat_app):
    info = {
        "os": "ios",
        "line_version": "14.0.0",
        "language": "zh-TW",
        "context_type": "utou",
        "friendship": True,
    }
    response = client.post("/api/me", headers=AUTH, json=info)
    assert response.status_code == 200
    metadata = chat_app.state.user_store.identities[internal_id(chat_app)]["line"]["metadata"]
    assert metadata["liff"] == {
        "liffId": TEST_LIFF_ID,
        "os": "ios",
        "appVersion": "14.0.0",
        "language": "zh-TW",
        "contextType": "utou",
        "friendshipStatus": True,
    }
    assert metadata["displayName"] == "測試者"


@pytest.mark.parametrize(
    "info",
    [
        {"context_id": "C123"},  # 不收的欄位
        {"os": "windows"},  # 不在白名單
        {"line_version": "x" * 33},  # 超過長度
    ],
)
def test_me_post_rejects_unexpected_client_info(client, info):
    assert client.post("/api/me", headers=AUTH, json=info).status_code == 422


def test_security_headers_on_api_responses(client):
    response = client.get("/api/sessions", headers=AUTH)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert (
        response.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    )


FRONTEND = "https://nthusa-chatbot.web.app"


@pytest.fixture
def cors_client(chat_app, monkeypatch):
    """以設定了 CORS_ALLOWED_ORIGINS 的環境重新建立 app，沿用 chat_app 的 state。"""
    from fastapi.testclient import TestClient

    from src.app import create_app

    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", FRONTEND)
    app = create_app()
    app.state._state.update(chat_app.state._state)
    return TestClient(app)


def preflight(client, origin):
    return client.options(
        "/api/sessions",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type,x-auth-provider",
        },
    )


def test_cors_allows_only_the_configured_frontend(cors_client):
    allowed = preflight(cors_client, FRONTEND)
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == FRONTEND
    assert "access-control-allow-credentials" not in allowed.headers
    assert "x-auth-provider" in allowed.headers["access-control-allow-headers"].lower()

    denied = preflight(cors_client, "https://evil.example")
    assert "access-control-allow-origin" not in denied.headers
    response = cors_client.get("/api/sessions", headers={**AUTH, "Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_no_cors_headers_without_configured_origins(client):
    response = preflight(client, FRONTEND)
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_disabled(client, path):
    assert client.get(path).status_code == 404


# -- sessions --
def test_session_crud(client):
    session_id = new_session(client)
    sessions = client.get("/api/sessions", headers=AUTH).json()
    assert [s["id"] for s in sessions] == [session_id]
    assert sessions[0]["title"] == "新對話"

    response = client.patch(f"/api/sessions/{session_id}", headers=AUTH, json={"title": "公車"})
    assert response.json()["title"] == "公車"

    assert messages_of(client, "nope").status_code == 404
    assert client.delete(f"/api/sessions/{session_id}", headers=AUTH).status_code == 204
    assert client.get("/api/sessions", headers=AUTH).json() == []


def test_bubble_origin_reopens_same_session_until_deleted(client):
    body = {"title": "南大公車", "origin": "event-1"}
    first = client.post("/api/sessions", headers=AUTH, json=body)
    assert first.status_code == 201
    assert first.json()["origin"] == "event-1"

    # 同一顆泡泡再點：回原對話（200），不新建
    again = client.post("/api/sessions", headers=AUTH, json=body)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert len(session_ids(client)) == 1

    # 不同泡泡：新對話
    other = client.post("/api/sessions", headers=AUTH, json={"title": "x", "origin": "event-2"})
    assert other.status_code == 201
    assert other.json()["id"] != first.json()["id"]

    # 刪掉後同一顆泡泡再點：重新建立
    client.delete(f"/api/sessions/{first.json()['id']}", headers=AUTH)
    rebuilt = client.post("/api/sessions", headers=AUTH, json=body)
    assert rebuilt.status_code == 201
    assert rebuilt.json()["id"] != first.json()["id"]


def test_bubble_origin_is_scoped_per_user(client, other_user):
    body = {"origin": "event-1"}
    mine = client.post("/api/sessions", headers=AUTH, json=body).json()
    theirs = client.post("/api/sessions", headers=other_user, json=body)
    assert theirs.status_code == 201
    assert theirs.json()["id"] != mine["id"]


async def test_concurrent_bubble_requests_create_only_once(chat_app):
    store = chat_app.state.store
    async with AsyncClient(transport=ASGITransport(app=chat_app), base_url="http://test") as client:
        responses = await asyncio.gather(
            *(
                client.post("/api/sessions", headers=AUTH, json={"origin": "concurrent-event"})
                for _ in range(20)
            )
        )
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1
    assert codes.count(200) == 19
    assert len({r.json()["id"] for r in responses}) == 1
    # 同一個 LINE 身分只建立了一個內部 user，對話也只有一個
    assert list(store._sessions) == [internal_id(chat_app)]
    assert len(store._sessions[internal_id(chat_app)]) == 1


def test_session_limit_evicts_oldest(client):
    first = new_session(client)
    for _ in range(MAX_SESSIONS_PER_USER - 1):
        new_session(client)
    # 對最舊的對話發訊息，讓它變成「最近更新」，就不該被淘汰
    send(client, first, "keep me")
    second_oldest = session_ids(client)[-1]

    newest = new_session(client)
    ids = session_ids(client)
    assert len(ids) == MAX_SESSIONS_PER_USER
    assert newest in ids
    assert first in ids
    assert second_oldest not in ids
    assert messages_of(client, second_oldest).status_code == 404


# -- chat SSE --
def test_send_message_streams_and_persists(client, chat_app, runner):
    session_id = new_session(client)
    response = send(client, session_id, "南大公車")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [name for name, _ in events] == [
        "user_message",
        "thinking",
        "tool_call_start",
        "tool_call_end",
        "token",
        "token",
        "done",
    ]
    assert events[2][1]["name"] == "get_bus_schedule"
    assert events[-1][1]["content"] == "下一班 17:00"

    messages = messages_of(client, session_id).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["tool_calls"][0]["name"] == "get_bus_schedule"

    sessions = client.get("/api/sessions", headers=AUTH).json()
    assert sessions[0]["title"] == "南大公車"
    assert sessions[0]["message_count"] == 2

    meta = chat_app.state.store.meta[messages[1]["id"]]
    assert meta.model == chat_app.state.settings.openai_model
    assert meta.prompt_version == PROMPT_VERSION
    assert meta.latency_ms >= 0
    assert "usage" not in events[-1][1]

    send(client, session_id, "那回程呢")
    history, text = runner.streams[-1]
    assert text == "那回程呢"
    assert [m.content for m in history] == ["南大公車", "下一班 17:00"]


def test_agent_error_is_reported_not_persisted(client, runner):
    runner.fail = True
    session_id = new_session(client)
    response = send(client, session_id, "hi")
    assert [name for name, _ in parse_sse(response.text)] == ["user_message", "error"]
    assert [m["role"] for m in messages_of(client, session_id).json()] == ["user"]


def test_retry_reuses_the_stored_user_message(client, runner):
    runner.fail = True
    session_id = new_session(client)
    events = parse_sse(send(client, session_id, "hi").text)
    stored_id = events[0][1]["id"]

    runner.fail = False
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "hi", "retry_of": stored_id},
    )
    events = parse_sse(response.text)
    assert events[0] == ("user_message", {"id": stored_id})
    assert events[-1][0] == "done"
    # 使用者訊息只存一次，模型也不會收到重複的問題
    assert [m["role"] for m in messages_of(client, session_id).json()] == ["user", "assistant"]
    history, text = runner.streams[-1]
    assert (history, text) == ([], "hi")


@pytest.mark.parametrize("retry_of", ["someone-else", None])
def test_retry_of_a_message_that_already_has_a_reply_is_rejected(client, runner, retry_of):
    session_id = new_session(client)
    events = parse_sse(send(client, session_id, "hi").text)
    calls = len(runner.streams)
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        headers=AUTH,
        json={"text": "hi", "retry_of": retry_of or events[0][1]["id"]},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "retry_stale"
    assert len(runner.streams) == calls
    assert len(messages_of(client, session_id).json()) == 2


def test_oversized_agent_output_is_bounded_before_persistence(client, chat_app):
    chat_app.state.settings = make_settings(max_output_chars=12)
    output = "超長回覆" * 20

    class OversizedRunner(FakeRunner):
        async def stream(self, history, user_text, context=None):
            yield AgentEvent("done", {"content": output, "tool_calls": []})

    chat_app.state.agent_runner = OversizedRunner()
    session_id = new_session(client)
    response = send(client, session_id, "hi")
    assert response.status_code == 200
    name, data = parse_sse(response.text)[-1]
    assert name == "done"
    assert data["content"] == output[:12]
    last = messages_of(client, session_id).json()[-1]
    assert last["role"] == "assistant"
    assert last["content"] == output[:12]


def test_daily_quota_enforced(client):
    session_id = new_session(client)
    for _ in range(3):
        assert send(client, session_id, "x").status_code == 200
    assert send(client, session_id, "x").status_code == 429


def test_deleting_the_account_does_not_reset_the_daily_quota(client):
    session_id = new_session(client)
    for _ in range(3):
        assert send(client, session_id, "x").status_code == 200
    assert client.delete("/api/me", headers=AUTH).status_code == 204
    # 同一個 LINE 帳號重建：要重新同意，但今天的額度沿用
    client._policy_accepted = False
    accept_policy(client)
    assert send(client, new_session(client), "x").status_code == 429


def test_memory_store_drops_message_metadata_with_the_messages(client, chat_app):
    store = chat_app.state.store
    first = new_session(client)
    send(client, first, "x")
    assert store.meta
    client.delete(f"/api/sessions/{first}", headers=AUTH)
    assert store.meta == {}
    send(client, new_session(client), "x")
    client.delete("/api/me", headers=AUTH)
    assert store.meta == {}


def test_session_that_keeps_changing_reports_a_conflict(client, chat_app, monkeypatch):
    from src.application.models.identity import DeletionIncompleteError

    session_id = new_session(client)

    async def busy(user_id, sid):
        raise DeletionIncompleteError(user_id)

    monkeypatch.setattr(chat_app.state.store, "delete_session", busy)
    response = client.delete(f"/api/sessions/{session_id}", headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "delete_conflict"


def test_message_too_long(client, chat_app):
    session_id = new_session(client)
    limit = chat_app.state.settings.max_message_chars
    assert send(client, session_id, "a" * (limit + 1)).status_code == 413


@pytest.mark.parametrize("limit", [6000, 8])
def test_configured_message_limit_can_be_above_or_below_4000(client, chat_app, runner, limit):
    chat_app.state.settings = make_settings(max_message_chars=limit)
    session_id = new_session(client)
    response = send(client, session_id, "a" * limit)
    assert response.status_code == 200
    assert runner.streams[-1][1] == "a" * limit
    response = send(client, session_id, "a" * (limit + 1))
    assert response.status_code == 413
    assert str(limit) in response.json()["detail"]
    messages = messages_of(client, session_id).json()
    assert len(messages) == 2
    assert messages[0]["content"] == "a" * limit


def test_users_are_isolated(client, other_user):
    session_id = new_session(client)
    assert messages_of(client, session_id, headers=other_user).status_code == 404
    assert client.get("/api/sessions", headers=other_user).json() == []


# -- MemoryChatStore --
async def test_memory_store_same_origin_is_atomic_and_scoped_per_user():
    store = MemoryChatStore()
    results = await asyncio.gather(
        *(store.get_or_create_session("user", "first", "origin") for _ in range(20))
    )
    assert sum(created for _, created in results) == 1
    assert len({session.id for session, _ in results}) == 1
    first = results[0][0]
    await store.add_message("user", first.id, "user", "preserved")
    existing, created = await store.get_or_create_session("user", "changed", "origin")
    assert not created
    assert existing.title == "first"
    assert existing.message_count == 1
    other, created = await store.get_or_create_session("other", "first", "origin")
    assert created
    assert first.id != other.id
    await store.delete_session("user", first.id)
    rebuilt, created = await store.get_or_create_session("user", "first", "origin")
    assert created
    assert first.id != rebuilt.id


async def test_memory_store_concurrent_creates_enforce_limit_and_clean_messages():
    store = MemoryChatStore()
    oldest = await store.create_session("user", "oldest")
    await store.add_message("user", oldest.id, "user", "remove me")
    results = await asyncio.gather(
        *(
            store.get_or_create_session("user", "session", f"origin-{index}")
            for index in range(MAX_SESSIONS_PER_USER + 10)
        )
    )
    assert all(created for _, created in results)
    assert len(store._sessions["user"]) == MAX_SESSIONS_PER_USER
    assert oldest.id not in store._sessions["user"]
    assert ("user", oldest.id) not in store._messages
