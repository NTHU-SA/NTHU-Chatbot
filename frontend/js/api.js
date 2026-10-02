// Backend access. The page is served by Firebase Hosting and calls the Cloud Run
// API directly (CORS); identity comes from the LIFF id_token, which the backend
// verifies with LINE. No secrets live here — config.json only holds the public
// LIFF ID and API base URL for this environment.

import { state } from "./dom.js";

let apiBase = "";

export async function loadConfig() {
  const res = await fetch("./config.json", { cache: "no-store" });
  if (!res.ok) throw new Error("config.json unavailable");
  const cfg = await res.json();
  apiBase = String(cfg.apiBase || "").replace(/\/+$/, "");
  return cfg;
}

export async function api(path, opts = {}) {
  // X-Auth-Provider tells the backend which authenticator verifies the token.
  const headers = Object.assign(
    { Authorization: `Bearer ${state.idToken}`, "X-Auth-Provider": "line" },
    opts.headers || {});
  if (opts.body && typeof opts.body !== "string") {
    headers["Content-Type"] = "application/json";
    opts = Object.assign({}, opts, { body: JSON.stringify(opts.body) });
  }
  // credentials: "omit" — auth is the bearer token only, never cookies.
  const res = await fetch(apiBase + path, Object.assign({ credentials: "omit" }, opts, { headers }));
  if (res.status === 401) {
    // id_token expired — re-login through LIFF and come back.
    liff.logout();
    liff.login({ redirectUri: location.href });
    throw new Error("re-login");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* ignore */ }
    // Structured errors look like {code, message}, e.g. {code: "consent_required"}.
    const structured = detail && typeof detail === "object" && !Array.isArray(detail);
    const message = structured ? detail.message || detail.code : Array.isArray(detail) ? "輸入格式有誤" : detail;
    const err = new Error(message);
    err.status = res.status;
    if (structured) err.code = detail.code;
    throw err;
  }
  return res;
}

// Parse a text/event-stream body coming from fetch (EventSource is GET-only).
export async function readSse(res, onEvent) {
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
