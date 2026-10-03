// Message bubbles, tool cards, the "思考中" row and quick-reply chips.

import { send } from "./chat.js";
import { el, state } from "./dom.js";
import { escapeHtml, renderMarkdown, updateScrollHints } from "./markdown.js";

const SUGGEST_TOOL = "suggest_replies";

// Friendly names for the tool cards; the raw name stays in the tooltip.
const TOOL_LABELS = {
  search_campus: "查校園地點",
  get_next_buses: "查公車時刻",
  get_bus_stops: "查公車站牌",
  search_courses: "查課程",
  get_announcements: "查公告",
  find_dining: "查餐廳",
  get_library_info: "查圖書館",
  get_newsletters: "查電子報",
  get_energy_usage: "查用電",
  web_search: "搜尋清大網站",
};

const TOOL_ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true">' +
  '<circle class="ring" cx="12" cy="12" r="9"/>' +
  '<path class="mark check" d="M7.5 12.5l3 3l6-6.5"/>' +
  '<path class="mark cross" d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>' +
  "</svg>";

export function clearMessages() {
  for (const n of [...el.messages.children]) {
    if (n === el.empty || n === el.loading) continue;
    hideThinking(n.querySelector(".tools"));
    n.remove();
  }
}

export function appendMessage(role, content, toolCalls, { animate = false } = {}) {
  const node = el.tplMessage.content.firstElementChild.cloneNode(true);
  node.classList.add(role);
  if (animate) node.classList.add("arriving");
  const bubble = node.querySelector(".bubble");
  bubble.innerHTML = role === "assistant" ? renderMarkdown(content) : escapeHtml(content);
  const tools = node.querySelector(".tools");
  let shown = 0;
  for (const tc of toolCalls) {
    if (tc.name === SUGGEST_TOOL) continue; // rendered as chips below the bubble
    tools.append(toolNode(tc.name, tc.args, tc, true));
    shown++;
  }
  tools.hidden = shown === 0;
  el.messages.append(node);
  for (const tc of toolCalls) {
    if (tc.name === SUGGEST_TOOL) renderSuggestions(node, (tc.args || {}).options || []);
  }
  updateScrollHints(bubble); // needs layout, so after it is in the DOM
  if (role === "assistant" && content) addCopyButton(node, content);
  el.empty.hidden = true;
  return node;
}

function actionsRow(node) {
  let row = node.querySelector(".msg-actions");
  if (!row) {
    row = document.createElement("div");
    row.className = "msg-actions";
    node.append(row);
  }
  return row;
}

// Copy the reply's Markdown source (what the user would paste elsewhere).
export function addCopyButton(node, text) {
  if (node.querySelector(".copy-btn")) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-action copy-btn";
  btn.textContent = "複製";
  btn.setAttribute("aria-label", "複製這則回覆");
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = "已複製";
    } catch (_) {
      btn.textContent = "無法複製";
    }
    setTimeout(() => { btn.textContent = "複製"; }, 1500);
  });
  actionsRow(node).append(btn);
}

// Error bubble with an optional retry action.
export function showError(node, message, onRetry) {
  node.classList.add("error");
  node.querySelector(".bubble").textContent = message;
  if (!onRetry || node.querySelector(".retry-btn")) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "msg-action retry-btn";
  btn.textContent = "重試";
  btn.addEventListener("click", () => {
    if (state.busy || state.loading) return;
    btn.remove();
    onRetry();
  });
  actionsRow(node).append(btn);
}

// Quick-reply chips the model offers when it asks a clarifying question.
// Tapping one sends it as the next message; the row is then greyed out.
export function renderSuggestions(node, options) {
  if (!options.length) return;
  let box = node.querySelector(".suggestions");
  if (!box) {
    box = document.createElement("div");
    box.className = "suggestions";
    node.append(box);
  }
  box.innerHTML = "";
  for (const text of options) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = text;
    btn.addEventListener("click", () => {
      if (state.busy || state.loading) return;
      send(text);
    });
    box.append(btn);
  }
}

const MEMORY_LABEL = { nickname: "稱呼", department: "系所", memory: "" };

// One-line note under a reply when the assistant saved or forgot something.
export function memoryNote(node, data) {
  const items = (data.items || []).map((i) => (MEMORY_LABEL[i.kind] ? `${MEMORY_LABEL[i.kind]}「${i.value}」` : `「${i.value}」`));
  if (!items.length) return;
  const note = document.createElement("div");
  note.className = "memory-note";
  note.textContent = data.action === "forgotten"
    ? `🗑 已忘記：${items.join("、")}`
    : `📝 已記住：${items.join("、")}（可在側欄「我的資料」修改）`;
  node.append(note);
}

