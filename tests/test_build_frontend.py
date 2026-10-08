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


def test_local_config_example_uses_backend_policy_version():
    example = Path(__file__).resolve().parent.parent / "frontend" / "config.example.json"
    config = json.loads(example.read_text(encoding="utf-8"))
    assert config["privacyPolicyVersion"] == PRIVACY_POLICY_VERSION


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
    assert not (built / "public" / "flex-preview.html").exists()
    assert not (built / "public" / "flex-preview-data.js").exists()
    assert not (built / "public" / "flex-preview.css").exists()
    assert not (built / "public" / "js" / "flex-preview.js").exists()


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


AUTH0_CONF = CONF + "AUTH0_DOMAIN=auth.example.test\nAUTH0_AUDIENCE=https://chat.example.test/api\n"


def build_with(tmp_path, monkeypatch, conf_text, client_id=None):
    monkeypatch.setenv("LIFF_ID", "1234567890-abcdefgh")
    if client_id is None:
        monkeypatch.delenv("AUTH0_CLIENT_ID", raising=False)
    else:
        monkeypatch.setenv("AUTH0_CLIENT_ID", client_id)
    conf = tmp_path / "demo.conf"
    conf.write_text(conf_text, encoding="utf-8")
    monkeypatch.setattr(build_frontend, "ROOT", tmp_path)
    out = build_frontend.build(conf)
    config = json.loads((out / "public" / "config.json").read_text(encoding="utf-8"))
    hosting = json.loads((out / "firebase.json").read_text(encoding="utf-8"))["hosting"]
    headers = {h["key"]: h["value"] for h in hosting["headers"][0]["headers"]}
    return config, headers["Content-Security-Policy"]


def test_auth0_settings_reach_config_and_csp(tmp_path, monkeypatch):
    config, csp = build_with(tmp_path, monkeypatch, AUTH0_CONF, client_id="spaClient12345")
    assert config["auth0"] == {
        "domain": "auth.example.test",
        "clientId": "spaClient12345",
        "audience": "https://chat.example.test/api",
    }
    assert "https://auth.example.test" in csp.split("connect-src")[1].split(";")[0]
    assert "frame-src 'self' https://auth.example.test" in csp
    assert "https://avatars.githubusercontent.com" in csp


def test_line_connection_reaches_the_config(tmp_path, monkeypatch):
    conf = AUTH0_CONF + "AUTH0_LINE_CONNECTION=line-chat\n"
    config, _ = build_with(tmp_path, monkeypatch, conf, client_id="spaClient12345")
    assert config["auth0"]["lineConnection"] == "line-chat"


def test_without_client_id_auth0_stays_disabled(tmp_path, monkeypatch):
    config, csp = build_with(tmp_path, monkeypatch, AUTH0_CONF)
    assert "auth0" not in config
    assert "auth.example.test" not in csp
    assert "frame-src" not in csp


def test_without_auth0_the_csp_is_unchanged(built):
    hosting = json.loads((built / "firebase.json").read_text(encoding="utf-8"))["hosting"]
    csp = hosting["headers"][0]["headers"][0]["value"]
    assert "frame-src" not in csp
    assert "googleusercontent" not in csp


@pytest.mark.parametrize(
    "extra",
    [
        "AUTH0_DOMAIN=https://auth.example.test\nAUTH0_AUDIENCE=x\n",
        "AUTH0_DOMAIN=auth.example.test\n",
        "AUTH0_AUDIENCE=https://chat.example.test/api\n",
        "CUSTOM_DOMAIN=chat.example.test/\n",
        "AUTH0_LINE_CONNECTION=line\n",  # 沒有 Auth0
        "AUTH0_DOMAIN=auth.example.test\nAUTH0_AUDIENCE=a\nAUTH0_LINE_CONNECTION=bad name\n",
    ],
)
def test_bad_auth0_or_domain_settings_are_rejected(tmp_path, extra):
    conf = tmp_path / "bad.conf"
    conf.write_text(CONF + extra, encoding="utf-8")
    with pytest.raises(SystemExit):
        build_frontend.read_conf(conf)


def test_malformed_auth0_client_id_fails_the_build(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        build_with(tmp_path, monkeypatch, AUTH0_CONF, client_id="bad id!")
