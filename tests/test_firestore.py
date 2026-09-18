import os
import unittest
from datetime import datetime
from uuid import uuid4

from google.cloud import firestore

from src.application.models.chat import ToolCall
from src.application.services.chat_store import MAX_SESSIONS_PER_USER
from src.infrastructure.firebase.chat_store import FirestoreChatStore


@unittest.skipUnless(
    os.getenv("FIRESTORE_EMULATOR_HOST"), "Firestore emulator required"
)
class FirestoreChatStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = firestore.AsyncClient(project="demo-nthu-chatbot")
        self.store = FirestoreChatStore(self.client)
        self.user_id = "test-" + uuid4().hex
        self.other_id = "test-" + uuid4().hex

    async def asyncTearDown(self):
        for user_id in (self.user_id, self.other_id):
            await self.client.recursive_delete(
                self.client.collection("users").document(user_id)
            )
        self.client.close()

    async def user_doc(self, user_id=None) -> dict:
        snapshot = await self.client.collection("users").document(user_id or self.user_id).get()
        return snapshot.to_dict() or {}

    async def test_touch_user_merges_without_clobbering(self):
        await self.store.touch_user(self.user_id, followed=True)
        await self.store.touch_user(self.user_id, display_name="小明")
        await self.store.touch_user(self.user_id)
        data = await self.user_doc()
        self.assertTrue(data["followed"])
        self.assertEqual(data["display_name"], "小明")
        self.assertIsInstance(data["last_seen_at"], datetime)
        self.assertIsInstance(data["created_at"], datetime)
        await self.store.touch_user(self.user_id, followed=False)
        self.assertFalse((await self.user_doc())["followed"])

    async def test_session_lifecycle_and_ordering(self):
        first = await self.store.create_session(self.user_id, "第一")
        second = await self.store.create_session(self.user_id, "第二")
        self.assertEqual(
            [s.id for s in await self.store.list_sessions(self.user_id)],
            [second.id, first.id],
        )

        await self.store.add_message(self.user_id, first.id, "user", "hi")
        self.assertEqual(
            [s.id for s in await self.store.list_sessions(self.user_id)],
            [first.id, second.id],
        )
        refreshed = await self.store.get_session(self.user_id, first.id)
        self.assertEqual(refreshed.message_count, 1)

        await self.store.rename_session(self.user_id, first.id, "改名")
        self.assertEqual((await self.store.get_session(self.user_id, first.id)).title, "改名")

        await self.store.delete_session(self.user_id, first.id)
        self.assertIsNone(await self.store.get_session(self.user_id, first.id))
        self.assertEqual(await self.store.list_messages(self.user_id, first.id, 10), [])

    async def test_messages_order_limit_and_tool_calls(self):
        session = await self.store.create_session(self.user_id, "chat")
        for index in range(5):
            await self.store.add_message(self.user_id, session.id, "user", str(index))
        await self.store.add_message(
            self.user_id,
            session.id,
            "assistant",
            "done",
            [ToolCall(name="get_next_buses", args={"route": "main"}, ok=True)],
        )
        messages = await self.store.list_messages(self.user_id, session.id, 3)
        self.assertEqual([m.content for m in messages], ["3", "4", "done"])
        self.assertEqual(messages[-1].tool_calls[0].name, "get_next_buses")

    async def test_find_session_by_origin(self):
        self.assertIsNone(await self.store.find_session_by_origin(self.user_id, "ev-1"))
        session = await self.store.create_session(self.user_id, "bubble", origin="ev-1")
        found = await self.store.find_session_by_origin(self.user_id, "ev-1")
        self.assertEqual(found.id, session.id)
        self.assertEqual(found.origin, "ev-1")
        self.assertIsNone(await self.store.find_session_by_origin(self.other_id, "ev-1"))
        await self.store.delete_session(self.user_id, session.id)
        self.assertIsNone(await self.store.find_session_by_origin(self.user_id, "ev-1"))

    async def test_session_limit_evicts_least_recently_updated(self):
        first = await self.store.create_session(self.user_id, "first")
        for _ in range(MAX_SESSIONS_PER_USER - 1):
            await self.store.create_session(self.user_id, "s")
        await self.store.add_message(self.user_id, first.id, "user", "keep me")
        sessions = await self.store.list_sessions(self.user_id)
        oldest = sessions[-1]

        newest = await self.store.create_session(self.user_id, "one more")
        ids = [s.id for s in await self.store.list_sessions(self.user_id)]
        self.assertEqual(len(ids), MAX_SESSIONS_PER_USER)
        self.assertIn(newest.id, ids)
        self.assertIn(first.id, ids)
        self.assertNotIn(oldest.id, ids)
        self.assertIsNone(await self.store.get_session(self.user_id, oldest.id))

    async def test_daily_quota_boundary(self):
        results = [await self.store.consume_daily_quota(self.user_id, 2) for _ in range(3)]
        self.assertEqual(results, [True, True, False])
        self.assertTrue(await self.store.consume_daily_quota(self.other_id, 2))

    async def test_users_are_isolated(self):
        session = await self.store.create_session(self.user_id, "mine")
        self.assertIsNone(await self.store.get_session(self.other_id, session.id))
        self.assertEqual(await self.store.list_sessions(self.other_id), [])
