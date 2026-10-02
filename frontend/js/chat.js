// Session list, opening/creating/deleting sessions, and sending a message
// with the streamed (SSE) reply.

import { api, readSse } from "./api.js";
import { requestConsent } from "./consent.js";
import {
  autosize, closeSidebar, confirmDialog, el, scrollToBottom, setBusy, setLoading, state,
} from "./dom.js";
import { renderMarkdown, updateScrollHints } from "./markdown.js";
import {
  appendMessage, appendThought, clearMessages, demoteToProgress, finishTool, hideThinking,
  memoryNote, renderSuggestions, retireSuggestions, showThinking, toolNode,
} from "./messages.js";

// --------------------------------------------------------------- sessions
export async function loadSessions() {
  state.sessions = await (await api("/api/sessions")).json();
  renderSessionList();
}

function renderSessionList() {
  el.sessionList.innerHTML = "";
  for (const s of state.sessions) {
    const li = document.createElement("li");
    if (s.id === state.current) li.classList.add("active");
    const btn = document.createElement("button");
    btn.className = "sess-btn";
    btn.type = "button";
    btn.textContent = s.title || "新對話";
    btn.addEventListener("click", () => { openSession(s.id); closeSidebar(); });
    const del = document.createElement("button");
    del.className = "del-btn";
    del.type = "button";
    del.textContent = "×";
    del.setAttribute("aria-label", "刪除對話");
    del.addEventListener("click", () => deleteSession(s.id));
    li.append(btn, del);
    el.sessionList.append(li);
  }
}

// With `origin` (the key carried by a LINE bubble) the server does get-or-create:
// 201 = new session, 200 = the bubble's existing session.
export async function createSession(title, origin) {
  const navigation = ++state.navigation;
  state.current = null;
  setLoading(true);
  let s, created;
  try {
    const res = await api("/api/sessions", {
      method: "POST",
      body: { title: title || null, origin: origin || null },
    });
    s = await res.json();
    created = res.status === 201;
  } catch (err) {
    if (navigation !== state.navigation) return;
    throw err;
  } finally {
    if (navigation === state.navigation) setLoading(false);
  }
  if (navigation !== state.navigation) return;
  if (!state.sessions.some((x) => x.id === s.id)) state.sessions.unshift(s);
  if (!(await openSession(s.id)) || state.current !== s.id) return;
  return { session: s, created };
}

export async function openSession(id) {
  const navigation = ++state.navigation;
  setLoading(true);
  state.current = id;
  const s = state.sessions.find((x) => x.id === id);
  el.title.textContent = (s && s.title) || "新對話";
  renderSessionList();
  clearMessages();
  let msgs;
  try {
    msgs = await (await api(`/api/sessions/${encodeURIComponent(id)}/messages?limit=100`)).json();
  } catch (err) {
    if (navigation !== state.navigation) return false;
    if (err.status === 404) return resyncSessions();
    throw err;
  } finally {
    if (navigation === state.navigation) setLoading(false);
  }
  if (navigation !== state.navigation) return false;
  for (const m of msgs) appendMessage(m.role, m.content, m.tool_calls || []);
  // Only the chips on the latest reply are still answerable; older ones were already passed.
  const boxes = [...el.messages.querySelectorAll(".suggestions")];
  const last = msgs[msgs.length - 1];
  boxes.forEach((box, i) => {
    if (i < boxes.length - 1 || !last || last.role !== "assistant") box.classList.add("used");
  });
  el.empty.hidden = msgs.length > 0;
  scrollToBottom();
  return true;
}

// The server no longer knows our current session (restart, or deleted from
// another device): reload the list and land on a valid session.
async function resyncSessions() {
  const navigation = ++state.navigation;
  setLoading(true);
  state.current = null;
  try {
    await loadSessions();
  } catch (err) {
    if (navigation !== state.navigation) return;
    throw err;
  } finally {
    if (navigation === state.navigation) setLoading(false);
  }
  if (navigation !== state.navigation) return false;
  if (state.sessions.length) return openSession(state.sessions[0].id);
  return Boolean(await createSession());
}

