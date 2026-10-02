// Shared DOM references, page state and small view helpers.

export const $ = (id) => document.getElementById(id);

export const el = {
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
  confirmOk: $("confirmOk"),
  consent: $("consent"),
  consentAccept: $("consentAccept"),
  revokeBtn: $("revokeBtn"),
  deleteDataBtn: $("deleteDataBtn"),
  profileBtn: $("profileBtn"),
  tplMessage: $("tplMessage"),
};

export const state = {
  idToken: null,
  sessions: [],
  current: null, // session id
  busy: false,
  loading: false,
  navigation: 0,
};

export function showOverlay(text, retry) {
  el.overlay.hidden = false;
  el.overlayText.textContent = text;
  el.overlayRetry.hidden = !retry;
}

export const hideOverlay = () => (el.overlay.hidden = true);

export function scrollToBottom() {
  el.messages.scrollTop = el.messages.scrollHeight;
}

export function updateControls() {
  el.sendBtn.disabled = state.busy || state.loading || !el.input.value.trim();
  el.newBtn.disabled = state.busy || state.loading;
}

export function setBusy(busy) {
  state.busy = busy;
  updateControls();
}

export function setLoading(loading) {
  state.loading = loading;
  updateControls();
}

export function autosize() {
  if (CSS.supports("field-sizing", "content")) return;
  el.input.style.height = "auto";
  el.input.style.height = Math.min(el.input.scrollHeight, 140) + "px";
}

// Resolves true only when the OK button is pressed. The form's submit event fires
// synchronously with the pressed button; "close" alone is not reliable (Chrome
// defers it while the page is hidden), so whichever signal comes first wins.
export function confirmDialog(text, okLabel = "刪除") {
  return new Promise((resolve) => {
    const dlg = el.confirmDlg;
    const form = dlg.querySelector("form");
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      form.removeEventListener("submit", onSubmit);
      dlg.removeEventListener("cancel", onCancel);
      dlg.removeEventListener("close", onClose);
      resolve(ok);
    };
    const onSubmit = (e) => finish(Boolean(e.submitter) && e.submitter.value === "ok");
    const onCancel = () => finish(false);
    const onClose = () => finish(dlg.returnValue === "ok");
    $("confirmText").textContent = text;
    el.confirmOk.textContent = okLabel;
    dlg.returnValue = "cancel";
    form.addEventListener("submit", onSubmit);
    dlg.addEventListener("cancel", onCancel);
    dlg.addEventListener("close", onClose);
    dlg.showModal();
  });
}

export function openSidebar() {
  el.sidebar.inert = false;
  el.sidebar.setAttribute("aria-hidden", "false");
  el.sidebar.classList.add("open");
  el.backdrop.hidden = false;
  el.menuBtn.setAttribute("aria-expanded", "true");
  el.closeSidebar.focus();
}

export function closeSidebar() {
  if (!el.sidebar.classList.contains("open")) return;
  el.menuBtn.focus();
  el.sidebar.inert = true;
  el.sidebar.setAttribute("aria-hidden", "true");
  el.sidebar.classList.remove("open");
  el.backdrop.hidden = true;
  el.menuBtn.setAttribute("aria-expanded", "false");
}
