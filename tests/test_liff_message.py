from templates.messages import liff_url, open_web_chat

LIFF_ID = "1234567890-abcdefgh"
BASE = f"https://liff.line.me/{LIFF_ID}"


def test_url_encodes_and_truncates():
    assert liff_url(LIFF_ID) == BASE
    assert liff_url(LIFF_ID, "a b&c") == f"{BASE}?q=a%20b%26c"
    assert liff_url(LIFF_ID, "hi", "ev/1") == f"{BASE}?s=ev%2F1&q=hi"
    assert liff_url(LIFF_ID, session_key="ev-1") == f"{BASE}?s=ev-1"
    long_question = "x" * 600
    assert liff_url(LIFF_ID, long_question).endswith("x" * 500)
    assert "x" * 501 not in liff_url(LIFF_ID, long_question)


def test_bubble_variants():
    plain = open_web_chat(LIFF_ID)
    assert plain.contents.footer.contents[0].action.label == "在網頁中詢問"
    assert len(plain.contents.body.contents) == 1  # 沒有問題預覽

    with_question = open_web_chat(LIFF_ID, "南大公車", session_key="ev-1")
    assert with_question.contents.body.contents[1].contents[0].text == "南大公車"
    assert "?s=ev-1&q=" in with_question.contents.footer.contents[0].action.uri

    greeting = open_web_chat(LIFF_ID, greeting=True)
    assert greeting.contents.footer.contents[0].action.label == "開始和本汪聊天"
    assert "清華校園情報員" in greeting.alt_text
