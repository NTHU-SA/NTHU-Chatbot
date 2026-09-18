import unittest

from templates.messages import liff_url, open_web_chat

LIFF_ID = "1234567890-abcdefgh"


class LiffMessageTests(unittest.TestCase):
    def test_url_encodes_and_truncates(self):
        self.assertEqual(liff_url(LIFF_ID), f"https://liff.line.me/{LIFF_ID}")
        self.assertEqual(
            liff_url(LIFF_ID, "a b&c"), f"https://liff.line.me/{LIFF_ID}?q=a%20b%26c"
        )
        self.assertEqual(
            liff_url(LIFF_ID, "hi", "ev/1"),
            f"https://liff.line.me/{LIFF_ID}?s=ev%2F1&q=hi",
        )
        self.assertEqual(
            liff_url(LIFF_ID, session_key="ev-1"),
            f"https://liff.line.me/{LIFF_ID}?s=ev-1",
        )
        long_question = "x" * 600
        self.assertTrue(liff_url(LIFF_ID, long_question).endswith("x" * 500))
        self.assertNotIn("x" * 501, liff_url(LIFF_ID, long_question))

    def test_bubble_variants(self):
        plain = open_web_chat(LIFF_ID)
        self.assertEqual(plain.contents.footer.contents[0].action.label, "在網頁中詢問")
        self.assertEqual(len(plain.contents.body.contents), 1)  # 沒有問題預覽

        with_question = open_web_chat(LIFF_ID, "南大公車", session_key="ev-1")
        preview = with_question.contents.body.contents[1].contents[0].text
        self.assertEqual(preview, "南大公車")
        self.assertIn("?s=ev-1&q=", with_question.contents.footer.contents[0].action.uri)

        greeting = open_web_chat(LIFF_ID, greeting=True)
        self.assertEqual(greeting.contents.footer.contents[0].action.label, "開始和本汪聊天")
        self.assertIn("狗狗情報員", greeting.alt_text)
