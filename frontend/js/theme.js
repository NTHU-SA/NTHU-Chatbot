(() => {
  const storageKey = "nthu-chatbot-theme";
  const preferences = ["system", "light", "dark"];
  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  let storageUnavailable = false;

  function showStorageWarning() {
    const status = document.getElementById("themeStatus");
    if (!status || !storageUnavailable) return;
    status.textContent = "瀏覽器無法儲存外觀設定，這次仍會套用。";
    status.hidden = false;
  }

  function storageFailed() {
    storageUnavailable = true;
    console.warn("Unable to access local theme preferences.");
    showStorageWarning();
  }

  function applyTheme() {
    document.documentElement.dataset.theme = preference === "system"
      ? (systemTheme.matches ? "dark" : "light")
      : preference;
    const select = document.getElementById("themeSelect");
    if (select) select.value = preference;
  }

  try {
    const saved = localStorage.getItem(storageKey);
    if (saved && preferences.includes(saved)) preference = saved;
    else if (saved) console.warn("Ignoring an invalid local theme preference.");
  } catch (_) {
    storageFailed();
  }
  applyTheme();

  systemTheme.addEventListener("change", () => {
    if (preference === "system") applyTheme();
  });

  window.addEventListener("storage", (event) => {
    if (event.key !== storageKey && event.key !== null) return;
    const next = event.newValue || "system";
    if (!preferences.includes(next)) {
      console.warn("Ignoring an invalid local theme preference.");
      return;
    }
    preference = next;
    applyTheme();
  });

  function bindControls() {
    const select = document.getElementById("themeSelect");
    if (!select) return;
    select.value = preference;
    showStorageWarning();
    select.addEventListener("change", () => {
      if (!preferences.includes(select.value)) {
        console.warn("Ignoring an invalid theme selection.");
        applyTheme();
        return;
      }
      preference = select.value;
      applyTheme();
      try {
        localStorage.setItem(storageKey, preference);
        storageUnavailable = false;
        document.getElementById("themeStatus").hidden = true;
      } catch (_) {
        storageFailed();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindControls, { once: true });
  } else {
    bindControls();
  }
})();
