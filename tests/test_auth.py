import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException

from src.app.auth.line import LineLiffAuthenticator
from src.app.auth.rate_limit import RateLimiter
from src.app.auth.service import IdentityService
from src.application.models.identity import (
    NTHUSA_PROVIDER,
    AccountDisabledError,
    IdentityConflictError,
    LastIdentityError,
    VerifiedIdentity,
    lookup_key,
    new_user_id,
)
from src.application.services.chat_store import MemoryChatStore
from src.application.services.user_store import MemoryUserStore

VALID_CLAIMS = {"iss": "https://access.line.me", "sub": "Uabc", "aud": "100"}


@pytest.fixture
async def make_verifier():
    """依傳入的 handler 建立指向假 LINE endpoint 的 verifier，測試結束時關閉 client。"""
    clients: list[httpx.AsyncClient] = []

    def build(handler, channel_id="100"):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        return LineLiffAuthenticator(channel_id, http)

    yield build
    for http in clients:
        await http.aclose()


async def test_id_token_verified_and_cached(make_verifier):
    calls = 0

    def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        assert request.url.host == "api.line.me"
        body = request.content.decode()
        assert "client_id=100" in body
        assert "id_token=tok" in body
        return httpx.Response(
            200,
            json={
                **VALID_CLAIMS,
                "exp": int(time.time()) + 600,
                "name": "Ben",
                "picture": "https://profile.line-scdn.net/x",
            },
        )

    verifier = make_verifier(handler)
    first = await verifier.verify("tok")
    second = await verifier.verify("tok")
    assert first.provider == "line"
    assert first.provider_user_id == "Uabc"
    assert first.display_name == "Ben"
    assert "Uabc" not in repr(first)  # 外部 ID 不出現在 repr / log
    assert first == second
    assert calls == 1


async def test_id_token_rejected_when_line_says_400(make_verifier):
    verifier = make_verifier(lambda request: httpx.Response(400, json={"error": "invalid_request"}))
    with pytest.raises(HTTPException) as error:
        await verifier.verify("bad")
    assert error.value.status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {**VALID_CLAIMS, "aud": "999", "exp": time.time() + 60},  # audience 不符
        {**VALID_CLAIMS, "iss": "https://evil.example"},  # issuer 不符
        {"iss": "https://access.line.me", "aud": "100"},  # 沒有 sub
    ],
)
async def test_id_token_rejected_on_bad_claims(make_verifier, claims):
    verifier = make_verifier(lambda request: httpx.Response(200, json=claims))
    with pytest.raises(HTTPException) as error:
        await verifier.verify("tok")
    assert error.value.status_code == 401


async def test_unreachable_line_returns_503(make_verifier):
    def handler(_request):
        raise httpx.ConnectError("down")

    verifier = make_verifier(handler)
    with pytest.raises(HTTPException) as error:
        await verifier.verify("tok")
    assert error.value.status_code == 503


