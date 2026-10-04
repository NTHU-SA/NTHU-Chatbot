const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const test = require("node:test");
const { runInNewContext } = require("node:vm");

const source = readFileSync(join(__dirname, "..", "frontend", "js", "dom.js"), "utf8")
  .replaceAll("export ", "");

function setup({ desktop = false, initialMessages = 0 } = {}) {
  const nodes = new Map();
  const observers = {};
  const frames = [];
  let focused = null;
  const node = (id) => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {
        nodeType: 1,
        hidden: true,
        inert: false,
        attrs: {},
        children: [],
        handlers: {},
        classList: {
          add: (value) => classes.add(value),
          remove: (value) => classes.delete(value),
          contains: (value) => classes.has(value),
          toggle: (value, on) => on ? classes.add(value) : classes.delete(value),
        },
        setAttribute(name, value) { this.attrs[name] = value; },
        addEventListener(name, fn) { this.handlers[name] = fn; },
        contains: (element) => element === focused && focused === nodes.get("closeSidebar"),
        focus() { focused = this; },
      });
    }
    return nodes.get(id);
  };
  const messages = node("messages");
  for (let i = 0; i < initialMessages; i++) {
    const child = node(`message-${i}`);
    child.parentNode = messages;
    messages.children.push(child);
  }
  messages.scrollHeight = 1000;
  messages.clientHeight = 400;
  let top = 600;
  Object.defineProperty(messages, "scrollTop", {
    get: () => top,
    set: (value) => { top = Math.min(value, messages.scrollHeight - messages.clientHeight); },
  });
  const media = { matches: desktop };
  const ctx = runInNewContext(`${source}
    ({ bindMessageScrolling, scrollToBottom, syncSidebarLayout, openSidebar, closeSidebar })`, {
    document: {
      getElementById: node,
      get activeElement() { return focused; },
    },
    matchMedia: () => media,
    requestAnimationFrame: (fn) => { frames.push(fn); return frames.length; },
    ResizeObserver: class {
      constructor(fn) {
        observers.resize = fn;
        observers.registrations = [];
        observers.unregistrations = [];
        observers.disconnects = 0;
      }
      observe(element) { observers.registrations.push(element); }
      unobserve(element) { observers.unregistrations.push(element); }
      disconnect() { observers.disconnects++; }
    },
    MutationObserver: class {
      constructor(fn) { observers.mutation = fn; }
      observe(target, options) { observers.mutationTarget = target; observers.mutationOptions = options; }
    },
  });
  const flush = () => { while (frames.length) frames.shift()(); };
  return { ...ctx, node, messages, media, observers, frames, flush };
}

test("streamed text, tool cards, and final layout follow the latest message", () => {
  const ctx = setup({ initialMessages: 1 });
  ctx.bindMessageScrolling();
  ctx.flush();
  assert.deepEqual(JSON.parse(JSON.stringify(ctx.observers.mutationOptions)), { childList: true });
  assert.equal(ctx.observers.mutationTarget, ctx.messages);
  assert.equal(ctx.observers.registrations.length, 2);
  for (const height of [1300, 1600, 2100]) {
    ctx.messages.scrollHeight = height;
    ctx.observers.resize();
    assert.equal(ctx.frames.length, 1);
    ctx.flush();
    assert.equal(ctx.messages.scrollTop, height - 400);
    assert.equal(ctx.node("scrollBtn").hidden, true);
  }
  assert.equal(ctx.observers.registrations.length, 2);
  assert.equal(ctx.observers.disconnects, 0);
});

