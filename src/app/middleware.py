"""
HTTP 安全標頭與 CORS。

後端只提供 JSON API 與 LINE webhook；LIFF 頁面由 Firebase Hosting 提供，
頁面的 CSP 設在 Hosting（見 `infra/build_frontend.py`）。
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# API 回應不會被當成頁面渲染：禁止載入任何資源與被嵌入
API_CSP = "default-src 'none'; frame-ancestors 'none'"


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", API_CSP)
    return response


def add_cors(app: FastAPI, origins: tuple[str, ...]) -> None:
    """
    只允許設定中的前端網域跨站呼叫 API。

    驗證只靠 `Authorization: Bearer`，不使用 cookie，所以 `allow_credentials=False`；
    沒有設定任何網域時不掛 CORS（只接受同源請求，例如 webhook 與本機測試）。
    """
    if not origins:
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Auth-Provider"],
        max_age=600,
    )
