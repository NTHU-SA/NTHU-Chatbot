import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from src.app.auth import auth0
from src.app.auth.auth0 import LINE_CLAIM, USR_CLAIM, Auth0Authenticator

DOMAIN = "auth.example.test"
ISSUER = f"https://{DOMAIN}/"
AUDIENCE = "https://chat.example.test/api"
CLIENT_ID = "chatClientId123"
SUB = "google-oauth2|1234567890"
NTHUSA_ID = "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZABCD"
LINE_USER_ID = "U" + "0123456789abcdef" * 2


def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY = rsa_key()
OTHER_KEY = rsa_key()


def jwk(private_key, kid: str) -> dict:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    return {**data, "kid": kid, "use": "sig", "alg": "RS256"}


def claims(**overrides) -> dict:
    now = int(time.time())
    values = {
        "iss": ISSUER,
        "sub": SUB,
        "aud": [AUDIENCE, f"https://{DOMAIN}/userinfo"],
        "azp": CLIENT_ID,
        "iat": now,
        "exp": now + 600,
        "scope": "openid profile",
        USR_CLAIM: NTHUSA_ID,
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def token(private_key=SIGNING_KEY, kid="k1", **overrides) -> str:
    return jwt.encode(claims(**overrides), private_key, algorithm="RS256", headers={"kid": kid})


class FakeAuth0:
    """假的 Auth0：提供 JWKS 與 /userinfo，並記錄呼叫次數。"""

    def __init__(self):
        self.keys = [jwk(SIGNING_KEY, "k1")]
        self.jwks_calls = 0
        self.userinfo_calls = 0
        self.userinfo = {"name": "王小明", "picture": "https://lh3.googleusercontent.com/a/x"}
        self.userinfo_status = 200
        self.jwks_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == DOMAIN
        if request.url.path == "/.well-known/jwks.json":
            self.jwks_calls += 1
            return httpx.Response(self.jwks_status, json={"keys": self.keys})
        if request.url.path == "/userinfo":
            self.userinfo_calls += 1
            assert request.headers["authorization"].startswith("Bearer ")
            return httpx.Response(self.userinfo_status, json=self.userinfo)
        return httpx.Response(404)


@pytest.fixture
async def fake():
    return FakeAuth0()


@pytest.fixture
async def verifier(fake):
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        yield Auth0Authenticator(DOMAIN, AUDIENCE, CLIENT_ID, http)


async def test_access_token_verified_and_cached(verifier, fake):
    access_token = token()
    first = await verifier.verify(access_token)
    second = await verifier.verify(access_token)
    # 身分是 Action 給的 NTHUSA ID，不是會隨帳號連結改變的 sub
    assert first.provider == "nthusa"
    assert first.provider_user_id == NTHUSA_ID
    assert first.linked == ()
    assert first.display_name == "王小明"
    assert first.picture_url == "https://lh3.googleusercontent.com/a/x"
    assert NTHUSA_ID not in repr(first)  # 外部 ID 不出現在 repr / log
    assert first == second
    assert (fake.jwks_calls, fake.userinfo_calls) == (1, 1)


async def test_single_audience_string_is_accepted(verifier):
    identity = await verifier.verify(token(aud=AUDIENCE))
    assert identity.provider_user_id == NTHUSA_ID


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "https://other.example.test/api"},  # 別的 API
        {"iss": "https://evil.example.test/"},  # 別的 tenant
        {"azp": "someOtherApp123"},  # 同 tenant 的其他 Application
        {"azp": None},
        {"sub": None},
        {"exp": int(time.time()) - 120},  # 過期（超過容忍範圍）
        {"iat": None},
        {USR_CLAIM: None},  # post-login Action 沒設定
        {USR_CLAIM: "usr_" + "0" * 32},  # 內部 id 的格式，不是 NTHUSA ID
        {USR_CLAIM: "usr_01K7AJ3Z8Q4N5V6W7X8Y9ZABCU"},  # ULID 不含 U
        {USR_CLAIM: 123},
    ],
)
async def test_bad_claims_are_rejected(verifier, overrides):
    with pytest.raises(HTTPException) as error:
        await verifier.verify(token(**overrides))
    assert error.value.status_code == 401


async def test_signature_from_another_key_is_rejected(verifier):
    with pytest.raises(HTTPException) as error:
        await verifier.verify(token(private_key=OTHER_KEY))
    assert error.value.status_code == 401


async def test_symmetric_algorithm_is_rejected(verifier):
    """alg confusion：以 HS256 簽、key 用公開資訊，也不能通過。"""
    forged = jwt.encode(
        claims(), "not-a-secret-but-long-enough-32b!", algorithm="HS256", headers={"kid": "k1"}
    )
    with pytest.raises(HTTPException) as error:
        await verifier.verify(forged)
    assert error.value.status_code == 401


@pytest.mark.parametrize("value", ["", "garbage", "a.b.c"])
async def test_malformed_tokens_are_rejected(verifier, value):
    with pytest.raises(HTTPException) as error:
        await verifier.verify(value)
    assert error.value.status_code == 401


async def test_unknown_kid_refetches_at_most_once_a_minute(verifier, fake):
    await verifier.verify(token())
    for _ in range(3):
        with pytest.raises(HTTPException):
            await verifier.verify(token(private_key=OTHER_KEY, kid="forged"))
    assert fake.jwks_calls == 1


async def test_rotated_key_is_picked_up(verifier, fake, monkeypatch):
    await verifier.verify(token())
    fake.keys.append(jwk(OTHER_KEY, "k2"))
    # 超過最短重抓間隔後，新的 kid 會觸發重抓
    monkeypatch.setattr(auth0, "JWKS_MIN_REFRESH_SECONDS", 0)
    identity = await verifier.verify(token(private_key=OTHER_KEY, kid="k2"))
    assert identity.provider_user_id == NTHUSA_ID
    assert fake.jwks_calls == 2


async def test_unreachable_jwks_returns_503(verifier, fake):
    fake.jwks_status = 500
    with pytest.raises(HTTPException) as error:
        await verifier.verify(token())
    assert error.value.status_code == 503


async def test_profile_is_best_effort(verifier, fake):
    fake.userinfo_status = 429
    identity = await verifier.verify(token())
    assert identity.provider_user_id == NTHUSA_ID
    assert identity.display_name is None
    assert identity.picture_url is None


async def test_non_https_picture_is_dropped(verifier, fake):
    fake.userinfo = {"nickname": "ming", "picture": "http://example.test/a.png"}
    identity = await verifier.verify(token())
    assert identity.display_name == "ming"
    assert identity.picture_url is None


async def test_line_identity_from_the_action_is_linked(verifier):
    identity = await verifier.verify(token(**{LINE_CLAIM: LINE_USER_ID}))
    assert [(other.provider, other.provider_user_id) for other in identity.linked] == [
        ("line", LINE_USER_ID)
    ]
    assert LINE_USER_ID not in repr(identity)


@pytest.mark.parametrize("value", ["Uabc", "line|" + LINE_USER_ID, LINE_USER_ID.upper(), 42])
async def test_malformed_line_claim_is_ignored(verifier, value):
    identity = await verifier.verify(token(**{LINE_CLAIM: value}))
    assert identity.provider_user_id == NTHUSA_ID
    assert identity.linked == ()
