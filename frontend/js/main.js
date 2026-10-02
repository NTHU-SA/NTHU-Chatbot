/* NTHU campus assistant — LIFF chat page (entry point).
 * Served by Firebase Hosting; talks only to our own Cloud Run API. Identity comes
 * from the LIFF id_token, which the backend verifies with LINE. No secrets live here.
 */

import { api, loadConfig } from "./api.js";
import { createSession, loadSessions, openSession, send } from "./chat.js";
import { deleteAllData, requestConsent, revokeConsent } from "./consent.js";
import { openProfile } from "./profile.js";
import {
  autosize, closeSidebar, el, hideOverlay, openSidebar, scrollToBottom, showOverlay, state,
  updateControls, updateScrollButton,
} from "./dom.js";
import { updateScrollHint, updateScrollHints } from "./markdown.js";

function bindUi() {
  el.menuBtn.addEventListener("click", openSidebar);
  el.closeSidebar.addEventListener("click", closeSidebar);
  el.backdrop.addEventListener("click", closeSidebar);
  el.newBtn.addEventListener("click", () => createSession());
  el.revokeBtn.addEventListener("click", () => revokeConsent().catch(reportError));
  el.profileBtn.addEventListener("click", () => openProfile().catch(reportError));
  el.deleteDataBtn.addEventListener("click", () => deleteAllData().catch(reportError));
  el.composer.addEventListener("submit", (e) => { e.preventDefault(); send(el.input.value); });
  el.input.addEventListener("input", () => { autosize(); updateControls(); });
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
  el.messages.addEventListener("scroll", updateScrollButton, { passive: true });
  new MutationObserver(updateScrollButton).observe(el.messages, { childList: true, subtree: true, characterData: true });
  el.scrollBtn.addEventListener("click", () => {
    el.messages.scrollTo({ top: el.messages.scrollHeight, behavior: "smooth" });
  });
}

function reportError(err) {
  if (err.message === "re-login") return;
  console.error(err);
  showOverlay(`發生錯誤：${err.message}`, true);
}

// Environment info for the backend's records only (never used for auth).
// The context id (possibly a group id) is deliberately not sent.
async function clientInfo() {
  const info = {};
  try {
    const os = liff.getOS();
    if (["ios", "android", "web"].includes(os)) info.os = os;
    const version = liff.getLineVersion();
    if (version) info.line_version = String(version).slice(0, 32);
    const language = liff.getAppLanguage ? liff.getAppLanguage() : liff.getLanguage();
    if (language) info.language = String(language).slice(0, 16);
    const context = liff.getContext();
    // Must match the backend whitelist, otherwise /api/me rejects the request.
    const types = ["utou", "group", "room", "external", "none", "square_chat"];
    if (context && types.includes(context.type)) info.context_type = context.type;
  } catch (_) { /* best effort */ }
  try {
    info.friendship = (await liff.getFriendship()).friendFlag;
  } catch (_) { /* requires the LIFF app to be linked to the bot */ }
  return info;
}

async function boot() {
  showOverlay("連線中…");
  try {
    const cfg = await loadConfig();
    await liff.init({ liffId: cfg.liffId });
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

    const me = await (await api("/api/me", { method: "POST", body: await clientInfo() })).json();
    el.userBox.innerHTML = "";
    if (me.picture_url) {
      const img = document.createElement("img");
      img.src = me.picture_url;
      img.alt = "";
      el.userBox.append(img);
    }
    el.userBox.append(document.createTextNode(me.display_name || "LINE 使用者"));

    // AI chat needs consent to the current privacy policy (the backend enforces it too).
    hideOverlay();
    if (!me.consent.accepted) {
      await requestConsent(me.consent.version);
      // First run: optionally ask for a nickname and department (skippable, asked once).
      const profile = await (await api("/api/profile")).json();
      if (profile.onboarding == null && !profile.nickname && !profile.department) {
        await openProfile({ onboarding: true });
      }
    }

    await loadSessions();
    const params = new URLSearchParams(location.search);
    const q = params.get("q");
    const origin = params.get("s");
    hideOverlay();
    if (q || origin) {
      history.replaceState(null, "", location.pathname);
      const result = await createSession(q ? q.slice(0, 30) : null, origin);
      if (!result) return;
      const { session, created } = result;
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
