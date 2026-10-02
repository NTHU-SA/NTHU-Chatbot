import pytest

from src.application.models.identity import lookup_key
from tests.fakes import AUTH, TEST_IDENTITY, make_settings, parse_sse

CONSENT = "/api/consents/privacy_policy"


def user_id(chat_app) -> str:
    return chat_app.state.user_store.lookup[lookup_key("line", TEST_IDENTITY.provider_user_id)]


def session(client) -> str:
    return client.post("/api/sessions", headers=AUTH, json={}).json()["id"]


def send(client, session_id, text="hi"):
    return client.post(f"/api/sessions/{session_id}/messages", headers=AUTH, json={"text": text})


# -- consent --
def test_new_user_must_consent_before_ai_chat(client, runner):
    me = client.get("/api/me", headers=AUTH).json()
    assert me["consent"] == {"type": "privacy_policy", "version": "1", "accepted": False}

    session_id = session(client)  # 建立 / 列出對話不經過 AI，不需要同意
    response = send(client, session_id)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "consent_required"
    assert runner.streams == []  # 沒有任何內容送到 LLM
    assert client.get(f"/api/sessions/{session_id}/messages", headers=AUTH).json() == []

    accepted = client.post(CONSENT, headers=AUTH, json={"version": "1"})
    assert accepted.json() == {"type": "privacy_policy", "version": "1", "accepted": True}
    assert client.get("/api/me", headers=AUTH).json()["consent"]["accepted"]
    assert send(client, session_id).status_code == 200


def test_revoking_consent_blocks_ai_chat_again(client, chat_app):
    client.post(CONSENT, headers=AUTH, json={"version": "1"})
    session_id = session(client)
    assert send(client, session_id).status_code == 200

    revoked = client.post(f"{CONSENT}/revoke", headers=AUTH)
    assert revoked.json()["accepted"] is False
    assert send(client, session_id).status_code == 403
    audit = chat_app.state.user_store.audit[user_id(chat_app)]
    assert [entry["action"] for entry in audit] == ["consent", "revoke"]


def test_accepting_a_stale_version_is_rejected(client):
    assert client.post(CONSENT, headers=AUTH, json={"version": "0"}).status_code == 409
    assert not client.get("/api/me", headers=AUTH).json()["consent"]["accepted"]


def test_policy_version_bump_requires_consent_again(client, chat_app):
    client.post(CONSENT, headers=AUTH, json={"version": "1"})
    session_id = session(client)
    chat_app.state.settings = make_settings(privacy_policy_version="2")
    assert send(client, session_id).status_code == 403
    me = client.get("/api/me", headers=AUTH).json()
    assert me["consent"] == {"type": "privacy_policy", "version": "2", "accepted": False}
    client.post(CONSENT, headers=AUTH, json={"version": "2"})
    assert send(client, session_id).status_code == 200
    # 舊版本的同意紀錄保留，不被覆蓋
    assert set(chat_app.state.user_store.consents[user_id(chat_app)]) == {
        "privacy_policy_v1",
        "privacy_policy_v2",
    }


def test_unknown_consent_type_is_404(client):
    assert client.post("/api/consents/marketing", headers=AUTH, json={"version": "1"}).status_code == 404


# -- delete my data --
def test_delete_me_removes_everything_and_next_login_is_a_new_user(client, chat_app):
    client.post(CONSENT, headers=AUTH, json={"version": "1"})
    session_id = session(client)
    events = parse_sse(send(client, session_id, "我的秘密").text)
    assert events[-1][0] == "done"
    old_id = user_id(chat_app)

    assert client.delete("/api/me", headers=AUTH).status_code == 204

    users, chats = chat_app.state.user_store, chat_app.state.store
    assert old_id not in users.users
    assert old_id not in users.identities
    assert old_id not in users.consents
    assert lookup_key("line", TEST_IDENTITY.provider_user_id) not in users.lookup
    assert old_id not in chats._sessions
    assert not any(key[0] == old_id for key in chats._messages)

    # 同一個 LINE 帳號再開啟：全新的 user，沒有舊對話，也要重新同意
    me = client.get("/api/me", headers=AUTH).json()
    assert me["consent"]["accepted"] is False
    assert user_id(chat_app) != old_id
    assert client.get("/api/sessions", headers=AUTH).json() == []


def test_delete_me_requires_auth(client):
    assert client.delete("/api/me").status_code == 401


@pytest.mark.parametrize("value", ["", "has space", "x" * 21])
def test_invalid_policy_version_fails_fast(value, monkeypatch):
    import os

    from src.core.config import Settings

    base = {
        "LINE_CHANNEL_SECRET": "s", "LINE_CHANNEL_ACCESS_TOKEN": "t", "LINE_LOGIN_CHANNEL_ID": "1",
        "LIFF_ID": "l", "OPENAI_API_KEY": "k", "CHAT_STORE": "memory",
        "PRIVACY_POLICY_VERSION": value,
    }
    monkeypatch.setattr(os, "environ", base)
    if value == "":
        assert Settings.from_env().privacy_policy_version == "1"  # 空值沿用預設
    else:
        with pytest.raises(RuntimeError, match="PRIVACY_POLICY_VERSION"):
            Settings.from_env()
