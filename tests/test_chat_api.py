import asyncio
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.app.security import LineUser
from src.application.services.chat_store import MAX_SESSIONS_PER_USER, MemoryChatStore
from src.infrastructure.ai.agent_runner import AgentEvent
from tests.fakes import AUTH, TEST_LIFF_ID, FakeRunner, make_settings, parse_sse

OTHER = {"Authorization": "Bearer other-token"}


@pytest.fixture
def other_user(chat_app):
    """註冊第二位使用者的 token，回傳對應的 headers。"""
    chat_app.state.token_verifier.valid["other-token"] = LineUser(user_id="Uother")
    return OTHER


def new_session(client) -> str:
    response = client.post("/api/sessions", headers=AUTH, json={})
    assert response.status_code == 201
    return response.json()["id"]


def send(client, session_id: str, text: str):
    return client.post(
        f"/api/sessions/{session_id}/messages", headers=AUTH, json={"text": text}
    )


def messages_of(client, session_id: str, headers=AUTH):
    return client.get(f"/api/sessions/{session_id}/messages", headers=headers)


def session_ids(client) -> list[str]:
    return [s["id"] for s in client.get("/api/sessions", headers=AUTH).json()]


# -- auth / headers --
def test_config_is_public_and_only_exposes_liff_id(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    assert response.json() == {"liff_id": TEST_LIFF_ID}


def test_api_requires_valid_bearer(client):
    assert client.get("/api/sessions").status_code == 401
    assert (
        client.get("/api/sessions", headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )
    response = client.get("/api/me", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["user_id"] == "U0123456789abcdef"


def test_security_headers_and_liff_csp(client):
    response = client.get("/api/config")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" not in response.headers
    response = client.get("/liff/")
    assert response.status_code == 200
    assert "狗狗情報員" in response.text
    assert (
        "script-src 'self' https://static.line-scdn.net"
        in response.headers["content-security-policy"]
    )


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_are_disabled(client, path):
    assert client.get(path).status_code == 404


# -- sessions --
def test_session_crud(client):
    session_id = new_session(client)
    sessions = client.get("/api/sessions", headers=AUTH).json()
    assert [s["id"] for s in sessions] == [session_id]
    assert sessions[0]["title"] == "新對話"

    response = client.patch(
        f"/api/sessions/{session_id}", headers=AUTH, json={"title": "公車"}
    )
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
    other = client.post(
        "/api/sessions", headers=AUTH, json={"title": "x", "origin": "event-2"}
    )
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
    find = store.find_session_by_origin

    async def delayed_find(*args):
        session = await find(*args)
        await asyncio.sleep(0)
        return session

    async with AsyncClient(
        transport=ASGITransport(app=chat_app), base_url="http://test"
    ) as client:
        with patch.object(store, "find_session_by_origin", delayed_find):
            responses = await asyncio.gather(
                *(
                    client.post(
                        "/api/sessions", headers=AUTH, json={"origin": "concurrent-event"}
                    )
                    for _ in range(20)
                )
            )
    codes = [r.status_code for r in responses]
    assert codes.count(201) == 1
    assert codes.count(200) == 19
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(store._sessions["U0123456789abcdef"]) == 1


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
def test_send_message_streams_and_persists(client, runner):
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
    assert events[2][1]["name"] == "get_next_buses"
    assert events[-1][1]["content"] == "下一班 17:00"

    messages = messages_of(client, session_id).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["tool_calls"][0]["name"] == "get_next_buses"

    sessions = client.get("/api/sessions", headers=AUTH).json()
    assert sessions[0]["title"] == "南大公車"
    assert sessions[0]["message_count"] == 2

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


def test_oversized_agent_output_is_bounded_before_persistence(client, chat_app):
    chat_app.state.settings = make_settings(max_output_chars=12)
    output = "超長回覆" * 20

    class OversizedRunner(FakeRunner):
        async def stream(self, history, user_text):
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
