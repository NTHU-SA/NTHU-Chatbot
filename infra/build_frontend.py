"""
產生某個環境的 Firebase Hosting 部署內容。

    LIFF_ID=<該環境的 LIFF ID> python infra/build_frontend.py infra/environments/staging.conf

輸出到 `build/frontend-<環境>/`：
- `public/`：`frontend/` 的複本，加上該環境的 `config.json`（只含公開的 LIFF ID、API 網址、Auth0 設定與隱私權政策版本）；
  `privacy.html` 顯示的版本換成 `src/core/privacy.py` 的版本（與後端同一個來源）
- `firebase.json`：Hosting 設定與安全標頭；CSP 的 connect-src 只列出這個環境的 API（與 Auth0 網域）

LIFF ID 與 Auth0 Client ID 不寫在 repo：從環境變數 `LIFF_ID`、`AUTH0_CLIENT_ID` 讀取
（CI 用 GitHub repo variable `LIFF_ID_<ENV>`、`AUTH0_CLIENT_ID_<ENV>` 注入）。
Auth0 網域與 API audience 在環境設定檔（`AUTH0_DOMAIN`、`AUTH0_AUDIENCE`）；三者缺一時不啟用 Auth0，
一般瀏覽器沿用 LIFF 的 LINE 登入。不讀也不寫任何機密。
"""

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
POLICY_SOURCE = ROOT / "src" / "core" / "privacy.py"
POLICY_MARKER = re.compile(r"(<span data-policy-version>)[^<]*(</span>)")
REQUIRED = ("PROJECT_ID", "API_ORIGIN")
LIFF_ID_PATTERN = re.compile(r"^\d+-[A-Za-z0-9]+$")
ORIGIN = re.compile(r"^https://[a-z0-9-]+(\.[a-z0-9-]+)+$")
DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Auth0 登入後的頭像（Google、GitHub、Gravatar 與 Auth0 預設頭像）
AVATAR_HOSTS = (
    "https://*.googleusercontent.com",
    "https://avatars.githubusercontent.com",
    "https://s.gravatar.com",
    "https://cdn.auth0.com",
)


def read_conf(path: Path) -> dict[str, str]:
    """讀取 `KEY=VALUE` 格式的環境設定（與 bootstrap.sh 共用）。"""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    missing = [key for key in REQUIRED if not values.get(key) or values[key] == "REPLACE_ME"]
    if missing:
        raise SystemExit(f"{path}: missing {', '.join(missing)}")
    if not ORIGIN.match(values["API_ORIGIN"]):
        raise SystemExit(f"{path}: API_ORIGIN must be an https origin without a path")
    for key in ("AUTH0_DOMAIN", "CUSTOM_DOMAIN"):
        if values.get(key) and not DOMAIN.match(values[key]):
            raise SystemExit(f"{path}: {key} must be a bare host name")
    if bool(values.get("AUTH0_DOMAIN")) != bool(values.get("AUTH0_AUDIENCE")):
        raise SystemExit(f"{path}: set both AUTH0_DOMAIN and AUTH0_AUDIENCE, or neither")
    return values


def auth0_config(conf: dict[str, str]) -> dict[str, str] | None:
    """
    前端的 Auth0 設定（公開值）。

    設定檔沒有 Auth0 時停用；有設定但還沒提供 `AUTH0_CLIENT_ID`（尚未建立 Application）時也停用並提示，
    前端沿用 LINE 登入。Client ID 格式不符則直接失敗。
    """
    if not conf.get("AUTH0_DOMAIN"):
        return None
    client_id = os.environ.get("AUTH0_CLIENT_ID", "").strip()
    if not client_id:
        print("AUTH0_CLIENT_ID is not set; Auth0 sign-in stays disabled", file=sys.stderr)
        return None
    if not CLIENT_ID.match(client_id):
        raise SystemExit("AUTH0_CLIENT_ID environment variable is malformed")
    return {
        "domain": conf["AUTH0_DOMAIN"],
        "clientId": client_id,
        "audience": conf["AUTH0_AUDIENCE"],
    }


