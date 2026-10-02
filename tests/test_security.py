import time

import httpx
import pytest
from fastapi import HTTPException

from src.app.security import LiffTokenVerifier, RateLimiter

VALID_CLAIMS = {"iss": "https://access.line.me", "sub": "Uabc", "aud": "100"}


@pytest.fixture
async def make_verifier():
    """依傳入的 handler 建立指向假 LINE endpoint 的 verifier，測試結束時關閉 client。"""
    clients: list[httpx.AsyncClient] = []

    def build(handler, channel_id="100"):
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(http)
        return LiffTokenVerifier(channel_id, http)

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
    assert first.user_id == "Uabc"
    assert first.display_name == "Ben"
    assert first == second
    assert calls == 1


async def test_id_token_rejected_when_line_says_400(make_verifier):
    verifier = make_verifier(
        lambda request: httpx.Response(400, json={"error": "invalid_request"})
    )
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
