"""Isolated main-content extraction worker for visit_webpage."""

from __future__ import annotations

import sys

from trafilatura import extract
from trafilatura.utils import load_html

TRUNCATION_NOTE = "\n\n[已截斷：網頁內文過長，請勿推測未讀取的部分。]"
PRUNE_XPATH = [
    "//script | //style | //nav | //footer | //aside | //form | //iframe | //noscript",
    "//header[not(ancestor::article or ancestor::main)]",
    "//*[@hidden or @aria-hidden='true']",
    "//*[@role='navigation' or @role='banner' or @role='contentinfo' "
    "or @role='complementary' or @role='dialog']",
]
NO_CONTENT = "找不到可讀取的網頁內文；頁面可能需要登入或 JavaScript 才能顯示。"
PARSER_ERROR = "無法解析網頁內文。"


def _extract_content(html: bytes | str, max_chars: int) -> str:
    tree = load_html(html)
    if tree is None:
        raise ValueError(NO_CONTENT)
    # precision 模式會移除 header；文章內的標題仍是有用的內文。
    for header in tree.xpath("//article//header | //main//header"):
        header.tag = "div"
    text = extract(
        tree,
        output_format="txt",
        include_comments=False,
        include_links=False,
        include_images=False,
        include_tables=True,
        favor_precision=True,
        prune_xpath=PRUNE_XPATH,
    )
    if not text or not text.strip():
        raise ValueError(NO_CONTENT)
    text = "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()
    if len(text) > max_chars:
        note = TRUNCATION_NOTE[:max_chars]
        text = text[: max_chars - len(note)].rstrip() + note
    return text


def _main() -> None:
    try:
        length, kind = sys.stdin.buffer.readline(64).decode("ascii").split()
        html = sys.stdin.buffer.read()
        if kind == "text":
            html = html.decode("utf-8")
        elif kind != "bytes":
            raise ValueError
        text = _extract_content(html, int(length))
    except ValueError as error:
        message = NO_CONTENT if str(error) == NO_CONTENT else PARSER_ERROR
        sys.stdout.buffer.write(b"E\n" + message.encode("utf-8"))
    else:
        sys.stdout.buffer.write(b"O\n" + text.encode("utf-8"))


if __name__ == "__main__":
    _main()
