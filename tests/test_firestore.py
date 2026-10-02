import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from google.api_core.exceptions import Aborted
from google.auth.credentials import AnonymousCredentials
from google.cloud import firestore

from src.application.models.chat import ToolCall
from src.application.services.chat_store import MAX_SESSIONS_PER_USER
from src.infrastructure.firebase.chat_store import FirestoreChatStore

PROJECT = "demo-nthu-chatbot"
MAX_SESSIONS_PATH = "src.infrastructure.firebase.chat_store.MAX_SESSIONS_PER_USER"


# -- 不需要 emulator：以 mock 驗證交易與清理流程 --
async def test_retry_reads_before_writes_and_cleans_only_committed_victims():
    client = firestore.AsyncClient(project=PROJECT, credentials=AnonymousCredentials())
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
        assert kwargs["transaction"] is transaction
        assert transaction._write_pbs == []

    async def stream(**kwargs):
        assert kwargs["transaction"] is transaction
        assert transaction._write_pbs == []
        yield victims[attempt - 1]

    async def stream_markers():
        yield marker

    cleanup_query = Mock(stream=Mock(side_effect=stream_markers))
    try:
        with (
            patch(MAX_SESSIONS_PATH, 1),
            patch.object(store, "_user", return_value=user),
            patch.object(store, "_sessions", return_value=sessions),
            patch.object(store, "_session_cleanup", return_value=markers),
            patch.object(store, "_messages", return_value=messages),
            patch.object(user, "get", side_effect=read_user) as get_user,
            patch.object(sessions, "stream", side_effect=stream),
            patch.object(
                markers, "order_by", return_value=Mock(limit=Mock(return_value=cleanup_query))
            ),
            patch.object(messages, "limit", return_value=Mock(get=AsyncMock(return_value=[]))),
            patch.object(marker_ref, "delete", new_callable=AsyncMock) as delete_marker,
            patch.object(client, "transaction", return_value=transaction),
            patch.object(transaction, "_begin", side_effect=begin),
            patch.object(transaction, "_commit", side_effect=[Aborted("retry"), None]) as commit,
            patch.object(client, "recursive_delete", new_callable=AsyncMock) as cleanup,
        ):
            session, created = await store.get_or_create_session("user", "new", "origin")
    finally:
        client.close()
    assert created
    assert session.origin == "origin"
    assert get_user.await_count == 2
    assert commit.await_count == 2
    cleanup.assert_awaited_once_with(victims[1].reference.collection("messages"))
    delete_marker.assert_awaited_once()
    writes = transaction._write_pbs
    assert len(writes) == 4
    assert writes[0].update.name == user._document_path
    assert writes[1].delete == victims[1].reference._document_path
    assert writes[2].update.name == marker_ref._document_path
    assert writes[3].update.name == sessions.document(session.id)._document_path


async def test_failed_cleanup_is_retried_when_existing_origin_is_reopened():
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

    client.transaction.return_value = transaction
    user = Mock(get=AsyncMock())
    sessions = Mock(stream=Mock(side_effect=stream_sessions))
    markers = Mock()
    markers.order_by.return_value.limit.return_value.stream.side_effect = stream_markers
    with (
        patch(
            "src.infrastructure.firebase.chat_store.firestore.async_transactional",
            side_effect=lambda callback: callback,
        ),
        patch("src.infrastructure.firebase.chat_store.logger") as log,
        patch.object(store, "_user", return_value=user),
        patch.object(store, "_sessions", return_value=sessions),
        patch.object(store, "_session_cleanup", return_value=markers),
        patch.object(store, "_messages", return_value=messages),
    ):
        first, created = await store.get_or_create_session("user", "ignored", "origin")
        assert not created
        assert first.id == session.id
        marker.reference.delete.assert_not_awaited()
        log.warning.assert_called_once_with("Session cleanup deferred: {}", "RuntimeError")
        reopened, created = await store.get_or_create_session("user", "ignored", "origin")
    assert not created
    assert reopened.id == first.id
    assert client.recursive_delete.await_count == 2
    marker.reference.delete.assert_awaited_once()
    transaction.set.assert_not_called()
    transaction.delete.assert_not_called()


