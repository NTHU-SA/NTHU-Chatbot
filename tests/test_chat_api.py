import unittest

from fastapi.testclient import TestClient

from src.app import create_app
from src.app.security import RateLimiter
from src.application.services.chat_store import MAX_SESSIONS_PER_USER, MemoryChatStore
from tests.fakes import (
    AUTH,
    TEST_LIFF_ID,
    FakeRunner,
    FakeVerifier,
    make_settings,
    parse_sse,
)


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings(daily_message_limit=3)
        self.runner = FakeRunner()
        self.app = create_app()
        self.app.state.settings = self.settings
        self.app.state.store = MemoryChatStore()
        self.app.state.token_verifier = FakeVerifier()
        self.app.state.rate_limiter = RateLimiter(rate_per_minute=600, burst=100)
        self.app.state.agent_runner = self.runner
        self.client = TestClient(self.app)

    def new_session(self) -> str:
        response = self.client.post("/api/sessions", headers=AUTH, json={})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def send(self, session_id: str, text: str):
        return self.client.post(
            f"/api/sessions/{session_id}/messages", headers=AUTH, json={"text": text}
        )

    # -- auth / headers --
    def test_config_is_public_and_only_exposes_liff_id(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"liff_id": TEST_LIFF_ID})

    def test_api_requires_valid_bearer(self):
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/sessions", headers={"Authorization": "Bearer nope"}
            ).status_code,
            401,
        )
        response = self.client.get("/api/me", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], "U0123456789abcdef")

    def test_security_headers_and_liff_csp(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertNotIn("content-security-policy", response.headers)
        response = self.client.get("/liff/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("狗狗情報員", response.text)
        self.assertIn(
            "script-src 'self' https://static.line-scdn.net",
            response.headers["content-security-policy"],
        )

    def test_docs_are_disabled(self):
        for path in ("/docs", "/redoc", "/openapi.json"):
            self.assertEqual(self.client.get(path).status_code, 404)

    # -- sessions --
    def test_session_crud(self):
        session_id = self.new_session()
        response = self.client.get("/api/sessions", headers=AUTH)
        self.assertEqual([s["id"] for s in response.json()], [session_id])
        self.assertEqual(response.json()[0]["title"], "新對話")

        response = self.client.patch(
            f"/api/sessions/{session_id}", headers=AUTH, json={"title": "公車"}
        )
        self.assertEqual(response.json()["title"], "公車")

        self.assertEqual(
            self.client.get("/api/sessions/nope/messages", headers=AUTH).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(f"/api/sessions/{session_id}", headers=AUTH).status_code,
            204,
        )
        self.assertEqual(self.client.get("/api/sessions", headers=AUTH).json(), [])

    def test_bubble_origin_reopens_same_session_until_deleted(self):
        body = {"title": "南大公車", "origin": "event-1"}
        first = self.client.post("/api/sessions", headers=AUTH, json=body)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["origin"], "event-1")

        # 同一顆泡泡再點：回原對話（200），不新建
        again = self.client.post("/api/sessions", headers=AUTH, json=body)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["id"], first.json()["id"])
        self.assertEqual(len(self.client.get("/api/sessions", headers=AUTH).json()), 1)

        # 不同泡泡：新對話
        other = self.client.post(
            "/api/sessions", headers=AUTH, json={"title": "x", "origin": "event-2"}
        )
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.json()["id"], first.json()["id"])

        # 刪掉後同一顆泡泡再點：重新建立
        self.client.delete(f"/api/sessions/{first.json()['id']}", headers=AUTH)
        rebuilt = self.client.post("/api/sessions", headers=AUTH, json=body)
        self.assertEqual(rebuilt.status_code, 201)
        self.assertNotEqual(rebuilt.json()["id"], first.json()["id"])

    def test_bubble_origin_is_scoped_per_user(self):
        body = {"origin": "event-1"}
        mine = self.client.post("/api/sessions", headers=AUTH, json=body).json()
        self.app.state.token_verifier.valid["other-token"] = type(
            self.app.state.token_verifier.valid["good-token"]
        )(user_id="Uother", display_name=None, picture_url=None)
        other = {"Authorization": "Bearer other-token"}
        theirs = self.client.post("/api/sessions", headers=other, json=body)
        self.assertEqual(theirs.status_code, 201)
        self.assertNotEqual(theirs.json()["id"], mine["id"])

    def test_session_limit_evicts_oldest(self):
        first = self.new_session()
        for _ in range(MAX_SESSIONS_PER_USER - 1):
            self.new_session()
        # 對最舊的對話發訊息，讓它變成「最近更新」，就不該被淘汰
        self.send(first, "keep me")
        second_oldest = self.client.get("/api/sessions", headers=AUTH).json()[-1]["id"]

        newest = self.new_session()
        ids = [s["id"] for s in self.client.get("/api/sessions", headers=AUTH).json()]
        self.assertEqual(len(ids), MAX_SESSIONS_PER_USER)
        self.assertIn(newest, ids)
        self.assertIn(first, ids)
        self.assertNotIn(second_oldest, ids)
        self.assertEqual(
            self.client.get(f"/api/sessions/{second_oldest}/messages", headers=AUTH).status_code,
            404,
        )

    # -- chat SSE --
    def test_send_message_streams_and_persists(self):
        session_id = self.new_session()
        response = self.send(session_id, "南大公車")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = parse_sse(response.text)
        self.assertEqual(
            [name for name, _ in events],
            [
                "user_message",
                "thinking",
                "tool_call_start",
                "tool_call_end",
                "token",
                "token",
                "done",
            ],
        )
        self.assertEqual(events[2][1]["name"], "get_next_buses")
        self.assertEqual(events[-1][1]["content"], "下一班 17:00")

        messages = self.client.get(
            f"/api/sessions/{session_id}/messages", headers=AUTH
        ).json()
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[1]["tool_calls"][0]["name"], "get_next_buses")

        sessions = self.client.get("/api/sessions", headers=AUTH).json()
        self.assertEqual(sessions[0]["title"], "南大公車")
        self.assertEqual(sessions[0]["message_count"], 2)

        self.send(session_id, "那回程呢")
        history, text = self.runner.streams[-1]
        self.assertEqual(text, "那回程呢")
        self.assertEqual([m.content for m in history], ["南大公車", "下一班 17:00"])

    def test_agent_error_is_reported_not_persisted(self):
        self.runner.fail = True
        session_id = self.new_session()
        response = self.send(session_id, "hi")
        self.assertEqual(
            [name for name, _ in parse_sse(response.text)], ["user_message", "error"]
        )
        messages = self.client.get(
            f"/api/sessions/{session_id}/messages", headers=AUTH
        ).json()
        self.assertEqual([m["role"] for m in messages], ["user"])

    def test_daily_quota_enforced(self):
        session_id = self.new_session()
        for _ in range(3):
            self.assertEqual(self.send(session_id, "x").status_code, 200)
        self.assertEqual(self.send(session_id, "x").status_code, 429)

    def test_message_too_long(self):
        session_id = self.new_session()
        response = self.send(session_id, "a" * (self.settings.max_message_chars + 1))
        self.assertEqual(response.status_code, 413)

    def test_users_are_isolated(self):
        session_id = self.new_session()
        self.app.state.token_verifier.valid["other-token"] = type(
            self.app.state.token_verifier.valid["good-token"]
        )(user_id="Uother", display_name=None, picture_url=None)
        other = {"Authorization": "Bearer other-token"}
        self.assertEqual(
            self.client.get(f"/api/sessions/{session_id}/messages", headers=other).status_code,
            404,
        )
        self.assertEqual(self.client.get("/api/sessions", headers=other).json(), [])