async function deleteSession(id) {
  if (!(await confirmDialog("確定要刪除這個對話嗎？"))) return;
  await api(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" });
  state.sessions = state.sessions.filter((s) => s.id !== id);
  if (state.current === id) {
    state.current = null;
    if (state.sessions.length) await openSession(state.sessions[0].id);
    else await createSession();
  }
  renderSessionList();
}

// --------------------------------------------------------------- sending
export async function send(text, isRetry) {
  text = (text || "").trim();
  if (!text || state.busy || state.loading) return;
  if (!state.current && !(await createSession())) return;
  const sessionId = state.current;
  const navigation = state.navigation;
  setBusy(true);
  retireSuggestions();
  el.input.value = "";
  autosize();

  appendMessage("user", text, []);
  const node = appendMessage("assistant", "", []);
  const bubble = node.querySelector(".bubble");
  const tools = node.querySelector(".tools");
  bubble.classList.add("cursor", "streaming");
  let acc = "";
  let done = false;
  const isCurrentView = () => state.current === sessionId && state.navigation === navigation;
  showThinking(tools);
  scrollToBottom(); // once, so the sent message and the reply's start are in view

  try {
    const res = await api(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: "POST",
      body: { text },
    });
    await readSse(res, (event, data) => {
      if (event === "done") done = true;
      if (!isCurrentView()) return;
      switch (event) {
        case "tool_call_start": {
          hideThinking(tools);
          const t = toolNode(data.name, data.args, null);
          t.dataset.call = data.call_id;
          tools.append(t);
          tools.hidden = false;
          break;
        }
        case "tool_call_end":
          finishTool(node, data.call_id, data);
          showThinking(tools); // model reads the result and thinks again
          break;
        case "thinking":
          appendThought(tools, data.delta);
          break;
        case "suggestions":
          renderSuggestions(node, data.options || []);
          break;
        case "memory":
          memoryNote(node, data);
          break;
        case "interim":
          if (data.discard) bubble.innerHTML = ""; // repeated question, nothing worth keeping
          else demoteToProgress(tools, bubble, data.text);
          acc = "";
          break;
        case "token":
          hideThinking(tools);
          acc += data.delta;
          if (!bubble.firstChild) bubble.append(document.createTextNode(""));
          bubble.firstChild.appendData(data.delta);
          break;
        case "done":
          hideThinking(tools);
          acc = data.content || acc;
          bubble.classList.remove("streaming");
          bubble.innerHTML = renderMarkdown(acc);
          updateScrollHints(bubble);
          break;
        case "error":
          hideThinking(tools);
          node.classList.add("error");
          bubble.textContent = data.message;
          break;
      }
      // no auto-scroll while streaming: the view stays where the user left it
    });
    // Returning to an in-flight session may have loaded its unfinished history.
    if (done && state.current === sessionId && !isCurrentView()) await openSession(sessionId);
    // The first message names the session server-side; refresh titles.
    const s = state.sessions.find((x) => x.id === sessionId);
    if (s && s.message_count === 0) {
      await loadSessions();
      const cur = state.sessions.find((x) => x.id === sessionId);
      if (cur && state.current === sessionId) el.title.textContent = cur.title;
    } else if (s) {
      s.message_count += 2;
    }
  } catch (err) {
    if (!isCurrentView()) return;
    if (err.status === 404 && !isRetry) {
      // Stale session (server restarted): resync and resend once.
      setBusy(false);
      if (!(await resyncSessions())) return;
      return await send(text, true);
    }
    node.classList.add("error");
    if (err.code === "consent_required") {
      bubble.textContent = "請先同意隱私權政策，再重新送出一次。";
      requestConsent();
      return;
    }
    bubble.textContent = err.message === "re-login" ? "登入已過期，重新登入中…" : `發生錯誤：${err.message}`;
  } finally {
    hideThinking(tools);
    bubble.classList.remove("cursor");
    setBusy(false);
  }
}