async def test_incomplete_bulk_cleanup_retains_marker():
    client = Mock(recursive_delete=AsyncMock())
    store = FirestoreChatStore(client)
    marker = Mock(id="evicted-session")
    marker.reference.delete = AsyncMock()
    messages = Mock()
    messages.limit.return_value.get = AsyncMock(return_value=[Mock()])
    markers = Mock()

    async def stream_markers():
        yield marker

    markers.order_by.return_value.limit.return_value.stream.side_effect = stream_markers
    with (
        patch.object(store, "_session_cleanup", return_value=markers),
        patch.object(store, "_messages", return_value=messages),
    ):
        await store._drain_session_cleanup("user")
    marker.reference.delete.assert_not_awaited()
    client.recursive_delete.assert_awaited_once_with(messages)


# -- 需要 Firestore emulator --
@pytest.fixture
async def emulator():
    """emulator 上的 client、store 與兩個隨機使用者；結束時刪除測試資料。"""
    client = firestore.AsyncClient(project=PROJECT)
    env = Mock(
        client=client,
        store=FirestoreChatStore(client),
        user_id="test-" + uuid4().hex,
        other_id="test-" + uuid4().hex,
    )
    yield env
    for user_id in (env.user_id, env.other_id):
        await client.recursive_delete(client.collection("users").document(user_id))
    client.close()


async def user_doc(env, user_id=None) -> dict:
    snapshot = await env.client.collection("users").document(user_id or env.user_id).get()
    return snapshot.to_dict() or {}


async def raw_session_ids(env) -> set[str]:
    sessions = env.client.collection("users").document(env.user_id).collection("sessions")
    return {doc.id async for doc in sessions.stream()}


async def concurrent_creates(env, origins):
    clients = [firestore.AsyncClient(project=PROJECT) for _ in origins]
    try:
        return await asyncio.gather(
            *(
                FirestoreChatStore(client).get_or_create_session(
                    env.user_id, "concurrent", origin
                )
                for client, origin in zip(clients, origins, strict=True)
            )
        )
    finally:
        for client in clients:
            client.close()


async def seed_sessions(env, *, updated_at, with_messages=False, with_origin=False):
    """直接寫入滿額的舊對話，模擬已存在的資料。"""
    reference = env.client.collection("users").document(env.user_id)
    batch = env.client.batch()
    for index in range(MAX_SESSIONS_PER_USER):
        session = reference.collection("sessions").document(f"legacy-{index}")
        payload = {
            "title": "legacy",
            "created_at": updated_at,
            "updated_at": updated_at + timedelta(seconds=index),
            "message_count": 1 if with_messages else 0,
        }
        if with_origin:
            payload["origin"] = f"legacy-origin-{index}"
        batch.set(session, payload)
        if with_messages:
            batch.set(
                session.collection("messages").document("message"),
                {"role": "user", "content": "old", "created_at": updated_at},
            )
    await batch.commit()
    return reference


@pytest.mark.firestore
async def test_touch_user_merges_without_clobbering(emulator):
    store = emulator.store
    await store.touch_user(emulator.user_id, followed=True)
    await store.touch_user(emulator.user_id, display_name="小明")
    await store.touch_user(emulator.user_id)
    data = await user_doc(emulator)
    assert data["followed"]
    assert data["display_name"] == "小明"
    assert isinstance(data["last_seen_at"], datetime)
    assert isinstance(data["created_at"], datetime)
    await store.touch_user(emulator.user_id, followed=False)
    assert not (await user_doc(emulator))["followed"]


