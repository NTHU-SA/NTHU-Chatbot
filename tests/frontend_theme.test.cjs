const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "..", "frontend", "js", "theme.js"), "utf8");
const key = "nthu-chatbot-theme";

function setup({ saved = null, dark = false, blocked = false, hasControls = true } = {}) {
  const handlers = {};
  const warnings = [];
  const writes = [];
  const buttons = ["system", "light", "dark"].map((value) => ({
    dataset: { themePreference: value },
    pressed: null,
    setAttribute(_, pressed) { this.pressed = pressed; },
    addEventListener: (_, fn) => { handlers[`click:${value}`] = fn; },
  }));
  const status = { hidden: true, textContent: "" };
  const root = { dataset: {} };
  const media = {
    matches: dark,
    addEventListener: (_, fn) => { handlers.system = fn; },
  };
  runInNewContext(source, {
    document: {
      readyState: "loading",
      documentElement: root,
      getElementById: () => hasControls ? status : null,
      querySelectorAll: () => hasControls ? buttons : [],
      addEventListener: (_, fn) => { handlers.ready = fn; },
    },
    window: { addEventListener: (_, fn) => { handlers.storage = fn; } },
    localStorage: {
      getItem: () => {
        if (blocked) throw new Error("blocked");
        return saved;
      },
      setItem: (name, value) => {
        if (blocked) throw new Error("blocked");
        writes.push([name, value]);
      },
    },
    matchMedia: () => media,
    console: { warn: (message) => warnings.push(message) },
  });
  const selected = () => buttons.find((button) => button.pressed === "true").dataset.themePreference;
  return { handlers, warnings, writes, buttons, selected, status, root, media };
}

test("system theme applies before controls load and follows OS changes", () => {
  const ctx = setup({ dark: true });
  assert.equal(ctx.root.dataset.theme, "dark");
  assert.equal(ctx.selected(), "system");
  ctx.handlers.ready();
  ctx.media.matches = false;
  ctx.handlers.system();
  assert.equal(ctx.root.dataset.theme, "light");
  assert.deepEqual(ctx.writes, []);
});

test("saved explicit preference overrides the OS without rewriting storage", () => {
  for (const saved of ["light", "dark"]) {
    const ctx = setup({ saved, dark: saved === "light" });
    ctx.handlers.ready();
    ctx.handlers.system();
    assert.equal(ctx.root.dataset.theme, saved);
    assert.equal(ctx.selected(), saved);
    assert.deepEqual(ctx.writes, []);
  }
});

test("all three choices persist locally and system mode resumes OS updates", () => {
  const ctx = setup();
  ctx.handlers.ready();
  for (const value of ["dark", "light", "system"]) {
    ctx.handlers[`click:${value}`]();
    assert.equal(ctx.root.dataset.theme, value === "system" ? "light" : value);
    assert.deepEqual(ctx.writes.at(-1), [key, value]);
    assert.equal(ctx.selected(), value);
    assert.equal(ctx.buttons.filter((button) => button.pressed === "true").length, 1);
  }
  ctx.media.matches = true;
  ctx.handlers.system();
  assert.equal(ctx.root.dataset.theme, "dark");
});

test("storage events synchronize tabs and removing preferences resumes system mode", () => {
  const ctx = setup({ saved: "light", dark: true });
  ctx.handlers.storage({ key: "unrelated", newValue: "dark" });
  assert.equal(ctx.root.dataset.theme, "light");
  ctx.handlers.storage({ key, newValue: "dark" });
  assert.equal(ctx.root.dataset.theme, "dark");
  assert.equal(ctx.selected(), "dark");
  ctx.handlers.storage({ key, newValue: null });
  assert.equal(ctx.selected(), "system");
  ctx.handlers.storage({ key: null, newValue: null });
  assert.equal(ctx.root.dataset.theme, "dark");
});

test("blocked storage still permits switching and reports the persistence failure", () => {
  const ctx = setup({ blocked: true });
  ctx.handlers.ready();
  assert.equal(ctx.status.hidden, false);
  ctx.handlers["click:dark"]();
  assert.equal(ctx.root.dataset.theme, "dark");
  assert.equal(ctx.status.hidden, false);
  assert.ok(ctx.warnings.length > 0);
  assert.deepEqual(ctx.writes, []);
});

test("invalid stored or incoming choices are reported without corrupting the theme", () => {
  const ctx = setup({ saved: "invalid" });
  ctx.handlers.ready();
  assert.equal(ctx.root.dataset.theme, "light");
  ctx.handlers.storage({ key, newValue: "invalid" });
  ctx.buttons[2].dataset.themePreference = "invalid";
  ctx.handlers["click:dark"]();
  assert.equal(ctx.root.dataset.theme, "light");
  assert.equal(ctx.selected(), "system");
  assert.equal(ctx.warnings.length, 3);
  assert.deepEqual(ctx.writes, []);
});

test("privacy pages apply saved preferences without chat controls", () => {
  const ctx = setup({ saved: "dark", hasControls: false });
  ctx.handlers.ready();
  assert.equal(ctx.root.dataset.theme, "dark");
  ctx.handlers.storage({ key, newValue: "light" });
  assert.equal(ctx.root.dataset.theme, "light");
});
