/* NTHU campus assistant — LIFF chat page (entry point).
 * Served by Firebase Hosting; talks only to our own Cloud Run API. Identity comes
 * from the LIFF id_token, which the backend verifies with LINE. No secrets live here.
 */

import { api, loadConfig } from "./api.js";
import { createSession, loadSessions, openSession, send } from "./chat.js";
import {
  deleteAllData, finishDeletion, requestConsent, setPolicyVersion,
} from "./consent.js";
import { openProfile } from "./profile.js";
import {
  autosize, bindMessageScrolling, closeSidebar, desktopLayout, el, hideOverlay, openSidebar,
  overlayRetryHandler, scrollToBottom, showOverlay, state, syncSidebarLayout, updateControls,
} from "./dom.js";
import { updateScrollHint, updateScrollHints } from "./markdown.js";

function bindUi() {
  el.menuBtn.addEventListener("click", openSidebar);
  el.closeSidebar.addEventListener("click", closeSidebar);
  el.backdrop.addEventListener("click", closeSidebar);
  el.newBtn.addEventListener("click", () => createSession());
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
  document.addEventListener("keydown", (e) => {
    if (desktopLayout.matches || !el.sidebar.classList.contains("open") || el.app.inert || !el.overlay.hidden || document.querySelector("dialog[open]")) return;
    if (e.key === "Escape") closeSidebar();
    if (e.key !== "Tab") return;
    const controls = [...el.sidebar.querySelectorAll("button:not(:disabled), select:not(:disabled), a[href]")];
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (!el.sidebar.contains(document.activeElement) || (e.shiftKey && document.activeElement === first)) {
      e.preventDefault();
      (e.shiftKey ? last : first).focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  });
  new ResizeObserver(() => {
    el.app.style.setProperty("--composer-height", `${el.composerDock.getBoundingClientRect().height}px`);
  }).observe(el.composerDock);
  // scroll events don't bubble; capture phase catches every .scroll-x-inner
  el.messages.addEventListener("scroll", (e) => {
    if (e.target.classList && e.target.classList.contains("scroll-x-inner")) updateScrollHint(e.target);
  }, true);
  window.addEventListener("resize", () => updateScrollHints(el.messages));
  bindMessageScrolling();
  el.scrollBtn.addEventListener("click", scrollToBottom);
  syncSidebarLayout();
  desktopLayout.addEventListener("change", syncSidebarLayout);
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
    setPolicyVersion(cfg.privacyPolicyVersion);
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
    if (err.code === "account_deleting") {
      // An earlier deletion did not finish; let the user complete it.
      showOverlay(err.message, true, finishDeletion);
      return;
    }
    console.error(err);
    showOverlay(`無法連線：${err.message}`, true);
  }
}

el.overlayRetry.addEventListener("click", () => (overlayRetryHandler() || boot)());
bindUi();
boot();