@pytest.mark.firestore
async def test_session_lifecycle_and_ordering(emulator):
    store, uid = emulator.store, emulator.user_id
    first = await store.create_session(uid, "第一")
    second = await store.create_session(uid, "第二")
    assert [s.id for s in await store.list_sessions(uid)] == [second.id, first.id]

    await store.add_message(uid, first.id, "user", "hi")
    assert [s.id for s in await store.list_sessions(uid)] == [first.id, second.id]
    assert (await store.get_session(uid, first.id)).message_count == 1

    await store.rename_session(uid, first.id, "改名")
    assert (await store.get_session(uid, first.id)).title == "改名"

    await store.delete_session(uid, first.id)
    assert await store.get_session(uid, first.id) is None
    assert await store.list_messages(uid, first.id, 10) == []


@pytest.mark.firestore
async def test_messages_order_limit_and_tool_calls(emulator):
    store, uid = emulator.store, emulator.user_id
    session = await store.create_session(uid, "chat")
    for index in range(5):
        await store.add_message(uid, session.id, "user", str(index))
    await store.add_message(
        uid,
        session.id,
        "assistant",
        "done",
        [ToolCall(name="get_next_buses", args={"route": "main"}, ok=True)],
    )
    messages = await store.list_messages(uid, session.id, 3)
    assert [m.content for m in messages] == ["3", "4", "done"]
    assert messages[-1].tool_calls[0].name == "get_next_buses"


@pytest.mark.firestore
async def test_find_session_by_origin(emulator):
    store, uid = emulator.store, emulator.user_id
    assert await store.find_session_by_origin(uid, "ev-1") is None
    session = await store.create_session(uid, "bubble", origin="ev-1")
    found = await store.find_session_by_origin(uid, "ev-1")
    assert found.id == session.id
    assert found.origin == "ev-1"
    assert await store.find_session_by_origin(emulator.other_id, "ev-1") is None
    await store.delete_session(uid, session.id)
    assert await store.find_session_by_origin(uid, "ev-1") is None


@pytest.mark.firestore
async def test_session_limit_evicts_least_recently_updated(emulator):
    store, uid = emulator.store, emulator.user_id
    first = await store.create_session(uid, "first")
    for _ in range(MAX_SESSIONS_PER_USER - 1):
        await store.create_session(uid, "s")
    await store.add_message(uid, first.id, "user", "keep me")
    oldest = (await store.list_sessions(uid))[-1]

    newest = await store.create_session(uid, "one more")
    ids = [s.id for s in await store.list_sessions(uid)]
    assert len(ids) == MAX_SESSIONS_PER_USER
    assert newest.id in ids
    assert first.id in ids
    assert oldest.id not in ids
    assert await store.get_session(uid, oldest.id) is None
    assert len(await raw_session_ids(emulator)) == MAX_SESSIONS_PER_USER


@pytest.mark.firestore
async def test_concurrent_origin_creation_across_clients(emulator):
    results = await concurrent_creates(emulator, ["same-origin"] * 4)
    assert sum(created for _, created in results) == 1
    assert len({session.id for session, _ in results}) == 1
    assert len(await raw_session_ids(emulator)) == 1


@pytest.mark.firestore
async def test_existing_legacy_origin_does_not_evict_at_capacity(emulator):
    reference = await seed_sessions(emulator, updated_at=datetime.now(UTC), with_origin=True)
    # 舊對話文件不一定有上層的 user 文件
    assert not (await reference.get()).exists
    results = await concurrent_creates(emulator, ["legacy-origin-0"] * 4)
    assert all(not created for _, created in results)
    assert {session.id for session, _ in results} == {"legacy-0"}
    assert len(await raw_session_ids(emulator)) == MAX_SESSIONS_PER_USER