def content_security_policy(api_origin: str, auth0_domain: str | None = None) -> str:
    # Auth0：token / userinfo 走 connect-src；頁面重新載入後的靜默登入用隱藏 iframe（frame-src）
    auth0 = f" https://{auth0_domain}" if auth0_domain else ""
    avatars = " " + " ".join(AVATAR_HOSTS) if auth0_domain else ""
    directives = [
        "default-src 'self'",
        "script-src 'self' https://static.line-scdn.net",
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: https://profile.line-scdn.net https://*.line-scdn.net{avatars}",
        f"connect-src 'self' {api_origin} https://api.line.me https://liff.line.me https://access.line.me{auth0}",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    ]
    if auth0_domain:
        directives.insert(5, f"frame-src 'self'{auth0}")
    return "; ".join(directives)


def hosting_config(conf: dict[str, str], auth0: dict[str, str] | None = None) -> dict:
    csp = content_security_policy(conf["API_ORIGIN"], auth0["domain"] if auth0 else None)
    headers = [
        {"key": "Content-Security-Policy", "value": csp},
        {"key": "X-Content-Type-Options", "value": "nosniff"},
        {"key": "X-Frame-Options", "value": "DENY"},
        {"key": "Referrer-Policy", "value": "strict-origin-when-cross-origin"},
        {"key": "Permissions-Policy", "value": "camera=(), microphone=(), geolocation=()"},
        # 檔名沒有雜湊：每次都向 Hosting 驗證（ETag），避免新舊 module 混用
        {"key": "Cache-Control", "value": "no-cache"},
    ]
    return {
        "hosting": {
            "site": conf.get("HOSTING_SITE") or conf["PROJECT_ID"],
            "public": "public",
            "ignore": ["config.example.json", "**/.*"],
            "cleanUrls": True,
            "headers": [{"source": "**", "headers": headers}],
        }
    }


def liff_id() -> str:
    value = os.environ.get("LIFF_ID", "").strip()
    if not LIFF_ID_PATTERN.match(value):
        raise SystemExit("LIFF_ID environment variable is missing or malformed")
    return value


def policy_version() -> str:
    """後端要求同意的版本（src/core/privacy.py）。只執行那個沒有任何 import 的小檔案。"""
    return str(runpy.run_path(str(POLICY_SOURCE))["PRIVACY_POLICY_VERSION"])


def stamp_policy_version(page: Path, version: str) -> None:
    html = page.read_text(encoding="utf-8")
    stamped, count = POLICY_MARKER.subn(lambda m: f"{m.group(1)}{version}{m.group(2)}", html)
    if count == 0:
        raise SystemExit(f"{page}: missing <span data-policy-version>")
    page.write_text(stamped, encoding="utf-8")


def build(conf_path: Path) -> Path:
    conf = read_conf(conf_path)
    auth0 = auth0_config(conf)
    version = policy_version()
    out = ROOT / "build" / f"frontend-{conf_path.stem}"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(
        FRONTEND,
        out / "public",
        ignore=shutil.ignore_patterns("config*.json", "flex-preview*"),
    )
    public_config = {
        "liffId": liff_id(),
        "apiBase": conf["API_ORIGIN"],
        "privacyPolicyVersion": version,
    }
    if auth0:
        public_config["auth0"] = auth0
    (out / "public" / "config.json").write_text(
        json.dumps(public_config, indent=2) + "\n", encoding="utf-8"
    )
    stamp_policy_version(out / "public" / "privacy.html", version)
    (out / "firebase.json").write_text(
        json.dumps(hosting_config(conf, auth0), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python infra/build_frontend.py infra/environments/<env>.conf")
    print(build(Path(sys.argv[1])))
