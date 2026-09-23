import asyncio
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from src.app import create_app
from src.app.security import RateLimiter
from src.application.services.chat_store import MAX_SESSIONS_PER_USER, MemoryChatStore
from src.infrastructure.ai.agent_runner import AgentEvent
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

    def test_concurrent_bubble_requests_create_only_once(self):
        store = self.app.state.store
        find = store.find_session_by_origin

        async def delayed_find(*args):
            session = await find(*args)
            await asyncio.sleep(0)
            return session

        async def requests():
            async with AsyncClient(
                transport=ASGITransport(app=self.app), base_url="http://test"
            ) as client:
                with patch.object(store, "find_session_by_origin", delayed_find):
                    return await asyncio.gather(
                        *(
                            client.post(
                                "/api/sessions",
                                headers=AUTH,
                                json={"origin": "concurrent-event"},
                            )
                            for _ in range(20)
                        )
                    )

        responses = asyncio.run(requests())
        self.assertEqual([r.status_code for r in responses].count(201), 1)
        self.assertEqual([r.status_code for r in responses].count(200), 19)
        self.assertEqual(len({r.json()["id"] for r in responses}), 1)
        self.assertEqual(len(store._sessions["U0123456789abcdef"]), 1)

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

    def test_oversized_agent_output_is_bounded_before_persistence(self):
        self.app.state.settings = make_settings(max_output_chars=12)
        output = "超長回覆" * 20

        class OversizedRunner(FakeRunner):
            async def stream(self, history, user_text):
                yield AgentEvent("done", {"content": output, "tool_calls": []})

        self.app.state.agent_runner = OversizedRunner()
        session_id = self.new_session()
        response = self.send(session_id, "hi")
        self.assertEqual(response.status_code, 200)
        events = parse_sse(response.text)
        self.assertEqual(events[-1][0], "done")
        self.assertEqual(events[-1][1]["content"], output[:12])
        messages = self.client.get(
            f"/api/sessions/{session_id}/messages", headers=AUTH
        ).json()
        self.assertEqual(messages[-1]["role"], "assistant")
        self.assertEqual(messages[-1]["content"], output[:12])

    def test_daily_quota_enforced(self):
        session_id = self.new_session()
        for _ in range(3):
            self.assertEqual(self.send(session_id, "x").status_code, 200)
        self.assertEqual(self.send(session_id, "x").status_code, 429)

    def test_message_too_long(self):
        session_id = self.new_session()
        response = self.send(session_id, "a" * (self.settings.max_message_chars + 1))
        self.assertEqual(response.status_code, 413)

    def test_configured_message_limit_can_be_above_or_below_4000(self):
        for limit in (6000, 8):
            with self.subTest(limit=limit):
                self.app.state.settings = make_settings(max_message_chars=limit)
                session_id = self.new_session()
                response = self.send(session_id, "a" * limit)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self.runner.streams[-1][1], "a" * limit)
                response = self.send(session_id, "a" * (limit + 1))
                self.assertEqual(response.status_code, 413)
                self.assertIn(str(limit), response.json()["detail"])
                messages = self.client.get(
                    f"/api/sessions/{session_id}/messages", headers=AUTH
                ).json()
                self.assertEqual(len(messages), 2)
                self.assertEqual(messages[0]["content"], "a" * limit)

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


class MemoryChatStoreConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_origin_is_atomic_and_scoped_per_user(self):
        store = MemoryChatStore()
        results = await asyncio.gather(
            *(store.get_or_create_session("user", "first", "origin") for _ in range(20))
        )
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({session.id for session, _ in results}), 1)
        first = results[0][0]
        await store.add_message("user", first.id, "user", "preserved")
        existing, created = await store.get_or_create_session("user", "changed", "origin")
        self.assertFalse(created)
        self.assertEqual(existing.title, "first")
        self.assertEqual(existing.message_count, 1)
        other, created = await store.get_or_create_session("other", "first", "origin")
        self.assertTrue(created)
        self.assertNotEqual(first.id, other.id)
        await store.delete_session("user", first.id)
        rebuilt, created = await store.get_or_create_session("user", "first", "origin")
        self.assertTrue(created)
        self.assertNotEqual(first.id, rebuilt.id)

    async def test_concurrent_creates_enforce_raw_limit_and_clean_messages(self):
        store = MemoryChatStore()
        oldest = await store.create_session("user", "oldest")
        await store.add_message("user", oldest.id, "user", "remove me")
        results = await asyncio.gather(
            *(
                store.get_or_create_session("user", "session", f"origin-{index}")
                for index in range(MAX_SESSIONS_PER_USER + 10)
            )
        )
        self.assertTrue(all(created for _, created in results))
        self.assertEqual(len(store._sessions["user"]), MAX_SESSIONS_PER_USER)
        self.assertNotIn(oldest.id, store._sessions["user"])
        self.assertNotIn(("user", oldest.id), store._messages)