@pytest.mark.firestore
async def test_concurrent_creates_atomically_evict_and_insert(emulator):
    await seed_sessions(
        emulator, updated_at=datetime.now(UTC) - timedelta(days=1), with_messages=True
    )
    results = await concurrent_creates(emulator, [None, "event-1", "event-2", None])
    assert all(created for _, created in results)
    ids = await raw_session_ids(emulator)
    assert len(ids) == MAX_SESSIONS_PER_USER
    assert {session.id for session, _ in results} <= ids
    for index in range(4):
        assert f"legacy-{index}" not in ids
        assert await emulator.store.list_messages(emulator.user_id, f"legacy-{index}", 10) == []
    assert len(await emulator.store.list_messages(emulator.user_id, "legacy-4", 10)) == 1


@pytest.mark.firestore
async def test_eviction_cleanup_cannot_delete_recreated_origin(emulator):
    store, uid, client = emulator.store, emulator.user_id, emulator.client
    with patch(MAX_SESSIONS_PATH, 1):
        first = await store.create_session(uid, "first", "origin")
        await store.add_message(uid, first.id, "user", "old")
        cleanup_started = asyncio.Event()
        resume_cleanup = asyncio.Event()
        recursive_delete = client.recursive_delete

        async def delayed_cleanup(reference):
            cleanup_started.set()
            await resume_cleanup.wait()
            return await recursive_delete(reference)

        other_client = firestore.AsyncClient(project=PROJECT)
        try:
            with patch.object(client, "recursive_delete", side_effect=delayed_cleanup):
                pending = asyncio.create_task(store.create_session(uid, "replacement"))
                try:
                    await asyncio.wait_for(cleanup_started.wait(), timeout=30)
                    other_store = FirestoreChatStore(other_client)
                    recreated, created = await other_store.get_or_create_session(
                        uid, "recreated", "origin"
                    )
                    assert created
                    assert first.id != recreated.id
                    await other_store.add_message(uid, recreated.id, "user", "keep")
                finally:
                    resume_cleanup.set()
                    await pending
            assert await raw_session_ids(emulator) == {recreated.id}
            kept = await store.list_messages(uid, recreated.id, 10)
            assert [message.content for message in kept] == ["keep"]
            assert await store.list_messages(uid, first.id, 10) == []
        finally:
            other_client.close()


@pytest.mark.firestore
async def test_cleanup_failure_persists_marker_and_existing_origin_retries(emulator):
    store, uid, client = emulator.store, emulator.user_id, emulator.client
    with patch(MAX_SESSIONS_PATH, 1):
        first = await store.create_session(uid, "old", "old-origin")
        await store.add_message(uid, first.id, "user", "remove me")
        marker = (
            client.collection("users").document(uid).collection("session_cleanup").document(first.id)
        )
        with patch.object(client, "recursive_delete", side_effect=RuntimeError("unavailable")):
            replacement, created = await store.get_or_create_session(uid, "new", "new-origin")
        assert created
        assert await store.get_session(uid, first.id) is None
        assert (await marker.get()).exists
        assert len(await store.list_messages(uid, first.id, 10)) == 1
        # 新的 store 也能從持久化的 marker 接手清理，而且不會建立新對話
        reopened, created = await FirestoreChatStore(client).get_or_create_session(
            uid, "ignored", "new-origin"
        )
        assert not created
        assert reopened.id == replacement.id
        assert await raw_session_ids(emulator) == {replacement.id}
        assert not (await marker.get()).exists
        assert await store.list_messages(uid, first.id, 10) == []


@pytest.mark.firestore
async def test_daily_quota_boundary(emulator):
    store = emulator.store
    results = [await store.consume_daily_quota(emulator.user_id, 2) for _ in range(3)]
    assert results == [True, True, False]
    assert await store.consume_daily_quota(emulator.other_id, 2)


@pytest.mark.firestore
async def test_users_are_isolated(emulator):
    store = emulator.store
    session = await store.create_session(emulator.user_id, "mine")
    assert await store.get_session(emulator.other_id, session.id) is None
    assert await store.list_sessions(emulator.other_id) == []
