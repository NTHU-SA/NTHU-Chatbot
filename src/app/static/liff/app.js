/* NTHU campus assistant — LIFF chat page.
 * Talks only to our own backend; identity comes from the LIFF id_token which the
 * backend verifies with LINE. No secrets live here.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    app: $("app"),
    menuBtn: $("menuBtn"),
    newBtn: $("newBtn"),
    title: $("sessionTitle"),
    sidebar: $("sidebar"),
    closeSidebar: $("closeSidebar"),
    sessionList: $("sessionList"),
    userBox: $("userBox"),
    backdrop: $("backdrop"),
    messages: $("messages"),
    empty: $("empty"),
    composer: $("composer"),
    input: $("input"),
    sendBtn: $("sendBtn"),
    overlay: $("overlay"),
    overlayText: $("overlayText"),
    overlayRetry: $("overlayRetry"),
    confirmDlg: $("confirmDlg"),
    tplMessage: $("tplMessage"),
  };

  const state = {
    idToken: null,
    sessions: [],
    current: null, // session id
    busy: false,
  };

  // ------------------------------------------------------------------ utils
  const escapeHtml = (s) =>
    s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Full Markdown via marked, then DOMPurify so LLM output can never inject
  // scripts/handlers. Links open in a new tab; only http(s)/mailto allowed.
  marked.use({ gfm: true, breaks: true });
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") {
      node.setAttribute("target", "_blank");
      node.setAttribute("rel", "noopener noreferrer");
    }
  });
  const PURIFY_OPTS = {
    ALLOWED_TAGS: ["p", "br", "strong", "em", "del", "code", "pre", "blockquote", "ul", "ol", "li",
      "h1", "h2", "h3", "h4", "h5", "h6", "a", "hr", "table", "thead", "tbody", "tr", "th", "td", "span"],
    ALLOWED_ATTR: ["href", "title", "class", "align", "target", "rel"],
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:)/i,
  };
  // Tables and code blocks get a horizontal scroll wrapper so only they scroll,
  // with a persistent scrollbar plus a right-edge fade while more content exists.
  function renderMarkdown(text) {
    const tpl = document.createElement("template");
    tpl.innerHTML = DOMPurify.sanitize(marked.parse(text || ""), PURIFY_OPTS);
    for (const node of tpl.content.querySelectorAll("table, pre")) {
      const wrap = document.createElement("div");
      wrap.className = "scroll-x";
      const inner = document.createElement("div");
      inner.className = "scroll-x-inner";
      node.replaceWith(wrap);
      wrap.append(inner);
      inner.append(node);
    }
    return tpl.innerHTML;
  }

  function updateScrollHint(inner) {
    const more = inner.scrollWidth - inner.clientWidth - inner.scrollLeft > 2;
    inner.parentElement.classList.toggle("more-right", more);
  }
  function updateScrollHints(root) {
    for (const inner of root.querySelectorAll(".scroll-x-inner")) updateScrollHint(inner);
  }

  function showOverlay(text, retry) {
    el.overlay.hidden = false;
    el.overlayText.textContent = text;
    el.overlayRetry.hidden = !retry;
  }
  const hideOverlay = () => (el.overlay.hidden = true);

  function scrollToBottom() {
    el.messages.scrollTop = el.messages.scrollHeight;
  }

  function setBusy(b) {
    state.busy = b;
    el.sendBtn.disabled = b || !el.input.value.trim();
    el.newBtn.disabled = b;
  }

  // -------------------------------------------------------------------- api
  async function api(path, opts = {}) {
    const headers = Object.assign({ Authorization: `Bearer ${state.idToken}` }, opts.headers || {});
    if (opts.body && typeof opts.body !== "string") {
      headers["Content-Type"] = "application/json";
      opts = Object.assign({}, opts, { body: JSON.stringify(opts.body) });
    }
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (res.status === 401) {
      // id_token expired — re-login through LIFF and come back.
      liff.logout();
      liff.login({ redirectUri: location.href });
      throw new Error("re-login");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return res;
  }

  // --------------------------------------------------------------- sessions
  async function loadSessions() {
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
  async function createSession(title, origin) {
    const res = await api("/api/sessions", {
      method: "POST",
      body: { title: title || null, origin: origin || null },
    });
    const s = await res.json();
    const created = res.status === 201;
    if (!state.sessions.some((x) => x.id === s.id)) state.sessions.unshift(s);
    await openSession(s.id);
    return { session: s, created };
  }

  async function openSession(id) {
    state.current = id;
    const s = state.sessions.find((x) => x.id === id);
    el.title.textContent = (s && s.title) || "新對話";
    renderSessionList();
    clearMessages();
    let msgs;
    try {
      msgs = await (await api(`/api/sessions/${id}/messages?limit=100`)).json();
    } catch (err) {
      if (err.status === 404) return resyncSessions();
      throw err;
    }
    for (const m of msgs) appendMessage(m.role, m.content, m.tool_calls || []);
    // Only the chips on the latest reply are still answerable; older ones were already passed.
    const boxes = [...el.messages.querySelectorAll(".suggestions")];
    const last = msgs[msgs.length - 1];
    boxes.forEach((box, i) => {
      if (i < boxes.length - 1 || !last || last.role !== "assistant") box.classList.add("used");
    });
    el.empty.hidden = msgs.length > 0;
    scrollToBottom();
  }

  // The server no longer knows our current session (restart, or deleted from
  // another device): reload the list and land on a valid session.
  async function resyncSessions() {
    state.current = null;
    await loadSessions();
    if (state.sessions.length) await openSession(state.sessions[0].id);
    else await createSession();
  }

  function confirmDialog(text) {
    return new Promise((resolve) => {
      $("confirmText").textContent = text;
      el.confirmDlg.returnValue = "cancel";
      el.confirmDlg.addEventListener("close", () => resolve(el.confirmDlg.returnValue === "ok"), { once: true });
      el.confirmDlg.showModal();
    });
  }

  async function deleteSession(id) {
    if (!(await confirmDialog("確定要刪除這個對話嗎？"))) return;
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    state.sessions = state.sessions.filter((s) => s.id !== id);
    if (state.current === id) {
      state.current = null;
      if (state.sessions.length) await openSession(state.sessions[0].id);
      else await createSession();
    }
    renderSessionList();
  }

  // --------------------------------------------------------------- messages
  function clearMessages() {
    for (const n of [...el.messages.children]) if (n !== el.empty) n.remove();
  }

  const SUGGEST_TOOL = "suggest_replies";

  function appendMessage(role, content, toolCalls) {
    const node = el.tplMessage.content.firstElementChild.cloneNode(true);
    node.classList.add(role);
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
    el.empty.hidden = true;
    return node;
  }

  // Quick-reply chips the model offers when it asks a clarifying question.
  // Tapping one sends it as the next message; the row is then greyed out.
  function renderSuggestions(node, options) {
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
        if (state.busy) return;
        send(text);
      });
      box.append(btn);
    }
  }

  const TOOL_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<circle class="ring" cx="12" cy="12" r="9"/>' +
    '<path class="mark check" d="M7.5 12.5l3 3l6-6.5"/>' +
    '<path class="mark cross" d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>' +
    "</svg>";

  function toolLabel(state) {
    return state === "running" ? "正在使用" : state === "ok" ? "已使用" : "呼叫失敗";
  }

  // `done` is null while running, otherwise {ok, duration_ms, result_preview}.
  // `isStatic` renders the final state without replaying the animation (history).
  function toolNode(name, args, done, isStatic) {
    const state = done ? (done.ok ? "ok" : "err") : "running";
    const d = document.createElement("details");
    d.className = "tool " + state + (isStatic ? " static" : "");
    const summary = document.createElement("summary");
    const icon = document.createElement("span");
    icon.className = "tool-icon";
    icon.innerHTML = TOOL_ICON;
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = toolLabel(state);
    const nm = document.createElement("span");
    nm.className = "name";
    nm.textContent = name;
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

  function finishTool(node, callId, data) {
    const t = node.querySelector(`.tool[data-call="${CSS.escape(callId)}"]`);
    if (!t) return;
    const state = data.ok ? "ok" : "err";
    t.classList.remove("running");
    t.classList.add(state);
    t.querySelector(".label").textContent = toolLabel(state);
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
  function showThinking(tools) {
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
  function demoteToProgress(tools, bubble, text) {
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

  function appendThought(tools, delta) {
    showThinking(tools);
    const row = tools.querySelector(".thinking");
    const thought = row.querySelector(".thought");
    thought.hidden = false;
    row.open = true;
    thought.textContent += delta;
  }
  function hideThinking(tools) {
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

  // Parse a text/event-stream body coming from fetch (EventSource is GET-only).
  async function readSse(res, onEvent) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        let event = "message", data = "";
        for (const line of chunk.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (data) onEvent(event, JSON.parse(data));
      }
    }
  }

  // Any new message (typed or tapped) retires the quick-reply chips still on screen.
  function retireSuggestions() {
    for (const box of el.messages.querySelectorAll(".suggestions:not(.used)")) box.classList.add("used");
  }

  async function send(text, isRetry) {
    text = (text || "").trim();
    if (!text || state.busy) return;
    if (!state.current) await createSession();
    setBusy(true);
    retireSuggestions();
    el.input.value = "";
    autosize();

    appendMessage("user", text, []);
    const node = appendMessage("assistant", "", []);
    const bubble = node.querySelector(".bubble");
    const tools = node.querySelector(".tools");
    bubble.classList.add("cursor");
    let acc = "";
    showThinking(tools);
    scrollToBottom(); // once, so the sent message and the reply's start are in view

    try {
      const res = await api(`/api/sessions/${state.current}/messages`, { method: "POST", body: { text } });
      await readSse(res, (event, data) => {
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
          case "interim":
            demoteToProgress(tools, bubble, data.text);
            acc = "";
            break;
          case "token":
            hideThinking(tools);
            acc += data.delta;
            bubble.innerHTML = renderMarkdown(acc);
            updateScrollHints(bubble);
            break;
          case "done":
            hideThinking(tools);
            acc = data.content || acc;
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
      // The first message names the session server-side; refresh titles.
      const s = state.sessions.find((x) => x.id === state.current);
      if (s && s.message_count === 0) {
        await loadSessions();
        const cur = state.sessions.find((x) => x.id === state.current);
        if (cur) el.title.textContent = cur.title;
      } else if (s) {
        s.message_count += 2;
      }
    } catch (err) {
      if (err.status === 404 && !isRetry) {
        // Stale session (server restarted): resync and resend once.
        setBusy(false);
        await resyncSessions();
        return send(text, true);
      }
      node.classList.add("error");
      bubble.textContent = err.message === "re-login" ? "登入已過期，重新登入中…" : `發生錯誤：${err.message}`;
    } finally {
      hideThinking(tools);
      bubble.classList.remove("cursor");
      setBusy(false);
    }
  }

  // ------------------------------------------------------------------- UI
  function openSidebar() {
    el.sidebar.classList.add("open");
    el.backdrop.hidden = false;
    el.menuBtn.setAttribute("aria-expanded", "true");
  }
  function closeSidebar() {
    el.sidebar.classList.remove("open");
    el.backdrop.hidden = true;
    el.menuBtn.setAttribute("aria-expanded", "false");
  }

  function autosize() {
    if (CSS.supports("field-sizing", "content")) return;
    el.input.style.height = "auto";
    el.input.style.height = Math.min(el.input.scrollHeight, 140) + "px";
  }

  function bindUi() {
    el.menuBtn.addEventListener("click", openSidebar);
    el.closeSidebar.addEventListener("click", closeSidebar);
    el.backdrop.addEventListener("click", closeSidebar);
    el.newBtn.addEventListener("click", () => createSession());
    el.composer.addEventListener("submit", (e) => { e.preventDefault(); send(el.input.value); });
    el.input.addEventListener("input", () => { autosize(); el.sendBtn.disabled = state.busy || !el.input.value.trim(); });
    el.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(el.input.value); }
    });
    for (const chip of document.querySelectorAll(".chip")) {
      chip.addEventListener("click", () => send(chip.dataset.q));
    }
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSidebar(); });
    // scroll events don't bubble; capture phase catches every .scroll-x-inner
    el.messages.addEventListener("scroll", (e) => {
      if (e.target.classList && e.target.classList.contains("scroll-x-inner")) updateScrollHint(e.target);
    }, true);
    window.addEventListener("resize", () => updateScrollHints(el.messages));
  }

  // ----------------------------------------------------------------- boot
  async function boot() {
    showOverlay("連線中…");
    try {
      const cfg = await (await fetch("/api/config")).json();
      await liff.init({ liffId: cfg.liff_id });
      if (!liff.isLoggedIn()) {
        liff.login({ redirectUri: location.href });
        return;
      }
      state.idToken = liff.getIDToken();
      if (!state.idToken) {
        // Scope openid missing or stale session — force a fresh login.
        liff.logout();
        liff.login({ redirectUri: location.href });
        return;
      }

      const me = await (await api("/api/me")).json();
      el.userBox.innerHTML = "";
      if (me.picture_url) {
        const img = document.createElement("img");
        img.src = me.picture_url;
        img.alt = "";
        el.userBox.append(img);
      }
      el.userBox.append(document.createTextNode(me.display_name || me.user_id.slice(0, 8)));

      await loadSessions();
      const params = new URLSearchParams(location.search);
      const q = params.get("q");
      const origin = params.get("s");
      hideOverlay();
      if (q || origin) {
        history.replaceState(null, "", location.pathname);
        const { session, created } = await createSession(q ? q.slice(0, 30) : null, origin);
        // Only send the bubble's question the first time (or if the earlier send never landed).
        if (q && (created || session.message_count === 0)) await send(q);
      } else if (state.sessions.length) {
        await openSession(state.sessions[0].id);
      } else {
        await createSession();
      }
    } catch (err) {
      if (err.message === "re-login") return;
      console.error(err);
      showOverlay(`無法連線：${err.message}`, true);
    }
  }

  el.overlayRetry.addEventListener("click", boot);
  bindUi();
  boot();
})();
