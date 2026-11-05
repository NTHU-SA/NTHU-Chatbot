const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const { runInNewContext } = require("node:vm");

const frontend = join(__dirname, "..", "frontend");
const { marked } = require(join(frontend, "vendor", "marked.min.js"));
const source = readFileSync(join(frontend, "js", "markdown.js"), "utf8").replaceAll("export ", "");
// markdown.js configures the shared `marked`; sanitising is DOMPurify's job and not under test.
runInNewContext(source, { marked, DOMPurify: { addHook() {}, sanitize: (html) => html } });

const inline = (text) => marked.parseInline(text);

test("bold closes after CJK punctuation followed by a letter", () => {
  assert.equal(inline("1. **地點：**台北"), "1. <strong>地點：</strong>台北");
  assert.equal(inline("**營業時間（平日）**是 8:00"), "<strong>營業時間（平日）</strong>是 8:00");
  assert.equal(inline("請看**「宿舍」**的規定"), "請看<strong>「宿舍」</strong>的規定");
});

test("bold that CommonMark already handles is unchanged", () => {
  assert.equal(inline("這是**重點**喔"), "這是<strong>重點</strong>喔");
  assert.equal(inline("時間：**8:00–17:00**。"), "時間：<strong>8:00–17:00</strong>。");
  assert.equal(inline("**粗 *斜* 體**"), "<strong>粗 <em>斜</em> 體</strong>");
  assert.equal(inline("***都要***"), "<em><strong>都要</strong></em>");
  assert.equal(inline("**[連結](https://nthu.edu.tw)**"),
    '<strong><a href="https://nthu.edu.tw">連結</a></strong>');
});

test("markers that are not bold stay literal", () => {
  assert.equal(inline("a ** b ** c"), "a ** b ** c");
  assert.equal(inline("`**地點：**台北`"), "<code>**地點：**台北</code>");
  assert.equal(inline("\\*\\*地點：\\*\\*台北"), "**地點：**台北");
});
