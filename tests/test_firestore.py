"""
Firestore 實作的測試。

大部分需要 Firestore emulator（`@pytest.mark.firestore`）；沒有 emulator 時只跑以 mock 驗證清理流程的測試。
"""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from src.application.models.chat import MessageMeta, TokenUsage, ToolCall
from src.application.models.identity import (
    AccountDisabledError,
    IdentityConflictError,
    LastIdentityError,
    Principal,
    VerifiedIdentity,
    lookup_key,
)
from src.application.services.chat_store import MAX_SESSIONS_PER_USER
from src.infrastructure.firebase.chat_store import FirestoreChatStore
from src.infrastructure.firebase.module_registry import FirestoreModuleRegistry
from src.infrastructure.firebase.user_store import FirestoreUserStore

PROJECT = "demo-nthu-chatbot"
MAX_SESSIONS_PATH = "src.infrastructure.firebase.chat_store.MAX_SESSIONS_PER_USER"


# -- 不需要 emulator --
async def test_incomplete_bulk_cleanup_retains_marker():
    client = Mock(recursive_delete=AsyncMock())
    store = FirestoreChatStore(client)
    marker = Mock(id="evicted")
    marker.reference.delete = AsyncMock()
    messages = Mock()
    messages.limit.return_value.get = AsyncMock(return_value=[Mock()])
    markers = Mock()

    async def stream_markers():
        yield marker

    markers.order_by.return_value.limit.return_value.stream.side_effect = stream_markers
    with (
        patch.object(store, "_cleanup", return_value=markers),
        patch.object(store, "_messages", return_value=messages),
    ):
        await store._drain_cleanup("usr_test")
    marker.reference.delete.assert_not_awaited()
    client.recursive_delete.assert_awaited_once_with(messages)


async def test_cleanup_failure_is_logged_by_type_only():
    client = Mock(recursive_delete=AsyncMock(side_effect=RuntimeError("private detail")))
    store = FirestoreChatStore(client)
    marker = Mock(id="evicted")
    marker.reference.delete = AsyncMock()
    markers = Mock()

    async def stream_markers():
        yield marker

    markers.order_by.return_value.limit.return_value.stream.side_effect = stream_markers
    with (
        patch.object(store, "_cleanup", return_value=markers),
        patch("src.infrastructure.firebase.chat_store.logger") as log,
    ):
        await store._drain_cleanup("usr_test")
    marker.reference.delete.assert_not_awaited()
    log.warning.assert_called_once_with("Conversation cleanup deferred: {}", "RuntimeError")


# -- 需要 Firestore emulator --
@pytest.fixture
async def db():
    client = firestore.AsyncClient(project=PROJECT)
    yield client
    client.close()


@pytest.fixture
def users(db):
    return FirestoreUserStore(db)


@pytest.fixture
def chats(db):
    return FirestoreChatStore(db)


def identity(provider="line", display_name="測試者") -> VerifiedIdentity:
    """每個測試用隨機的外部 ID，避免互相干擾。"""
    return VerifiedIdentity(
        provider=provider, provider_user_id="U" + uuid4().hex, display_name=display_name
    )


async def new_user(users) -> str:
    user_id, _ = await users.resolve_or_create(identity())
    return user_id


async def owned_ids(db, user_id) -> set[str]:
    query = db.collection("conversations").where(filter=FieldFilter("userId", "==", user_id))
    return {doc.id async for doc in query.stream()}


async def doc(db, path) -> dict | None:
    snapshot = await db.document(path).get()
    return snapshot.to_dict() if snapshot.exists else None


# users / identities
@pytest.mark.firestore
async def test_first_contact_writes_user_identity_and_hashed_lookup(db, users):
    who = identity()
    user_id, created = await users.resolve_or_create(who)
    assert created
    assert user_id.startswith("usr_")

    lookup = await doc(db, f"identityLookup/{lookup_key('line', who.provider_user_id)}")
    assert lookup["userId"] == user_id
    assert who.provider_user_id not in str(lookup)  # 原始外部 ID 不存在 lookup
    user = await doc(db, f"users/{user_id}")
    assert user["status"] == "active"
    assert user["displayName"] == "測試者"
    assert user["conversationCount"] == 0
    line = await doc(db, f"users/{user_id}/identities/line")
    assert line["providerUserId"] == who.provider_user_id

    again, created = await users.resolve_or_create(who)
    assert (again, created) == (user_id, False)


