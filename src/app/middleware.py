"""HTTP 安全標頭。CSP 只套用在 LIFF 靜態頁面。"""

from fastapi import Request

CSP = (
    "default-src 'self'; "
    "script-src 'self' https://static.line-scdn.net; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https://profile.line-scdn.net https://*.line-scdn.net; "
    "connect-src 'self' https://api.line.me https://liff.line.me https://access.line.me; "
    "base-uri 'self'; form-action 'self'; object-src 'none'"
)


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.path.startswith("/liff"):
        response.headers.setdefault("Content-Security-Policy", CSP)
    return response
