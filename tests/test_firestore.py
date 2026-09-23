import asyncio
import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from google.api_core.exceptions import Aborted
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore

from src.application.models.chat import ToolCall
from src.application.services.chat_store import MAX_SESSIONS_PER_USER
from src.infrastructure.firebase.chat_store import FirestoreChatStore


class FirestoreTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_reads_before_writes_and_cleans_only_committed_victims(self):
        client = firestore.AsyncClient(
            project="demo-nthu-chatbot", credentials=AnonymousCredentials()
        )
        self.addCleanup(client.close)
        store = FirestoreChatStore(client)
        user = client.collection("users").document("user")
        sessions = user.collection("sessions")
        markers = user.collection("session_cleanup")
        marker_ref = markers.document("committed-victim")
        marker = Mock(id=marker_ref.id, reference=marker_ref)
        messages = sessions.document("committed-victim").collection("messages")
        transaction = client.transaction()
        now = datetime.now(UTC)
        victims = [
            Mock(
                id=name,
                reference=sessions.document(name),
                to_dict=Mock(return_value={"title": name, "updated_at": now}),
            )
            for name in ("aborted-victim", "committed-victim")
        ]
        attempt = 0

        async def begin(retry_id=None):
            nonlocal attempt
            attempt += 1
            transaction._id = f"attempt-{attempt}".encode()

        async def read_user(**kwargs):
            self.assertIs(kwargs["transaction"], transaction)
            self.assertEqual(transaction._write_pbs, [])

        async def stream(**kwargs):
            self.assertIs(kwargs["transaction"], transaction)
            self.assertEqual(transaction._write_pbs, [])
            yield victims[attempt - 1]

        async def stream_markers():
            yield marker

        cleanup_query = Mock(stream=Mock(side_effect=stream_markers))
        with (
            patch("src.infrastructure.firebase.chat_store.MAX_SESSIONS_PER_USER", 1),
            patch.object(store, "_user", return_value=user),
            patch.object(store, "_sessions", return_value=sessions),
            patch.object(store, "_session_cleanup", return_value=markers),
            patch.object(store, "_messages", return_value=messages),
            patch.object(user, "get", side_effect=read_user) as get_user,
            patch.object(sessions, "stream", side_effect=stream),
            patch.object(
                markers,
                "order_by",
                return_value=Mock(limit=Mock(return_value=cleanup_query)),
            ),
            patch.object(
                messages, "limit", return_value=Mock(get=AsyncMock(return_value=[]))
            ),
            patch.object(marker_ref, "delete", new_callable=AsyncMock) as delete_marker,
            patch.object(client, "transaction", return_value=transaction),
            patch.object(transaction, "_begin", side_effect=begin),
            patch.object(
                transaction, "_commit", side_effect=[Aborted("retry"), None]
            ) as commit,
            patch.object(client, "recursive_delete", new_callable=AsyncMock) as cleanup,
        ):
            session, created = await store.get_or_create_session("user", "new", "origin")
        self.assertTrue(created)
        self.assertEqual(session.origin, "origin")
        self.assertEqual(get_user.await_count, 2)
        self.assertEqual(commit.await_count, 2)
        cleanup.assert_awaited_once_with(victims[1].reference.collection("messages"))
        delete_marker.assert_awaited_once()
        writes = transaction._write_pbs
        self.assertEqual(len(writes), 4)
        self.assertEqual(writes[0].update.name, user._document_path)
        self.assertEqual(writes[1].delete, victims[1].reference._document_path)
        self.assertEqual(writes[2].update.name, marker_ref._document_path)
        self.assertEqual(
            writes[3].update.name, sessions.document(session.id)._document_path
        )

    async def test_failed_cleanup_is_retried_when_existing_origin_is_reopened(self):
        client = Mock()
        client.recursive_delete = AsyncMock(side_effect=[RuntimeError("private"), None])
        store = FirestoreChatStore(client)
        session = Mock(
            id="existing-session",
            to_dict=Mock(return_value={"title": "kept", "origin": "origin"}),
        )
        marker = Mock(id="evicted-session")
        marker.reference.delete = AsyncMock()
        messages = Mock()
        messages.limit.return_value.get = AsyncMock(return_value=[])
        transaction = Mock()

        async def stream_sessions(**kwargs):
            yield session

        async def stream_markers():
            yield marker

        def transactional(callback):
            return callback

        client.transaction.return_value = transaction
        user = Mock(get=AsyncMock())
        sessions = Mock(stream=Mock(side_effect=stream_sessions))
        markers = Mock()
        query = markers.order_by.return_value.limit.return_value
        query.stream.side_effect = stream_markers
        with (
            patch(
                "src.infrastructure.firebase.chat_store.firestore.async_transactional",
                side_effect=transactional,
            ),
            patch("src.infrastructure.firebase.chat_store.logger") as log,
            patch.object(store, "_user", return_value=user),
            patch.object(store, "_sessions", return_value=sessions),
            patch.object(store, "_session_cleanup", return_value=markers),
            patch.object(store, "_messages", return_value=messages),
        ):
            first, created = await store.get_or_create_session("user", "ignored", "origin")
            self.assertFalse(created)
            self.assertEqual(first.id, session.id)
            marker.reference.delete.assert_not_awaited()
            log.warning.assert_called_once_with(
                "Session cleanup deferred: {}", "RuntimeError"
            )
            reopened, created = await store.get_or_create_session(
                "user", "ignored", "origin"
            )
        self.assertFalse(created)
        self.assertEqual(reopened.id, first.id)
        self.assertEqual(client.recursive_delete.await_count, 2)
        marker.reference.delete.assert_awaited_once()
        transaction.set.assert_not_called()
        transaction.delete.assert_not_called()

    async def test_incomplete_bulk_cleanup_retains_marker(self):
        client = Mock(recursive_delete=AsyncMock())
        store = FirestoreChatStore(client)
        marker = Mock(id="evicted-session")
        marker.reference.delete = AsyncMock()
        messages = Mock()
        messages.limit.return_value.get = AsyncMock(return_value=[Mock()])
        markers = Mock()

        async def stream_markers():
            yield marker

        query = markers.order_by.return_value.limit.return_value
        query.stream.side_effect = stream_markers
        with (
            patch.object(store, "_session_cleanup", return_value=markers),
            patch.object(store, "_messages", return_value=messages),
        ):
            await store._drain_session_cleanup("user")
        marker.reference.delete.assert_not_awaited()
        client.recursive_delete.assert_awaited_once_with(messages)


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

    async def raw_session_ids(self):
        return {
            doc.id
            async for doc in self.client.collection("users")
            .document(self.user_id)
            .collection("sessions")
            .stream()
        }

    async def concurrent_creates(self, origins):
        clients = [
            firestore.AsyncClient(project="demo-nthu-chatbot") for _ in origins
        ]
        try:
            return await asyncio.gather(
                *(
                    FirestoreChatStore(client).get_or_create_session(
                        self.user_id, "concurrent", origin
                    )
                    for client, origin in zip(clients, origins, strict=True)
                )
            )
        finally:
            for client in clients:
                client.close()

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
        self.assertEqual(len(await self.raw_session_ids()), MAX_SESSIONS_PER_USER)

    async def test_concurrent_origin_creation_across_clients(self):
        results = await self.concurrent_creates(["same-origin"] * 4)
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(len({session.id for session, _ in results}), 1)
        self.assertEqual(len(await self.raw_session_ids()), 1)

    async def test_existing_legacy_origin_does_not_evict_at_capacity(self):
        reference = self.client.collection("users").document(self.user_id)
        batch = self.client.batch()
        now = datetime.now(UTC)
        for index in range(MAX_SESSIONS_PER_USER):
            batch.set(
                reference.collection("sessions").document(f"legacy-{index}"),
                {
                    "title": "legacy",
                    "origin": f"legacy-origin-{index}",
                    "created_at": now,
                    "updated_at": now,
                    "message_count": 0,
                },
            )
        await batch.commit()
        # Legacy session documents need not have an existing parent user document.
        self.assertFalse((await reference.get()).exists)
        results = await self.concurrent_creates(["legacy-origin-0"] * 4)
        self.assertTrue(all(not created for _, created in results))
        self.assertEqual({session.id for session, _ in results}, {"legacy-0"})
        self.assertEqual(len(await self.raw_session_ids()), MAX_SESSIONS_PER_USER)

    async def test_concurrent_creates_atomically_evict_and_insert(self):
        reference = self.client.collection("users").document(self.user_id)
        batch = self.client.batch()
        now = datetime.now(UTC) - timedelta(days=1)
        for index in range(MAX_SESSIONS_PER_USER):
            session = reference.collection("sessions").document(f"legacy-{index}")
            batch.set(
                session,
                {
                    "title": "legacy",
                    "created_at": now,
                    "updated_at": now + timedelta(seconds=index),
                    "message_count": 1,
                },
            )
            batch.set(
                session.collection("messages").document("message"),
                {"role": "user", "content": "old", "created_at": now},
            )
        await batch.commit()
        results = await self.concurrent_creates([None, "event-1", "event-2", None])
        self.assertTrue(all(created for _, created in results))
        ids = await self.raw_session_ids()
        self.assertEqual(len(ids), MAX_SESSIONS_PER_USER)
        self.assertTrue({session.id for session, _ in results}.issubset(ids))
        for index in range(4):
            self.assertNotIn(f"legacy-{index}", ids)
            self.assertEqual(
                await self.store.list_messages(self.user_id, f"legacy-{index}", 10), []
            )
        self.assertEqual(
            len(await self.store.list_messages(self.user_id, "legacy-4", 10)), 1
        )

    async def test_eviction_cleanup_cannot_delete_recreated_origin(self):
        with patch("src.infrastructure.firebase.chat_store.MAX_SESSIONS_PER_USER", 1):
            first = await self.store.create_session(self.user_id, "first", "origin")
            await self.store.add_message(self.user_id, first.id, "user", "old")
            cleanup_started = asyncio.Event()
            resume_cleanup = asyncio.Event()
            recursive_delete = self.client.recursive_delete

            async def delayed_cleanup(reference):
                cleanup_started.set()
                await resume_cleanup.wait()
                return await recursive_delete(reference)

            other_client = firestore.AsyncClient(project="demo-nthu-chatbot")
            try:
                with patch.object(
                    self.client, "recursive_delete", side_effect=delayed_cleanup
                ):
                    pending = asyncio.create_task(
                        self.store.create_session(self.user_id, "replacement")
                    )
                    try:
                        await asyncio.wait_for(cleanup_started.wait(), timeout=30)
                        other_store = FirestoreChatStore(other_client)
                        recreated, created = await other_store.get_or_create_session(
                            self.user_id, "recreated", "origin"
                        )
                        self.assertTrue(created)
                        self.assertNotEqual(first.id, recreated.id)
                        await other_store.add_message(
                            self.user_id, recreated.id, "user", "keep"
                        )
                    finally:
                        resume_cleanup.set()
                        await pending
                self.assertEqual(await self.raw_session_ids(), {recreated.id})
                self.assertEqual(
                    [
                        message.content
                        for message in await self.store.list_messages(
                            self.user_id, recreated.id, 10
                        )
                    ],
                    ["keep"],
                )
                self.assertEqual(
                    await self.store.list_messages(self.user_id, first.id, 10), []
                )
            finally:
                other_client.close()

    async def test_cleanup_failure_persists_marker_and_existing_origin_retries(self):
        with patch("src.infrastructure.firebase.chat_store.MAX_SESSIONS_PER_USER", 1):
            first = await self.store.create_session(self.user_id, "old", "old-origin")
            await self.store.add_message(self.user_id, first.id, "user", "remove me")
            marker = (
                self.client.collection("users")
                .document(self.user_id)
                .collection("session_cleanup")
                .document(first.id)
            )
            with patch.object(
                self.client, "recursive_delete", side_effect=RuntimeError("unavailable")
            ):
                replacement, created = await self.store.get_or_create_session(
                    self.user_id, "new", "new-origin"
                )
            self.assertTrue(created)
            self.assertIsNone(await self.store.get_session(self.user_id, first.id))
            self.assertTrue((await marker.get()).exists)
            self.assertEqual(
                len(await self.store.list_messages(self.user_id, first.id, 10)), 1
            )
            # A fresh store can recover the durable marker without creating a session.
            reopened, created = (
                await FirestoreChatStore(self.client).get_or_create_session(
                    self.user_id, "ignored", "new-origin"
                )
            )
            self.assertFalse(created)
            self.assertEqual(reopened.id, replacement.id)
            self.assertEqual(await self.raw_session_ids(), {replacement.id})
            self.assertFalse((await marker.get()).exists)
            self.assertEqual(
                await self.store.list_messages(self.user_id, first.id, 10), []
            )

    async def test_daily_quota_boundary(self):
        results = [await self.store.consume_daily_quota(self.user_id, 2) for _ in range(3)]
        self.assertEqual(results, [True, True, False])
        self.assertTrue(await self.store.consume_daily_quota(self.other_id, 2))

    async def test_users_are_isolated(self):
        session = await self.store.create_session(self.user_id, "mine")
        self.assertIsNone(await self.store.get_session(self.other_id, session.id))
        self.assertEqual(await self.store.list_sessions(self.other_id), [])
