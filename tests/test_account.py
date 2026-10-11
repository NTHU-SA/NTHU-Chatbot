from pathlib import Path

from src.application.models.identity import REUSE_AFTER_DELETION, DeletionIncompleteError
from src.core.privacy import PRIVACY_POLICY_VERSION
from tests.fakes import AUTH, TEST_USR, make_settings, parse_sse

CONSENT = "/api/consents/privacy_policy"


def user_id(chat_app) -> str:
    assert TEST_USR in chat_app.state.user_store.users
    return TEST_USR


def age_tombstone(chat_app, uid):
    """模擬刪除已超過 REUSE_AFTER_DELETION（期間各實例的快取也都過期）。"""
    chat_app.state.user_store.users[uid]["deletedAt"] -= REUSE_AFTER_DELETION
    chat_app.state.identity_service.forget_user(uid)


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
    # 附上需要同意的版本：頁面開著時政策改版，前端也知道要請使用者同意哪一版
    assert response.json()["detail"]["code"] == "consent_required"
    assert response.json()["detail"]["version"] == "1"
    assert runner.streams == []  # 沒有任何內容送到 LLM
    assert client.get(f"/api/sessions/{session_id}/messages", headers=AUTH).json() == []

    accepted = client.post(CONSENT, headers=AUTH, json={"version": "1"})
    assert accepted.json() == {"type": "privacy_policy", "version": "1", "accepted": True}
    assert client.get("/api/me", headers=AUTH).json()["consent"]["accepted"]
    assert send(client, session_id).status_code == 200


def test_removed_revocation_endpoint_does_not_change_consent(client, chat_app):
    client.post(CONSENT, headers=AUTH, json={"version": "1"})
    session_id = session(client)
    assert send(client, session_id).status_code == 200

    assert client.post(f"{CONSENT}/revoke", headers=AUTH).status_code == 404
    assert client.get("/api/me", headers=AUTH).json()["consent"]["accepted"] is True
    assert send(client, session_id).status_code == 200
    audit = chat_app.state.user_store.audit[user_id(chat_app)]
    assert [entry["action"] for entry in audit] == ["consent"]


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
    assert (
        client.post("/api/consents/marketing", headers=AUTH, json={"version": "1"}).status_code
        == 404
    )


# -- delete my data --
def test_delete_me_removes_everything_and_later_login_starts_fresh(client, chat_app):
    client.post(CONSENT, headers=AUTH, json={"version": "1"})
    session_id = session(client)
    events = parse_sse(send(client, session_id, "我的秘密").text)
    assert events[-1][0] == "done"
    old_id = user_id(chat_app)

    assert client.delete("/api/me", headers=AUTH).status_code == 204

    users, chats = chat_app.state.user_store, chat_app.state.store
    assert set(users.users[old_id]) == {"status", "deletedAt"}  # 只剩不含個資的墓碑
    assert users.users[old_id]["status"] == "deleted"
    assert old_id not in users.identities
    assert old_id not in users.consents
    assert users.lookup == {}
    assert old_id not in chats._sessions
    assert not any(key[0] == old_id for key in chats._messages)

    # 剛刪除：還不能重新開始（刪除前已在進行的請求可能晚到）
    early = client.get("/api/me", headers=AUTH)
    assert early.status_code == 403
    assert early.json()["detail"]["code"] == "account_deleted"

    # 之後同一個 NTHUSA ID 再開啟：空白的帳號，沒有舊對話，也要重新同意
    age_tombstone(chat_app, old_id)
    me = client.get("/api/me", headers=AUTH).json()
    assert me["consent"]["accepted"] is False
    assert users.users[old_id]["status"] == "active"
    assert client.get("/api/sessions", headers=AUTH).json() == []


def test_delete_me_requires_auth(client):
    assert client.delete("/api/me").status_code == 401


def test_failed_deletion_can_be_retried_and_blocks_everything_else(client, chat_app, monkeypatch):
    client.post(CONSENT, headers=AUTH, json={"version": "1"})
    session(client)
    users = chat_app.state.user_store
    old_id = user_id(chat_app)
    real_delete = users.delete_user

    async def flaky(uid):
        raise DeletionIncompleteError(uid)

    monkeypatch.setattr(users, "delete_user", flaky)
    failed = client.delete("/api/me", headers=AUTH)
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "deletion_incomplete"
    assert users.users[old_id]["status"] == "deleting"

    # 刪除中：其他 API 一律擋下，前端據此顯示「完成刪除」
    blocked = client.get("/api/me", headers=AUTH)
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "account_deleting"

    monkeypatch.setattr(users, "delete_user", real_delete)
    assert client.delete("/api/me", headers=AUTH).status_code == 204
    assert users.users[old_id]["status"] == "deleted"
    age_tombstone(chat_app, old_id)
    assert client.get("/api/me", headers=AUTH).status_code == 200


def test_policy_version_has_one_source():
    from src.core.config import Settings

    assert Settings.__dataclass_fields__["privacy_policy_version"].default == PRIVACY_POLICY_VERSION
    # 沒有經過 build 的頁面（本機開發）也顯示同一個版本
    page = Path(__file__).resolve().parent.parent / "frontend" / "privacy.html"
    marker = f"<span data-policy-version>{PRIVACY_POLICY_VERSION}</span>"
    assert marker in page.read_text(encoding="utf-8")
