const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = readFileSync(join(__dirname, "..", "frontend", "js", "flex-preview.js"), "utf8");
const dataScript = readFileSync(join(__dirname, "..", "frontend", "flex-preview-data.js"), "utf8");
const contents = {
  type: "bubble",
  body: {
    type: "box",
    layout: "vertical",
    contents: [{ type: "text", text: '校園 "JSON"\n第二行', size: "sm" }],
  },
};

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.events = {};
    this.attributes = {};
    this.dataset = {};
    this.style = { setProperty() {} };
  }
  append(...children) { this.children.push(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, handler) { this.events[name] = handler; }
  focus() { this.focused = true; }
  select() { this.selected = true; }
}

function setup(clipboard) {
  const gallery = new Element("div");
  const fields = { gallery, "sample-picker": new Element("select"), "action-detail": new Element("p") };
  const warnings = [];
  vm.runInNewContext(script, {
    document: {
      getElementById: (id) => fields[id],
      createElement: (tag) => new Element(tag),
    },
    window: {
      FLEX_PREVIEW: [{
        id: "sample", title: "Sample", description: "Example",
        messages: [{ type: "flex", altText: "Sample", contents }],
      }],
      addEventListener() {},
    },
    navigator: { clipboard },
    URL: { createObjectURL: () => "blob:preview", revokeObjectURL() {} },
    Blob,
    console: { warn: (...args) => warnings.push(args) },
  });
  const message = gallery.children[0].children[2].children[0];
  const [tools, fallback] = message.children;
  const [copy, feedback] = tools.children;
  return { copy, feedback, fallback, warnings };
}

test("generated data expands repeated subtrees without sharing mutable objects", () => {
  const context = { window: {} };
  vm.runInNewContext(dataScript, context);
  const samples = context.window.FLEX_PREVIEW;
  assert.ok(samples.length > 2);
  const first = samples[0].messages[0].contents;
  const second = samples[1].messages[0].contents;
  assert.deepEqual(JSON.parse(JSON.stringify(first.styles)), JSON.parse(JSON.stringify(second.styles)));
  assert.notEqual(first.styles, second.styles);
  assert.notEqual(first.styles.body, second.styles.body);
});

test("copy writes the exact Flex container, not the message array", async () => {
  let copied;
  const state = setup({ writeText: async (json) => { copied = json; } });
  await state.copy.events.click();
  assert.deepEqual(JSON.parse(copied), contents);
  assert.equal(state.feedback.textContent, "已複製，可貼進 Flex Simulator。");
  assert.equal(state.fallback.hidden, true);
  assert.equal(state.copy.disabled, false);
  assert.equal(state.warnings.length, 0);
});

test("denied clipboard access exposes selectable JSON and permits retry", async () => {
  let deny = true;
  const state = setup({
    writeText: async () => { if (deny) throw new Error("Permission denied"); },
  });
  await state.copy.events.click();
  assert.deepEqual(JSON.parse(state.fallback.value), contents);
  assert.equal(state.fallback.hidden, false);
  assert.equal(state.fallback.focused, true);
  assert.equal(state.fallback.selected, true);
  assert.match(state.feedback.textContent, /手動複製/);
  assert.equal(state.copy.disabled, false);
  assert.equal(state.copy.textContent, "複製 JSON");
  assert.equal(state.warnings.length, 1);
  deny = false;
  await state.copy.events.click();
  assert.equal(state.fallback.hidden, true);
  assert.match(state.feedback.textContent, /已複製/);
});

test("missing Clipboard API provides the same explicit manual fallback", async () => {
  const state = setup(undefined);
  await state.copy.events.click();
  assert.equal(state.fallback.hidden, false);
  assert.deepEqual(JSON.parse(state.fallback.value), contents);
  assert.match(state.feedback.textContent, /無法自動複製/);
});
