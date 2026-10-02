import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "infra"))
import build_frontend  # noqa: E402

CONF = """
PROJECT_ID=demo-project
LIFF_ID=1234567890-abcdefgh
API_ORIGIN=https://api-demo.a.run.app
HOSTING_SITE=demo-site
LINE_CHANNEL_SECRET=must-not-leak
"""


@pytest.fixture
def built(tmp_path, monkeypatch):
    conf = tmp_path / "demo.conf"
    conf.write_text(CONF, encoding="utf-8")
    monkeypatch.setattr(build_frontend, "ROOT", tmp_path)
    return build_frontend.build(conf)


def test_config_json_holds_only_public_values(built):
    config = json.loads((built / "public" / "config.json").read_text(encoding="utf-8"))
    assert config == {"liffId": "1234567890-abcdefgh", "apiBase": "https://api-demo.a.run.app"}
    assert "must-not-leak" not in (built / "public" / "config.json").read_text(encoding="utf-8")
    assert (built / "public" / "js" / "main.js").exists()
    assert not (built / "public" / "config.example.json").exists()


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
    ["API_ORIGIN=REPLACE_ME", "API_ORIGIN=http://api.example", "API_ORIGIN=https://api.example/path"],
)
def test_bad_or_missing_api_origin_is_rejected(tmp_path, line):
    conf = tmp_path / "bad.conf"
    conf.write_text(CONF.replace("API_ORIGIN=https://api-demo.a.run.app", line), encoding="utf-8")
    with pytest.raises(SystemExit):
        build_frontend.read_conf(conf)
