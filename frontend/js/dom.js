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
  composerDock: $("composerDock"),
  input: $("input"),
  sendBtn: $("sendBtn"),
  composerStatus: $("composerStatus"),
  overlay: $("overlay"),
  overlayText: $("overlayText"),
  overlayRetry: $("overlayRetry"),
  confirmDlg: $("confirmDlg"),
  confirmOk: $("confirmOk"),
  consent: $("consent"),
  consentAccept: $("consentAccept"),
  consentStale: $("consentStale"),
  deleteDataBtn: $("deleteDataBtn"),
  profileBtn: $("profileBtn"),
  scrollBtn: $("scrollBtn"),
  loading: $("loadingMsgs"),
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

let retryHandler = null;
let followingLatest = true;
export const desktopLayout = matchMedia("(min-width: 1024px)");

// `onRetry` replaces what the retry button does (default: restart the page boot).
export function showOverlay(text, retry, onRetry) {
  el.overlay.hidden = false;
  el.overlayText.textContent = text;
  el.overlayRetry.hidden = !retry;
  retryHandler = onRetry || null;
}

export const overlayRetryHandler = () => retryHandler;

export const hideOverlay = () => (el.overlay.hidden = true);

export function scrollToBottom() {
  followingLatest = true;
  el.messages.scrollTop = el.messages.scrollHeight;
  updateScrollButton();
}

export function bindMessageScrolling() {
  let frame = null;
  const schedule = () => {
    if (frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (followingLatest) scrollToBottom();
      else updateScrollButton();
    });
  };
  const resize = new ResizeObserver(schedule);
  resize.observe(el.messages);
  const observed = new Set();
  const observeChild = (child) => {
    if (child.nodeType !== 1 || observed.has(child)) return;
    observed.add(child);
    resize.observe(child);
  };
  for (const child of el.messages.children) observeChild(child);
  new MutationObserver((records) => {
    for (const record of records) {
      for (const child of record.removedNodes) {
        if (child.parentNode !== el.messages && observed.delete(child)) resize.unobserve(child);
      }
      for (const child of record.addedNodes) {
        if (child.parentNode === el.messages) observeChild(child);
      }
    }
    schedule();
  }).observe(el.messages, { childList: true });
  schedule();
  el.messages.addEventListener("scroll", () => {
    followingLatest = el.messages.scrollHeight - el.messages.clientHeight - el.messages.scrollTop < 48;
    updateScrollButton();
  }, { passive: true });
}

// Placeholder while a session's history loads.
export function showLoading(on) {
  el.loading.hidden = !on;
  el.messages.setAttribute("aria-busy", String(on));
  if (on) el.empty.hidden = true;
}

// Offer a way back down while the user is reading earlier messages.
export function updateScrollButton() {
  const fromBottom = el.messages.scrollHeight - el.messages.clientHeight - el.messages.scrollTop;
  el.scrollBtn.hidden = fromBottom < 120;
}

export function updateControls() {
  el.sendBtn.disabled = state.busy || state.loading || !el.input.value.trim();
  el.newBtn.disabled = state.busy || state.loading;
  el.composer.classList.toggle("busy", state.busy);
  el.composer.setAttribute("aria-busy", String(state.busy));
  el.sendBtn.setAttribute("aria-label", state.busy ? "回覆中" : "送出");
  el.sendBtn.title = state.busy ? "回覆中" : "送出";
  const status = state.busy ? "回覆中…" : state.loading ? "載入對話中…" : "";
  if (el.composerStatus.textContent !== status) el.composerStatus.textContent = status;
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
  if (desktopLayout.matches) return;
  el.sidebar.inert = false;
  el.sidebar.setAttribute("aria-hidden", "false");
  el.sidebar.classList.add("open");
  el.backdrop.hidden = false;
  el.menuBtn.setAttribute("aria-expanded", "true");
  el.closeSidebar.focus();
}

export function closeSidebar() {
  if (desktopLayout.matches) return;
  if (!el.sidebar.classList.contains("open")) return;
  el.menuBtn.focus();
  el.sidebar.inert = true;
  el.sidebar.setAttribute("aria-hidden", "true");
  el.sidebar.classList.remove("open");
  el.backdrop.hidden = true;
  el.menuBtn.setAttribute("aria-expanded", "false");
}

export function syncSidebarLayout() {
  const hadFocus = el.sidebar.contains(document.activeElement);
  el.sidebar.inert = !desktopLayout.matches;
  el.sidebar.setAttribute("aria-hidden", String(!desktopLayout.matches));
  el.sidebar.classList.toggle("open", desktopLayout.matches);
  el.backdrop.hidden = true;
  el.menuBtn.setAttribute("aria-expanded", String(desktopLayout.matches));
  if (hadFocus) (desktopLayout.matches ? el.input : el.menuBtn).focus();
}