@pytest.mark.firestore
async def test_concurrent_first_contact_across_clients_creates_one_user(users):
    who = identity()
    clients = [firestore.AsyncClient(project=PROJECT) for _ in range(5)]
    try:
        results = await asyncio.gather(
            *(FirestoreUserStore(c).resolve_or_create(who) for c in clients)
        )
    finally:
        for c in clients:
            c.close()
    assert len({user_id for user_id, _ in results}) == 1
    assert sum(created for _, created in results) == 1


@pytest.mark.firestore
async def test_blocked_user_cannot_resolve(db, users):
    who = identity()
    user_id, _ = await users.resolve_or_create(who)
    await db.document(f"users/{user_id}").update({"status": "blocked"})
    with pytest.raises(AccountDisabledError):
        await users.resolve_or_create(who)
    assert await users.get_status(user_id) == "blocked"


@pytest.mark.firestore
async def test_linking_rules(db, users):
    alice = await new_user(users)
    bob = await new_user(users)
    google = identity(provider="google")

    await users.link_identity(alice, google)
    assert (await users.resolve_or_create(google))[0] == alice
    with pytest.raises(IdentityConflictError):
        await users.link_identity(bob, google)  # 已屬於 alice
    with pytest.raises(IdentityConflictError):
        await users.link_identity(alice, identity())  # alice 已有 line 身分

    await users.unlink_identity(alice, "google")
    with pytest.raises(LastIdentityError):
        await users.unlink_identity(alice, "line")
    assert await doc(db, f"identityLookup/{lookup_key('google', google.provider_user_id)}") is None
    audit = [d.to_dict() async for d in db.collection(f"users/{alice}/auditLog").stream()]
    assert sorted(entry["action"] for entry in audit) == ["link", "unlink"]
    assert all(isinstance(entry["expiresAt"], datetime) for entry in audit)


@pytest.mark.firestore
async def test_login_activity_and_module_use(db, users):
    user_id = await new_user(users)
    principal = Principal(user_id=user_id, provider="line", display_name="新名字")
    await users.record_login(principal, {"liff": {"os": "ios", "contextType": "utou"}})
    await users.update_identity_metadata(user_id, "line", {"followed": True})
    await users.record_module_use(user_id, "bus")
    await users.record_module_use(user_id, "bus")
    await users.touch_activity(user_id)

    user = await doc(db, f"users/{user_id}")
    assert user["displayName"] == "新名字"
    assert user["lastModuleId"] == "bus"
    assert isinstance(user["lastActiveAt"], datetime)
    line = await doc(db, f"users/{user_id}/identities/line")
    assert line["metadata"]["liff"] == {"os": "ios", "contextType": "utou"}
    assert line["metadata"]["followed"] is True
    assert line["metadata"]["displayName"] == "新名字"
    state = await doc(db, f"users/{user_id}/moduleStates/bus")
    assert state["usageCount"] == 2


@pytest.mark.firestore
async def test_daily_quota_boundary_and_ttl(db, users):
    alice = await new_user(users)
    bob = await new_user(users)
    assert [await users.consume_daily_quota(alice, 2) for _ in range(3)] == [True, True, False]
    assert await users.consume_daily_quota(bob, 2)
    usage = [d.to_dict() async for d in db.collection(f"users/{alice}/usage").stream()]
    assert usage[0]["count"] == 3
    assert usage[0]["expiresAt"] > datetime.now(UTC) + timedelta(days=7)


@pytest.mark.firestore
async def test_module_registry_reads_enabled_flag(db):
    registry = FirestoreModuleRegistry(db)
    module = "test-" + uuid4().hex
    assert await registry.is_enabled(module)  # 文件不存在：預設啟用
    await db.document(f"modules/{module}").set({"enabled": False})
    assert await registry.is_enabled(module)  # 60 秒快取
    registry._cache.clear()
    assert not await registry.is_enabled(module)


# conversations / messages
@pytest.mark.firestore
async def test_conversation_lifecycle_ordering_and_count(db, users, chats):
    user_id = await new_user(users)
    first = await chats.create_session(user_id, "第一")
    second = await chats.create_session(user_id, "第二")
    assert [s.id for s in await chats.list_sessions(user_id)] == [second.id, first.id]

    await chats.add_message(user_id, first.id, "user", "hi")
    assert [s.id for s in await chats.list_sessions(user_id)] == [first.id, second.id]
    assert (await chats.get_session(user_id, first.id)).message_count == 1

    await chats.rename_session(user_id, first.id, "改名")
    assert (await chats.get_session(user_id, first.id)).title == "改名"
    conversation = await doc(db, f"conversations/{first.id}")
    assert conversation["userId"] == user_id
    assert conversation["channel"] == "liff"
    assert (await doc(db, f"users/{user_id}"))["conversationCount"] == 2

    await chats.delete_session(user_id, first.id)
    assert await chats.get_session(user_id, first.id) is None
    assert [m async for m in db.collection(f"conversations/{first.id}/messages").stream()] == []
    assert (await doc(db, f"users/{user_id}"))["conversationCount"] == 1


