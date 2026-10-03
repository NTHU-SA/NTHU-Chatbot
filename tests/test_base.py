def test_ping_get_returns_pong(client):
    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"message": "pong"}


def test_ping_head_returns_same_headers_without_body(client):
    get_response = client.get("/ping")
    head_response = client.head("/ping")

    assert head_response.status_code == 200
    assert head_response.content == b""
    assert head_response.headers["content-type"] == get_response.headers["content-type"]
    assert head_response.headers["content-length"] == get_response.headers["content-length"]


def test_ping_post_is_not_allowed(client):
    assert client.post("/ping").status_code == 405
