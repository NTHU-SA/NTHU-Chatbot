const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");

const frontend = join(__dirname, "..", "frontend");
const html = readFileSync(join(frontend, "index.html"), "utf8");
const css = readFileSync(join(frontend, "style.css"), "utf8");

test("skip link is inside the app made inert during consent", () => {
  const appStart = html.indexOf('<div id="app" class="app">');
  const appEnd = html.indexOf('<div id="overlay" class="overlay">');
  const skip = html.indexOf('<a class="skip-link" href="#input">');
  const input = html.indexOf('<textarea id="input"');
  assert.ok(appStart >= 0 && appEnd > appStart);
  assert.ok(skip > appStart && skip < appEnd);
  assert.ok(input > skip && input < appEnd);
});

test("busy ring has rotation keyframes and reduced-motion disables animations", () => {
  assert.match(css, /\.composer\.busy \.send-btn::after\s*\{[^}]*animation:\s*spin\s+\.8s linear infinite;/);
  assert.match(css, /@keyframes spin\s*\{\s*to\s*\{\s*transform:\s*rotate\(360deg\);\s*\}\s*\}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\*, \*::before, \*::after\s*\{[^}]*animation:\s*none !important;/);
});