def test_rate_limiter_burst_then_block_and_independent_keys():
    limiter = RateLimiter(rate_per_minute=60, burst=3)
    assert [limiter.allow("u") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("other")


# -- IdentityService --
@pytest.fixture
def users():
    return MemoryUserStore()


@pytest.fixture
def identities(users):
    return IdentityService(users)


LINE_A = VerifiedIdentity(provider="line", provider_user_id="Uaaaa", display_name="A")
LINE_B = VerifiedIdentity(provider="line", provider_user_id="Ubbbb")


async def test_same_identity_resolves_to_one_internal_user(identities, users):
    first = await identities.resolve(LINE_A)
    second = await identities.resolve(LINE_A)
    assert first.user_id == second.user_id
    assert first.user_id.startswith("usr_")
    assert len(users.users) == 1
    assert (await identities.resolve(LINE_B)).user_id != first.user_id


async def test_concurrent_first_contact_creates_one_user(users):
    services = [IdentityService(users) for _ in range(10)]  # 模擬多個實例
    principals = await asyncio.gather(*(service.resolve(LINE_A) for service in services))
    assert len({principal.user_id for principal in principals}) == 1
    assert len(users.users) == 1


async def test_webhook_and_liff_share_the_same_user(identities):
    from_liff = await identities.resolve(LINE_A)
    assert await identities.resolve_line_user("Uaaaa") == from_liff.user_id


async def test_blocked_user_is_rejected_even_when_mapping_is_cached(identities, users):
    principal = await identities.resolve(LINE_A)
    users.users[principal.user_id]["status"] = "blocked"
    identities._status.clear()  # 狀態快取過期
    with pytest.raises(AccountDisabledError):
        await identities.resolve(LINE_A)
    identities._mapping.clear()  # 對應快取也過期：改走 store，一樣拒絕
    with pytest.raises(AccountDisabledError):
        await identities.resolve(LINE_A)


async def test_deleting_account_is_refused_except_for_the_delete_api(identities, users):
    principal = await identities.resolve(LINE_A)
    await users.begin_deletion(principal.user_id)
    identities.forget_user(principal.user_id)
    with pytest.raises(AccountDisabledError) as raised:
        await identities.resolve(LINE_A)
    assert raised.value.status == "deleting"
    again = await identities.resolve(LINE_A, allow_deleting=True)
    assert again.user_id == principal.user_id


async def test_deleted_account_resolves_to_a_new_user_even_from_a_stale_cache(users):
    other_instance = IdentityService(users)
    principal = await other_instance.resolve(LINE_A)
    await users.begin_deletion(principal.user_id)
    await users.delete_user(principal.user_id)
    other_instance._status.clear()  # 對應還在快取裡，狀態快取過期
    fresh = await other_instance.resolve(LINE_A)
    assert fresh.user_id != principal.user_id
    assert users.users[principal.user_id] == {"status": "deleted"}


def test_lookup_key_hides_the_external_id():
    key = lookup_key("line", "Uaaaa")
    assert key == lookup_key("line", "Uaaaa")
    assert "Uaaaa" not in key
    assert key != lookup_key("google", "Uaaaa")  # provider 不同就是不同身分
    assert len(key) == 64


def test_internal_ids_are_random_and_opaque():
    ids = {new_user_id() for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("usr_") and len(i) == 36 for i in ids)


# -- 帳號連結規則（連結 API 等第二種登入方式上線時才開放） --
GOOGLE = VerifiedIdentity(provider="google", provider_user_id="1234567890")


async def test_explicit_link_lets_both_identities_reach_the_same_user(identities, users):
    principal = await identities.resolve(LINE_A)
    await users.link_identity(principal.user_id, GOOGLE)
    assert (await identities.resolve(GOOGLE)).user_id == principal.user_id
    assert users.audit[principal.user_id] == [{"action": "link", "provider": "google"}]


async def test_identity_owned_by_someone_else_cannot_be_linked(identities, users):
    alice = await identities.resolve(LINE_A)
    bob = await identities.resolve(LINE_B)
    await users.link_identity(alice.user_id, GOOGLE)
    with pytest.raises(IdentityConflictError):
        await users.link_identity(bob.user_id, GOOGLE)
    assert (await identities.resolve(GOOGLE)).user_id == alice.user_id


async def test_one_identity_per_provider(identities, users):
    alice = await identities.resolve(LINE_A)
    with pytest.raises(IdentityConflictError):
        await users.link_identity(alice.user_id, LINE_B)


async def test_last_identity_cannot_be_unlinked(identities, users):
    alice = await identities.resolve(LINE_A)
    with pytest.raises(LastIdentityError):
        await users.unlink_identity(alice.user_id, "line")
    await users.link_identity(alice.user_id, GOOGLE)
    await users.unlink_identity(alice.user_id, "google")
    identities.forget(GOOGLE)
    # 解除後同一個 Google 帳號再登入會是新的 user，不會回到 alice
    assert (await identities.resolve(GOOGLE)).user_id != alice.user_id
    assert [entry["action"] for entry in users.audit[alice.user_id]] == ["link", "unlink"]


async def test_quota_is_per_internal_user_across_linked_identities(identities, users):
    alice = await identities.resolve(LINE_A)
    await users.link_identity(alice.user_id, GOOGLE)
    via_google = await identities.resolve(GOOGLE)
    assert await users.consume_daily_quota(alice.user_id, 1)
    assert not await users.consume_daily_quota(via_google.user_id, 1)


# -- NTHUSA ID（Auth0）與綁定的 LINE --
def nthusa(usr: str, *linked: VerifiedIdentity) -> VerifiedIdentity:
    return VerifiedIdentity(provider=NTHUSA_PROVIDER, provider_user_id=usr, linked=linked)


USR_1 = "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZAAAA"
USR_2 = "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZBBBB"


@pytest.fixture
def chats():
    return MemoryChatStore()


@pytest.fixture
def linking(users, chats):
    return IdentityService(users, chats)


async def test_first_nthusa_login_adopts_the_existing_line_user(linking, users, chats):
    """之前只用 LINE 的人第一次經 Auth0 登入：沿用原本的 user，對話保留。"""
    old_user = await linking.resolve_line_user("Uaaaa")
    await chats.create_session(old_user, "舊對話")
    principal = await linking.resolve(nthusa(USR_1, LINE_A))
    assert principal.user_id == old_user
    assert principal.provider == NTHUSA_PROVIDER
    assert len(await chats.list_sessions(old_user)) == 1
    assert await users.find_user(nthusa(USR_1)) == old_user
    assert len(users.users) == 1


async def test_line_bound_later_reaches_the_same_user_from_the_webhook(linking, users):
    principal = await linking.resolve(nthusa(USR_1))
    assert await users.find_user(LINE_A) is None
    assert (await linking.resolve(nthusa(USR_1, LINE_A))).user_id == principal.user_id
    assert await IdentityService(users).resolve_line_user("Uaaaa") == principal.user_id


async def test_line_only_orphan_is_erased_when_its_line_moves(linking, users, chats):
    principal = await linking.resolve(nthusa(USR_1))
    orphan = await linking.resolve_line_user("Uaaaa")
    await chats.create_session(orphan, "孤兒帳號的對話")
    await linking.resolve(nthusa(USR_1, LINE_A))
    assert users.users[orphan]["status"] == "deleted"
    assert await chats.list_sessions(orphan) == []
    assert await linking.resolve_line_user("Uaaaa") == principal.user_id


async def test_another_nthusa_user_keeps_its_data_when_its_line_moves(linking, users):
    first = await linking.resolve(nthusa(USR_1, LINE_A))
    second = await linking.resolve(nthusa(USR_2, LINE_A))
    assert second.user_id != first.user_id
    assert users.users[first.user_id]["status"] == "active"
    assert "line" not in users.identities[first.user_id]
    assert await linking.resolve_line_user("Uaaaa") == second.user_id
    assert (await linking.resolve(nthusa(USR_1))).user_id == first.user_id


async def test_rebinding_another_line_replaces_the_old_mapping(linking, users):
    principal = await linking.resolve(nthusa(USR_1, LINE_A))
    await linking.resolve(nthusa(USR_1, LINE_B))
    assert await users.find_user(LINE_A) is None
    assert await users.find_user(LINE_B) == principal.user_id
    assert users.identities[principal.user_id]["line"]["providerUserId"] == "Ubbbb"


async def test_linked_mapping_is_cached_per_instance(linking, users, monkeypatch):
    await linking.resolve(nthusa(USR_1, LINE_A))
    calls = 0
    original = users.move_identity

    async def counting(*args):
        nonlocal calls
        calls += 1
        return await original(*args)

    monkeypatch.setattr(users, "move_identity", counting)
    for _ in range(3):
        await linking.resolve(nthusa(USR_1, LINE_A))
    assert calls == 0


async def test_blocked_line_user_cannot_escape_through_auth0(linking, users):
    blocked = await linking.resolve_line_user("Uaaaa")
    users.users[blocked]["status"] = "blocked"
    with pytest.raises(AccountDisabledError):
        await linking.resolve(nthusa(USR_1, LINE_A))
    assert await users.find_user(nthusa(USR_1)) is None


async def test_concurrent_first_nthusa_logins_adopt_one_user(users, chats):
    old_user = await IdentityService(users).resolve_line_user("Uaaaa")
    services = [IdentityService(users, chats) for _ in range(5)]
    principals = await asyncio.gather(
        *(service.resolve(nthusa(USR_1, LINE_A)) for service in services)
    )
    assert {principal.user_id for principal in principals} == {old_user}
    assert len(users.users) == 1


async def test_blocked_line_user_is_not_moved_or_erased(linking, users):
    principal = await linking.resolve(nthusa(USR_1))
    blocked = await linking.resolve_line_user("Uaaaa")
    users.users[blocked]["status"] = "blocked"
    assert (await linking.resolve(nthusa(USR_1, LINE_A))).user_id == principal.user_id
    assert users.users[blocked]["status"] == "blocked"
    assert await users.find_user(LINE_A) == blocked
