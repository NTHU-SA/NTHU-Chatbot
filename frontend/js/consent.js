// Privacy-policy consent, revoking it, and deleting all of the user's data.
// The backend enforces consent (403 consent_required); this module only shows
// the screen and records the user's choice.

import { api } from "./api.js";
import { closeSidebar, confirmDialog, el, showOverlay } from "./dom.js";

let policyVersion = null;
let pending = null;

// Show the consent screen and resolve once the user accepts the given version.
export function requestConsent(version) {
  policyVersion = version || policyVersion;
  if (pending) return pending;
  el.consent.hidden = false;
  el.consentAccept.disabled = false;
  el.consentAccept.focus();
  pending = new Promise((resolve) => {
    el.consentAccept.onclick = async () => {
      el.consentAccept.disabled = true;
      try {
        await api("/api/consents/privacy_policy", { method: "POST", body: { version: policyVersion } });
        el.consent.hidden = true;
        pending = null;
        resolve();
      } catch (err) {
        el.consentAccept.disabled = false;
        // 409: the policy changed while the screen was open — reload to show the new version.
        if (err.status === 409) location.reload();
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
  showOverlay("刪除中…");
  await api("/api/me", { method: "DELETE" });
  showOverlay("你的資料已全部刪除。重新開啟頁面會以新使用者身分開始。", false);
  history.replaceState(null, "", location.pathname);
}
