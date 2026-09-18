import time
import unittest

import httpx
from fastapi import HTTPException

from src.app.security import LiffTokenVerifier, RateLimiter


def _verifier(handler, channel_id="100"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LiffTokenVerifier(channel_id, http), http


class LiffTokenVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_id_token_verified_and_cached(self):
        calls = 0

        def handler(request: httpx.Request):
            nonlocal calls
            calls += 1
            self.assertEqual(request.url.host, "api.line.me")
            body = request.content.decode()
            self.assertIn("client_id=100", body)
            self.assertIn("id_token=tok", body)
            return httpx.Response(
                200,
                json={
                    "iss": "https://access.line.me",
                    "sub": "Uabc",
                    "aud": "100",
                    "exp": int(time.time()) + 600,
                    "name": "Ben",
                    "picture": "https://profile.line-scdn.net/x",
                },
            )

        verifier, http = _verifier(handler)
        async with http:
            first = await verifier.verify("tok")
            second = await verifier.verify("tok")
        self.assertEqual(first.user_id, "Uabc")
        self.assertEqual(first.display_name, "Ben")
        self.assertEqual(first, second)
        self.assertEqual(calls, 1)

    async def test_id_token_rejected_when_line_says_400(self):
        verifier, http = _verifier(
            lambda request: httpx.Response(400, json={"error": "invalid_request"})
        )
        async with http:
            with self.assertRaises(HTTPException) as context:
                await verifier.verify("bad")
        self.assertEqual(context.exception.status_code, 401)

    async def test_id_token_rejected_on_audience_mismatch(self):
        def handler(_request):
            return httpx.Response(
                200,
                json={
                    "iss": "https://access.line.me",
                    "sub": "U1",
                    "aud": "999",
                    "exp": time.time() + 60,
                },
            )

        verifier, http = _verifier(handler, channel_id="100")
        async with http:
            with self.assertRaises(HTTPException) as context:
                await verifier.verify("tok")
        self.assertEqual(context.exception.status_code, 401)

    async def test_id_token_rejected_on_wrong_issuer_or_missing_sub(self):
        def handler(_request):
            return httpx.Response(
                200, json={"iss": "https://evil.example", "sub": "U1", "aud": "100"}
            )

        verifier, http = _verifier(handler)
        async with http:
            with self.assertRaises(HTTPException):
                await verifier.verify("tok")

        def no_sub(_request):
            return httpx.Response(
                200, json={"iss": "https://access.line.me", "aud": "100"}
            )

        verifier, http = _verifier(no_sub)
        async with http:
            with self.assertRaises(HTTPException):
                await verifier.verify("tok2")

    async def test_unreachable_line_returns_503(self):
        def handler(_request):
            raise httpx.ConnectError("down")

        verifier, http = _verifier(handler)
        async with http:
            with self.assertRaises(HTTPException) as context:
                await verifier.verify("tok")
        self.assertEqual(context.exception.status_code, 503)


class RateLimiterTests(unittest.TestCase):
    def test_burst_then_block_and_independent_keys(self):
        limiter = RateLimiter(rate_per_minute=60, burst=3)
        self.assertEqual(
            [limiter.allow("u") for _ in range(4)], [True, True, True, False]
        )
        self.assertTrue(limiter.allow("other"))