@pytest.mark.firestore
async def test_messages_store_metadata_and_respect_limit(db, users, chats):
    user_id = await new_user(users)
    session = await chats.create_session(user_id, "chat")
    for index in range(5):
        await chats.add_message(user_id, session.id, "user", str(index))
    meta = MessageMeta(
        model="test-model",
        prompt_version="v-test",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5, reasoning_tokens=2, requests=1),
        latency_ms=1234,
    )
    saved = await chats.add_message(
        user_id, session.id, "assistant", "done",
        [ToolCall(name="get_next_buses", args={"route": "main"})], meta,
    )
    messages = await chats.list_messages(user_id, session.id, 3)
    assert [m.content for m in messages] == ["3", "4", "done"]
    assert messages[-1].tool_calls[0].name == "get_next_buses"
    stored = await doc(db, f"conversations/{session.id}/messages/{saved.id}")
    assert stored["model"] == "test-model"
    assert stored["promptVersion"] == "v-test"
    assert stored["tokenUsage"]["reasoning_tokens"] == 2
    assert stored["latencyMs"] == 1234
    assert stored["contentType"] == "text"


@pytest.mark.firestore
async def test_other_users_cannot_see_or_touch_a_conversation(db, users, chats):
    alice = await new_user(users)
    bob = await new_user(users)
    session = await chats.create_session(alice, "alice 的對話")
    await chats.add_message(alice, session.id, "user", "秘密")

    assert await chats.get_session(bob, session.id) is None
    assert await chats.list_messages(bob, session.id, 10) == []
    assert await chats.list_sessions(bob) == []
    await chats.rename_session(bob, session.id, "被改了")
    await chats.delete_session(bob, session.id)
    kept = await chats.get_session(alice, session.id)
    assert kept.title == "alice 的對話"
    assert len(await chats.list_messages(alice, session.id, 10)) == 1


@pytest.mark.firestore
async def test_origin_is_get_or_create_and_scoped_per_user(users, chats):
    alice = await new_user(users)
    bob = await new_user(users)
    first, created = await chats.get_or_create_session(alice, "泡泡", "ev-1")
    assert created
    again, created = await chats.get_or_create_session(alice, "ignored", "ev-1")
    assert (again.id, created) == (first.id, False)
    theirs, created = await chats.get_or_create_session(bob, "泡泡", "ev-1")
    assert created and theirs.id != first.id
    await chats.delete_session(alice, first.id)
    rebuilt, created = await chats.get_or_create_session(alice, "泡泡", "ev-1")
    assert created and rebuilt.id != first.id


@pytest.mark.firestore
async def test_limit_evicts_least_recently_updated(db, users, chats):
    user_id = await new_user(users)
    first = await chats.create_session(user_id, "first")
    for _ in range(MAX_SESSIONS_PER_USER - 1):
        await chats.create_session(user_id, "s")
    await chats.add_message(user_id, first.id, "user", "keep me")
    oldest = (await chats.list_sessions(user_id))[-1]

    newest = await chats.create_session(user_id, "one more")
    ids = await owned_ids(db, user_id)
    assert len(ids) == MAX_SESSIONS_PER_USER
    assert {newest.id, first.id} <= ids
    assert oldest.id not in ids
    assert (await doc(db, f"users/{user_id}"))["conversationCount"] == MAX_SESSIONS_PER_USER


@pytest.mark.firestore
async def test_concurrent_creates_at_capacity_evict_atomically(db, users):
    """原本會 lock timeout 的情境：交易不再讀取全部對話，只鎖 user 文件與被淘汰的那幾則。"""
    user_id = await new_user(users)
    with patch(MAX_SESSIONS_PATH, 5):
        seed = FirestoreChatStore(db)
        for _ in range(5):
            await seed.create_session(user_id, "old")
        clients = [firestore.AsyncClient(project=PROJECT) for _ in range(4)]
        try:
            results = await asyncio.gather(
                *(
                    FirestoreChatStore(c).get_or_create_session(user_id, "new", origin)
                    for c, origin in zip(clients, [None, "e1", "e2", None], strict=True)
                )
            )
        finally:
            for c in clients:
                c.close()
    assert all(created for _, created in results)
    ids = await owned_ids(db, user_id)
    assert len(ids) == 5
    assert {session.id for session, _ in results} <= ids
    assert (await doc(db, f"users/{user_id}"))["conversationCount"] == 5


