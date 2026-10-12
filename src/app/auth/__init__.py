"""
身分驗證。

- `Authenticator`：驗證 token 後回傳 `VerifiedIdentity`。目前只有 Auth0（LINE、Google 等登入方式都在
  Auth0 裡，身分是 NTHUSA ID），在 `create_app()` 的 `app.state.authenticators` 註冊。
- `IdentityService`：NTHUSA ID → user（user id 就是 NTHUSA ID），以及 LINE userId → 綁定它的 user。
- `get_principal`：路由使用的 FastAPI 依賴。
"""

from typing import Protocol

from src.application.models.identity import VerifiedIdentity


class Authenticator(Protocol):
    provider: str

    async def verify(self, token: str) -> VerifiedIdentity:
        """驗證 token；無效時拋出 401 HTTPException。"""
        ...
