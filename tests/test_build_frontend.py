import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
import build_frontend  # noqa: E402

from src.core.privacy import PRIVACY_POLICY_VERSION  # noqa: E402

CONF = """
PROJECT_ID=demo-project
API_ORIGIN=https://api-demo.a.run.app
HOSTING_SITE=demo-site
LINE_CHANNEL_SECRET=must-not-leak
"""


@pytest.fixture
def built(tmp_path, monkeypatch):
    # LINE 的 ID 不寫在 repo：由環境變數注入（CI 用 repo variable LIFF_ID_<ENV>）
    monkeypatch.setenv("LIFF_ID", "1234567890-abcdefgh")
    conf = tmp_path / "demo.conf"
    conf.write_text(CONF, encoding="utf-8")
    monkeypatch.setattr(build_frontend, "ROOT", tmp_path)
    return build_frontend.build(conf)


def test_config_json_holds_only_public_values(built):
    config = json.loads((built / "public" / "config.json").read_text(encoding="utf-8"))
    assert config == {
        "liffId": "1234567890-abcdefgh",
        "apiBase": "https://api-demo.a.run.app",
        "privacyPolicyVersion": PRIVACY_POLICY_VERSION,
    }
    assert "must-not-leak" not in (built / "public" / "config.json").read_text(encoding="utf-8")
    assert (built / "public" / "js" / "main.js").exists()
    assert (built / "public" / "js" / "theme.js").exists()
    assert not (built / "public" / "config.example.json").exists()


def test_theme_initializes_before_styles_on_both_pages(built):
    for name in ("index.html", "privacy.html"):
        html = (built / "public" / name).read_text(encoding="utf-8")
        assert html.index('<script src="./js/theme.js"></script>') < html.index(
            '<link rel="stylesheet" href="./style.css">'
        )


def test_privacy_page_shows_the_backend_policy_version(built, monkeypatch):
    page = built / "public" / "privacy.html"
    assert f"<span data-policy-version>{PRIVACY_POLICY_VERSION}</span>" in page.read_text(
        encoding="utf-8"
    )
    build_frontend.stamp_policy_version(page, "7")
    assert "<span data-policy-version>7</span>" in page.read_text(encoding="utf-8")


def test_privacy_page_without_version_marker_fails_the_build(tmp_path):
    page = tmp_path / "privacy.html"
    page.write_text("<p>版本 1</p>", encoding="utf-8")
    with pytest.raises(SystemExit):
        build_frontend.stamp_policy_version(page, "1")


@pytest.mark.parametrize("value", [None, "", "not a liff id", "123-abc; rm -rf"])
def test_missing_or_malformed_liff_id_fails_the_build(tmp_path, monkeypatch, value):
    conf = tmp_path / "demo.conf"
    conf.write_text(CONF, encoding="utf-8")
    monkeypatch.setattr(build_frontend, "ROOT", tmp_path)
    if value is None:
        monkeypatch.delenv("LIFF_ID", raising=False)
    else:
        monkeypatch.setenv("LIFF_ID", value)
    with pytest.raises(SystemExit):
        build_frontend.build(conf)


def test_hosting_headers_lock_connections_to_this_api(built):
    hosting = json.loads((built / "firebase.json").read_text(encoding="utf-8"))["hosting"]
    assert hosting["site"] == "demo-site"
    headers = {h["key"]: h["value"] for h in hosting["headers"][0]["headers"]}
    csp = headers["Content-Security-Policy"]
    assert "connect-src 'self' https://api-demo.a.run.app https://api.line.me" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize(
    "line",
    [
        "API_ORIGIN=REPLACE_ME",
        "API_ORIGIN=http://api.example",
        "API_ORIGIN=https://api.example/path",
    ],
)
def test_bad_or_missing_api_origin_is_rejected(tmp_path, line):
    conf = tmp_path / "bad.conf"
    conf.write_text(CONF.replace("API_ORIGIN=https://api-demo.a.run.app", line), encoding="utf-8")
    with pytest.raises(SystemExit):
        build_frontend.read_conf(conf)