// Any new message (typed or tapped) retires the quick-reply chips still on screen.
export function retireSuggestions() {
  for (const box of el.messages.querySelectorAll(".suggestions:not(.used)")) box.classList.add("used");
}

function toolLabel(status) {
  return status === "running" ? "正在使用" : status === "ok" ? "已使用" : "呼叫失敗";
}

// `done` is null while running, otherwise {ok, duration_ms, result_preview}.
// `isStatic` renders the final state without replaying the animation (history).
export function toolNode(name, args, done, isStatic) {
  const status = done ? (done.ok ? "ok" : "err") : "running";
  const d = document.createElement("details");
  d.className = "tool " + status + (isStatic ? " static" : "");
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "tool-icon";
  icon.innerHTML = TOOL_ICON;
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = toolLabel(status);
  const nm = document.createElement("span");
  nm.className = "name";
  nm.textContent = TOOL_LABELS[name] || name;
  nm.title = name;
  summary.append(icon, label, nm);
  if (done && done.duration_ms != null) {
    const dur = document.createElement("span");
    dur.className = "dur";
    dur.textContent = `${(done.duration_ms / 1000).toFixed(1)}s`;
    summary.append(dur);
  }
  const pre = document.createElement("pre");
  pre.textContent = `參數: ${JSON.stringify(args || {}, null, 1)}` + (done && done.result_preview ? `\n結果: ${done.result_preview}` : "");
  d.append(summary, pre);
  return d;
}

export function finishTool(node, callId, data) {
  const t = node.querySelector(`.tool[data-call="${CSS.escape(callId)}"]`);
  if (!t) return;
  const status = data.ok ? "ok" : "err";
  t.classList.remove("running");
  t.classList.add(status);
  t.querySelector(".label").textContent = toolLabel(status);
  const dur = document.createElement("span");
  dur.className = "dur";
  dur.textContent = `${(data.duration_ms / 1000).toFixed(1)}s`;
  t.querySelector("summary").append(dur);
  if (data.result_preview) t.querySelector("pre").textContent += `\n結果: ${data.result_preview}`;
}

// "思考中 · 12s" row shown whenever the model is working but nothing is
// streaming yet (before the first token, and again after each tool result).
// When the model streams a reasoning summary it is shown inside the row and
// the row is kept afterwards as a collapsed "已思考" card.
export function showThinking(tools) {
  if (tools.querySelector(".thinking")) return;
  const row = document.createElement("details");
  row.className = "tool running thinking";
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "tool-icon";
  icon.innerHTML = TOOL_ICON;
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = "思考中";
  const dur = document.createElement("span");
  dur.className = "dur";
  summary.append(icon, label, dur);
  const thought = document.createElement("div");
  thought.className = "thought";
  thought.hidden = true;
  row.append(summary, thought);
  const started = performance.now();
  const tick = () => { dur.textContent = `${Math.round((performance.now() - started) / 1000)}s`; };
  tick();
  row._timer = setInterval(tick, 1000);
  tools.append(row);
  tools.hidden = false;
}

// Text the model said before calling a tool is a remark ("本汪查一下！"), not the
// answer: move it out of the bubble into a collapsed 過程 card and start over.
export function demoteToProgress(tools, bubble, text) {
  if (!text) return;
  const row = document.createElement("details");
  row.className = "tool ok static";
  const summary = document.createElement("summary");
  const icon = document.createElement("span");
  icon.className = "tool-icon";
  icon.innerHTML = TOOL_ICON;
  const label = document.createElement("span");
  label.className = "label";
  label.textContent = "過程";
  summary.append(icon, label);
  const body = document.createElement("div");
  body.className = "thought";
  body.textContent = text;
  row.append(summary, body);
  tools.append(row);
  tools.hidden = false;
  bubble.innerHTML = "";
}

export function appendThought(tools, delta) {
  showThinking(tools);
  const row = tools.querySelector(".thinking");
  const thought = row.querySelector(".thought");
  thought.hidden = false;
  row.open = true;
  thought.textContent += delta;
}

export function hideThinking(tools) {
  const row = tools.querySelector(".thinking");
  if (!row) return;
  clearInterval(row._timer);
  const thought = row.querySelector(".thought");
  if (thought && thought.textContent.trim()) {
    row.classList.remove("running", "thinking");
    row.classList.add("ok", "static");
    row.querySelector(".label").textContent = "已思考";
    row.open = false;
  } else {
    row.remove();
  }
  if (!tools.children.length) tools.hidden = true;
}
