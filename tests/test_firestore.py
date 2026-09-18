import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from google.cloud import firestore

from src.infrastructure.firebase.user_repository import (
    ConversationBusyError,
    UserRepository,
)


@unittest.skipUnless(
    os.getenv("FIRESTORE_EMULATOR_HOST"), "Firestore emulator required"
)
class FirestoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = firestore.AsyncClient(project="demo-nthu-chatbot")
        self.users = UserRepository(self.client)
        self.user_id = "test-" + uuid4().hex
        self.other_id = "test-" + uuid4().hex

    async def asyncTearDown(self):
        for collection in ("users", "conversations"):
            for user_id in (self.user_id, self.other_id):
                await self.client.collection(collection).document(user_id).delete()
        self.client.close()

    async def test_persistence_history_bound_redelivery_and_user_isolation(self):
        token, history, cached = await self.users.begin(self.user_id, "event-1")
        self.assertEqual(history, [])
        self.assertIsNone(cached)
        messages = [{"role": "user", "content": str(index)} for index in range(30)]
        await self.users.finish(self.user_id, token, "event-1", messages, "Answer")
        self.assertEqual(
            await self.users.begin(self.user_id, "event-1"), (None, [], "Answer")
        )
        _, history, _ = await UserRepository(self.client).begin(self.user_id, "event-2")
        self.assertEqual(history, messages[-20:])
        _, other_history, _ = await self.users.begin(self.other_id, "event-1")
        self.assertEqual(other_history, [])

    async def test_transaction_lease_blocks_other_client_and_checks_owner(self):
        token, _, _ = await self.users.begin(self.user_id, "event-1")
        other_client = firestore.AsyncClient(project="demo-nthu-chatbot")
        try:
            with self.assertRaises(ConversationBusyError):
                await UserRepository(other_client).begin(self.user_id, "event-2")
        finally:
            other_client.close()
        with self.assertRaises(ConversationBusyError):
            await self.users.finish(self.user_id, "wrong-token", "event-1", [], "Wrong")
        await self.users.release(self.user_id, "wrong-token")
        with self.assertRaises(ConversationBusyError):
            await self.users.begin(self.user_id, "event-2")
        await self.users.release(self.user_id, token)
        next_token, _, _ = await self.users.begin(self.user_id, "event-2")
        self.assertNotEqual(token, next_token)

    async def test_expired_history_is_not_revived_after_failed_request(self):
        await (
            self.client.collection("conversations")
            .document(self.user_id)
            .set(
                {
                    "history": [{"role": "user", "content": "Expired"}],
                    "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1),
                }
            )
        )
        token, history, _ = await self.users.begin(self.user_id, "event-1")
        self.assertEqual(history, [])
        await self.users.release(self.user_id, token)
        _, history, _ = await self.users.begin(self.user_id, "event-2")
        self.assertEqual(history, [])

    async def test_profile_merge_preserves_follow_status(self):
        await self.users.touch(self.user_id, followed=True)
        await self.users.touch(self.user_id)
        data = (
            await self.client.collection("users").document(self.user_id).get()
        ).to_dict()
        self.assertTrue(data["followed"])
        self.assertIsInstance(data["last_seen_at"], datetime)
        await self.users.touch(self.user_id, followed=False)
        data = (
            await self.client.collection("users").document(self.user_id).get()
        ).to_dict()
        self.assertFalse(data["followed"])
