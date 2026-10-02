"""
身分驗證。

- `Authenticator`：每種登入方式一個實作，驗證 token 後回傳 `VerifiedIdentity`。目前只有 LINE；
  未來加學校 OAuth 或 Google 時新增一個類別，並在 `create_app()` 的 `app.state.authenticators` 註冊即可。
- `IdentityService`：外部身分 → 內部 user（`usr_…`）。
- `get_principal`：路由使用的 FastAPI 依賴。
"""

from typing import Protocol

from src.application.models.identity import VerifiedIdentity


class Authenticator(Protocol):
    provider: str

    async def verify(self, token: str) -> VerifiedIdentity:
        """驗證 token；無效時拋出 401 HTTPException。"""
        ...
