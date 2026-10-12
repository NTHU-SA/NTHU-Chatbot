import asyncio

import pytest

from src.app.auth.rate_limit import RateLimiter
from src.app.auth.service import MAPPING_TTL_SECONDS, IdentityService
from src.application.models.identity import (
    NTHUSA_PROVIDER,
    REUSE_AFTER_DELETION,
    AccountDisabledError,
    VerifiedIdentity,
    lookup_key,
)
from src.application.services.chat_store import MemoryChatStore
from src.application.services.user_store import MemoryUserStore


def test_rate_limiter_burst_then_block_and_independent_keys():
    limiter = RateLimiter(rate_per_minute=60, burst=3)
    assert [limiter.allow("u") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other")


def test_lookup_key_hides_the_external_id():
    key = lookup_key("line", "Uaaaa")
    assert key == lookup_key("line", "Uaaaa")
    assert "Uaaaa" not in key
    assert key != lookup_key("google", "Uaaaa")  # provider 不同就是不同身分
    assert len(key) == 64


# -- IdentityService：NTHUSA ID 就是 user id --
@pytest.fixture
def users():
    return MemoryUserStore()


@pytest.fixture
def identities(users):
    return IdentityService(users)


USR_1 = "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZAAAA"
USR_2 = "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZBBBB"
LINE_A = VerifiedIdentity(provider="line", provider_user_id="Uaaaa")
LINE_B = VerifiedIdentity(provider="line", provider_user_id="Ubbbb")


def nthusa(usr: str, *linked: VerifiedIdentity) -> VerifiedIdentity:
    return VerifiedIdentity(provider=NTHUSA_PROVIDER, provider_user_id=usr, linked=linked)


async def test_the_nthusa_id_is_the_user_id(identities, users):
    first = await identities.resolve(nthusa(USR_1))
    second = await identities.resolve(nthusa(USR_1))
    assert first.user_id == second.user_id == USR_1
    assert first.provider == NTHUSA_PROVIDER
    assert list(users.users) == [USR_1]
    assert (await identities.resolve(nthusa(USR_2))).user_id == USR_2


async def test_concurrent_first_login_creates_one_user(users):
    services = [IdentityService(users) for _ in range(10)]  # 模擬多個實例
    principals = await asyncio.gather(*(service.resolve(nthusa(USR_1)) for service in services))
    assert {principal.user_id for principal in principals} == {USR_1}
    assert len(users.users) == 1


async def test_blocked_user_is_rejected_once_the_status_cache_expires(identities, users):
    await identities.resolve(nthusa(USR_1))
    users.users[USR_1]["status"] = "blocked"
    identities._status.clear()  # 狀態快取過期
    with pytest.raises(AccountDisabledError):
        await identities.resolve(nthusa(USR_1))


async def test_deleting_account_is_refused_except_for_the_delete_api(identities, users):
    await identities.resolve(nthusa(USR_1))
    await users.begin_deletion(USR_1)
    identities.forget_user(USR_1)
    with pytest.raises(AccountDisabledError) as raised:
        await identities.resolve(nthusa(USR_1))
    assert raised.value.status == "deleting"
    again = await identities.resolve(nthusa(USR_1), allow_deleting=True)
    assert again.user_id == USR_1


async def test_deleted_account_is_rebuilt_empty_only_after_the_grace_period(users):
    other_instance = IdentityService(users)
    await other_instance.resolve(nthusa(USR_1, LINE_A))
    await users.set_preference(USR_1, "nickname", "小明", "user")
    await users.begin_deletion(USR_1)
    await users.delete_user(USR_1)
    other_instance._status.clear()  # 狀態快取過期
    # 剛刪除：刪除前還在進行的請求可能晚到，不能馬上在同一個 key 重建
    with pytest.raises(AccountDisabledError) as raised:
        await other_instance.resolve(nthusa(USR_1))
    assert raised.value.status == "deleted"
    users.users[USR_1]["deletedAt"] -= REUSE_AFTER_DELETION
    # 這段時間裡其他實例的快取（含 LINE 的對應）都已過期
    assert MAPPING_TTL_SECONDS <= REUSE_AFTER_DELETION.total_seconds()
    other_instance._mapping.clear()
    fresh = await other_instance.resolve(nthusa(USR_1))
    assert fresh.user_id == USR_1
    assert users.users[USR_1]["status"] == "active"
    assert (await users.get_profile(USR_1)).nickname is None
    assert await other_instance.find_line_user("Uaaaa") is None  # 綁定在下一次登入時才同步


# -- LINE 綁定：以 Auth0 為準，webhook 只查詢 --
async def test_unbound_line_user_gets_nothing_created(identities, users):
    assert await identities.find_line_user("Uaaaa") is None
    assert users.users == {}
    assert users.lookup == {}


async def test_bound_line_reaches_the_same_user_from_the_webhook(identities, users):
    await identities.resolve(nthusa(USR_1))
    assert await users.find_user(LINE_A) is None
    await identities.resolve(nthusa(USR_1, LINE_A))
    assert await IdentityService(users).find_line_user("Uaaaa") == USR_1
    assert [entry["action"] for entry in users.audit[USR_1]] == ["link"]


async def test_line_moves_to_whoever_auth0_says_now_owns_it(identities, users):
    await identities.resolve(nthusa(USR_1, LINE_A))
    await users.set_preference(USR_1, "nickname", "第一個", "user")
    await identities.resolve(nthusa(USR_2, LINE_A))
    assert users.users[USR_1]["status"] == "active"  # 原本的帳號和資料都還在
    assert (await users.get_profile(USR_1)).nickname == "第一個"
    assert "line" not in users.identities[USR_1]
    assert await IdentityService(users).find_line_user("Uaaaa") == USR_2


async def test_rebinding_another_line_replaces_the_old_mapping(identities, users):
    await identities.resolve(nthusa(USR_1, LINE_A))
    await identities.resolve(nthusa(USR_1, LINE_B))
    assert await users.find_user(LINE_A) is None
    assert await users.find_user(LINE_B) == USR_1
    assert users.identities[USR_1]["line"]["providerUserId"] == "Ubbbb"


async def test_rebinding_back_and_forth_on_one_instance_follows_auth0(identities, users):
    await identities.resolve(nthusa(USR_1, LINE_A))
    assert await identities.find_line_user("Uaaaa") == USR_1
    await identities.resolve(nthusa(USR_1, LINE_B))
    # 同一個實例的快取不能再把舊的 LINE 對應到這個 user
    assert await identities.find_line_user("Uaaaa") is None
    await identities.resolve(nthusa(USR_1, LINE_A))
    assert await users.find_user(LINE_A) == USR_1
    assert await users.find_user(LINE_B) is None
    assert await identities.find_line_user("Ubbbb") is None


async def test_unbinding_twice_on_one_instance_removes_the_mapping(identities, users):
    await identities.resolve(nthusa(USR_1))
    await identities.resolve(nthusa(USR_1, LINE_A))
    await identities.resolve(nthusa(USR_1))
    assert await users.find_user(LINE_A) is None
    assert await identities.find_line_user("Uaaaa") is None
    await identities.resolve(nthusa(USR_1, LINE_A))
    await identities.resolve(nthusa(USR_1))
    assert await users.find_user(LINE_A) is None
    assert "line" not in users.identities[USR_1]


async def test_unbinding_in_auth0_removes_the_mapping(users):
    await IdentityService(users).resolve(nthusa(USR_1, LINE_A))
    await IdentityService(users).resolve(nthusa(USR_1))
    assert await users.find_user(LINE_A) is None
    assert "line" not in users.identities[USR_1]
    assert [entry["action"] for entry in users.audit[USR_1]] == ["link", "unlink"]


async def test_bindings_are_synced_once_per_cache_period(identities, users, monkeypatch):
    calls = 0
    original = users.sync_identity

    async def counting(*args):
        nonlocal calls
        calls += 1
        return await original(*args)

    monkeypatch.setattr(users, "sync_identity", counting)
    for _ in range(3):
        await identities.resolve(nthusa(USR_1, LINE_A))
    for _ in range(3):
        await identities.resolve(nthusa(USR_2))
    assert calls == 2


async def test_blocked_account_is_refused_from_the_webhook(identities, users):
    await identities.resolve(nthusa(USR_1, LINE_A))
    users.users[USR_1]["status"] = "blocked"
    webhook = IdentityService(users)
    with pytest.raises(AccountDisabledError):
        await webhook.find_line_user("Uaaaa")


async def test_quota_carries_over_when_the_same_nthusa_id_is_rebuilt(identities, users):
    await identities.resolve(nthusa(USR_1))
    assert await users.consume_daily_quota(USR_1, 1)
    await users.begin_deletion(USR_1)
    await users.delete_user(USR_1)
    users.users[USR_1]["deletedAt"] -= REUSE_AFTER_DELETION
    identities.forget_user(USR_1)
    await identities.resolve(nthusa(USR_1))
    assert not await users.consume_daily_quota(USR_1, 1)


async def test_chat_data_is_keyed_on_the_nthusa_id(identities):
    chats = MemoryChatStore()
    principal = await identities.resolve(nthusa(USR_1))
    await chats.create_session(principal.user_id, "對話")
    assert len(await chats.list_sessions(USR_1)) == 1
