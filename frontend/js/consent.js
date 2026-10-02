// Privacy-policy consent, revoking it, and deleting all of the user's data.
// The backend enforces consent (403 consent_required); this module only shows
// the screen and records the user's choice.

import { api } from "./api.js";
import { closeSidebar, confirmDialog, el, showOverlay } from "./dom.js";

// The version privacy.html shows in this deployment (config.json, stamped at build
// time from the same source as the backend). Consent is always recorded for the
// version the user could actually read.
let shownVersion = null;
let pending = null;
let returnFocus = null;

export function setPolicyVersion(version) {
  shownVersion = version ? String(version) : null;
}

function openScreen() {
  returnFocus = document.activeElement;
  // Modal: nothing behind the screen can be focused or read while it is open.
  el.app.inert = true;
  el.consent.hidden = false;
}

function closeScreen() {
  el.consent.hidden = true;
  el.app.inert = false;
  if (returnFocus && returnFocus.isConnected) returnFocus.focus();
  returnFocus = null;
}

// The frontend and backend are deployed separately; for a moment they may
// disagree on the version. Never record consent to a version not on screen.
function showStale() {
  el.consentStale.hidden = false;
  el.consentAccept.disabled = true;
}

// Show the consent screen and resolve once the user accepts. `required` is the
// version the backend asks for (from /api/me or a 403 consent_required).
export function requestConsent(required) {
  if (pending) return pending;
  const version = shownVersion || (required ? String(required) : null);
  openScreen();
  el.consentStale.hidden = true;
  el.consentAccept.disabled = false;
  if (required && version !== String(required)) showStale();
  else el.consentAccept.focus();
  pending = new Promise((resolve) => {
    el.consentAccept.onclick = async () => {
      el.consentAccept.disabled = true;
      try {
        await api("/api/consents/privacy_policy", { method: "POST", body: { version } });
        closeScreen();
        pending = null;
        resolve();
      } catch (err) {
        if (err.status === 409) showStale(); // the policy changed on the server
        else el.consentAccept.disabled = false;
      }
    };
  });
  return pending;
}

export async function revokeConsent() {
  closeSidebar();
  if (!(await confirmDialog("撤回同意後就不能和本汪對話，直到再次同意。既有資料不會被刪除。確定要撤回嗎？", "撤回"))) return;
  const res = await api("/api/consents/privacy_policy/revoke", { method: "POST" });
  const state = await res.json();
  await requestConsent(state.version);
}

export async function deleteAllData() {
  closeSidebar();
  const ok = await confirmDialog(
    "將永久刪除你的所有對話、設定與使用紀錄，無法復原。確定要刪除嗎？", "永久刪除");
  if (!ok) return;
  await finishDeletion();
}

// Also used when the page opens on an account whose earlier deletion did not
// finish (403 account_deleting): the backend only accepts DELETE /api/me then.
export async function finishDeletion() {
  showOverlay("刪除中…");
  try {
    await api("/api/me", { method: "DELETE" });
  } catch (err) {
    if (err.message === "re-login") return;
    showOverlay(err.message || "刪除沒有完成，請再試一次。", true, finishDeletion);
    return;
  }
  showOverlay("你的資料已全部刪除。重新開啟頁面會以新使用者身分開始。", false);
  history.replaceState(null, "", location.pathname);
}