@pytest.mark.firestore
async def test_concurrent_same_origin_across_clients_creates_once(db, users):
    user_id = await new_user(users)
    clients = [firestore.AsyncClient(project=PROJECT) for _ in range(4)]
    try:
        results = await asyncio.gather(
            *(FirestoreChatStore(c).get_or_create_session(user_id, "x", "same") for c in clients)
        )
    finally:
        for c in clients:
            c.close()
    assert sum(created for _, created in results) == 1
    assert len({session.id for session, _ in results}) == 1
    assert len(await owned_ids(db, user_id)) == 1


@pytest.mark.firestore
async def test_cleanup_failure_persists_marker_until_next_success(db, users, chats):
    user_id = await new_user(users)
    with patch(MAX_SESSIONS_PATH, 1):
        old = await chats.create_session(user_id, "old")
        await chats.add_message(user_id, old.id, "user", "remove me")
        with patch.object(db, "recursive_delete", side_effect=RuntimeError("unavailable")):
            replacement = await chats.create_session(user_id, "new")
        marker = f"users/{user_id}/conversationCleanup/{old.id}"
        assert await doc(db, marker) is not None
        assert await chats.get_session(user_id, old.id) is None

        # 新的 store（例如另一個實例）在下一次寫入時接手清理
        await FirestoreChatStore(db).get_or_create_session(user_id, "x", None)
    assert await doc(db, marker) is None
    assert [m async for m in db.collection(f"conversations/{old.id}/messages").stream()] == []
    assert replacement.id not in await owned_ids(db, user_id)  # 上限 1：也被淘汰了


# consent / deletion
@pytest.mark.firestore
async def test_consent_documents_are_per_version_and_audited(db, users):
    user_id = await new_user(users)
    assert not await users.has_consent(user_id, "privacy_policy", "1")
    await users.set_consent(user_id, "privacy_policy", "1", True, "LIFF")
    assert await users.has_consent(user_id, "privacy_policy", "1")
    await users.set_consent(user_id, "privacy_policy", "1", False, "LIFF")
    assert not await users.has_consent(user_id, "privacy_policy", "1")
    await users.set_consent(user_id, "privacy_policy", "2", True, "LIFF")

    v1 = await doc(db, f"users/{user_id}/consents/privacy_policy_v1")
    assert v1["status"] == "revoked"
    assert isinstance(v1["acceptedAt"], datetime) and isinstance(v1["revokedAt"], datetime)
    assert (await doc(db, f"users/{user_id}/consents/privacy_policy_v2"))["status"] == "accepted"
    audit = [d.to_dict() async for d in db.collection(f"users/{user_id}/auditLog").stream()]
    assert sorted(entry["action"] for entry in audit) == ["consent", "consent", "revoke"]


@pytest.mark.firestore
async def test_delete_user_leaves_nothing_behind(db, users, chats):
    who = identity()
    user_id, _ = await users.resolve_or_create(who)
    await users.set_consent(user_id, "privacy_policy", "1", True, "LIFF")
    await users.consume_daily_quota(user_id, 10)
    await users.record_module_use(user_id, "bus")
    session, _ = await chats.get_or_create_session(user_id, "x", "ev-1")
    await chats.add_message(user_id, session.id, "user", "我的秘密")

    await chats.delete_all_sessions(user_id)
    await users.delete_user(user_id)

    assert await doc(db, f"users/{user_id}") is None
    for sub in ("identities", "consents", "auditLog", "usage", "moduleStates", "conversationOrigins"):
        assert [d async for d in db.collection(f"users/{user_id}/{sub}").stream()] == [], sub
    assert await doc(db, f"identityLookup/{lookup_key('line', who.provider_user_id)}") is None
    assert await owned_ids(db, user_id) == set()
    assert [m async for m in db.collection(f"conversations/{session.id}/messages").stream()] == []

    again, created = await users.resolve_or_create(who)
    assert created and again != user_id


@pytest.mark.firestore
async def test_stale_lookup_after_interrupted_deletion_creates_a_new_user(db, users):
    who = identity()
    user_id, _ = await users.resolve_or_create(who)
    # 模擬刪除中斷：user 文件已刪除，但 lookup 還在
    await db.recursive_delete(db.document(f"users/{user_id}"))
    again, created = await users.resolve_or_create(who)
    assert created and again != user_id
    lookup = await doc(db, f"identityLookup/{lookup_key('line', who.provider_user_id)}")
    assert lookup["userId"] == again