test("direct message changes update resize targets without re-registering history", () => {
  const ctx = setup({ initialMessages: 100 });
  ctx.bindMessageScrolling();
  ctx.flush();
  assert.equal(ctx.observers.registrations.length, 101);
  const added = ctx.node("new-message");
  added.parentNode = ctx.messages;
  ctx.messages.children.push(added);
  ctx.observers.mutation([{ addedNodes: [added], removedNodes: [] }]);
  ctx.flush();
  assert.equal(ctx.observers.registrations.length, 102);
  assert.equal(ctx.observers.registrations.at(-1), added);
  ctx.messages.scrollHeight = 1400;
  ctx.observers.resize();
  ctx.flush();
  assert.equal(ctx.messages.scrollTop, 1000);

  ctx.messages.children.pop();
  added.parentNode = null;
  ctx.observers.mutation([{ addedNodes: [], removedNodes: [added] }]);
  ctx.flush();
  assert.deepEqual(ctx.observers.unregistrations, [added]);
  assert.equal(ctx.observers.registrations.length, 102);
  assert.equal(ctx.observers.disconnects, 0);
});

test("batched message moves and text nodes do not churn resize observations", () => {
  const ctx = setup({ initialMessages: 1 });
  ctx.bindMessageScrolling();
  ctx.flush();
  const child = ctx.node("message-0");
  ctx.observers.mutation([
    { addedNodes: [], removedNodes: [child] },
    { addedNodes: [child, { nodeType: 3, parentNode: ctx.messages }], removedNodes: [] },
  ]);
  ctx.flush();
  assert.deepEqual(ctx.observers.registrations, [ctx.messages, child]);
  assert.deepEqual(ctx.observers.unregistrations, []);
});

test("reading history pauses following; latest button and sending resume it", () => {
  const ctx = setup();
  ctx.bindMessageScrolling();
  ctx.flush();
  ctx.messages.scrollTop = 100;
  ctx.messages.handlers.scroll();
  ctx.messages.scrollHeight = 1500;
  ctx.observers.resize();
  ctx.flush();
  assert.equal(ctx.messages.scrollTop, 100);
  assert.equal(ctx.node("scrollBtn").hidden, false);
  ctx.scrollToBottom();
  ctx.messages.scrollHeight = 1700;
  ctx.observers.resize();
  ctx.flush();
  assert.equal(ctx.messages.scrollTop, 1300);
  assert.equal(ctx.node("scrollBtn").hidden, true);
});

test("scrolling back near the bottom resumes following and viewport changes stay pinned", () => {
  const ctx = setup();
  ctx.bindMessageScrolling();
  ctx.flush();
  ctx.messages.scrollTop = 100;
  ctx.messages.handlers.scroll();
  ctx.messages.scrollTop = 580;
  ctx.messages.handlers.scroll();
  ctx.messages.clientHeight = 250;
  ctx.observers.resize();
  ctx.flush();
  assert.equal(ctx.messages.scrollTop, 750);
});

test("desktop sidebar stays accessible when selecting a conversation or closing it", () => {
  const ctx = setup({ desktop: true });
  ctx.syncSidebarLayout();
  ctx.closeSidebar();
  assert.equal(ctx.node("sidebar").inert, false);
  assert.equal(ctx.node("sidebar").attrs["aria-hidden"], "false");
  assert.equal(ctx.node("sidebar").classList.contains("open"), true);
  assert.equal(ctx.node("backdrop").hidden, true);
});

test("mobile drawer and desktop transitions synchronize visibility and focus", () => {
  const ctx = setup();
  ctx.syncSidebarLayout();
  assert.equal(ctx.node("sidebar").inert, true);
  ctx.openSidebar();
  assert.equal(ctx.node("sidebar").inert, false);
  assert.equal(ctx.node("backdrop").hidden, false);
  ctx.closeSidebar();
  assert.equal(ctx.node("sidebar").inert, true);
  assert.equal(ctx.node("menuBtn").attrs["aria-expanded"], "false");
  ctx.openSidebar();
  ctx.media.matches = true;
  ctx.syncSidebarLayout();
  assert.equal(ctx.node("sidebar").inert, false);
  assert.equal(ctx.node("backdrop").hidden, true);
  ctx.media.matches = false;
  ctx.syncSidebarLayout();
  assert.equal(ctx.node("sidebar").inert, true);
});
