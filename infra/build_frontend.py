"""
產生某個環境的 Firebase Hosting 部署內容。

    python infra/build_frontend.py infra/environments/staging.conf

輸出到 `build/frontend-<環境>/`：
- `public/`：`frontend/` 的複本，加上該環境的 `config.json`（只含公開的 LIFF ID、API 網址與隱私權政策版本）；
  `privacy.html` 顯示的版本換成 `src/core/privacy.py` 的版本（與後端同一個來源）
- `firebase.json`：Hosting 設定與安全標頭；CSP 的 connect-src 只列出這個環境的 API

不讀也不寫任何機密。
"""

from __future__ import annotations

import json
import re
import runpy
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
POLICY_SOURCE = ROOT / "src" / "core" / "privacy.py"
POLICY_MARKER = re.compile(r"(<span data-policy-version>)[^<]*(</span>)")
REQUIRED = ("PROJECT_ID", "LIFF_ID", "API_ORIGIN")
ORIGIN = re.compile(r"^https://[a-z0-9-]+(\.[a-z0-9-]+)+$")


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
    return values


def content_security_policy(api_origin: str) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://static.line-scdn.net",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: https://profile.line-scdn.net https://*.line-scdn.net",
            f"connect-src 'self' {api_origin} https://api.line.me https://liff.line.me https://access.line.me",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
        ]
    )


def hosting_config(conf: dict[str, str]) -> dict:
    headers = [
        {"key": "Content-Security-Policy", "value": content_security_policy(conf["API_ORIGIN"])},
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
    version = policy_version()
    out = ROOT / "build" / f"frontend-{conf_path.stem}"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(FRONTEND, out / "public", ignore=shutil.ignore_patterns("config*.json"))
    public_config = {
        "liffId": conf["LIFF_ID"],
        "apiBase": conf["API_ORIGIN"],
        "privacyPolicyVersion": version,
    }
    (out / "public" / "config.json").write_text(
        json.dumps(public_config, indent=2) + "\n", encoding="utf-8"
    )
    stamp_policy_version(out / "public" / "privacy.html", version)
    (out / "firebase.json").write_text(
        json.dumps(hosting_config(conf), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python infra/build_frontend.py infra/environments/<env>.conf")
    print(build(Path(sys.argv[1])))
