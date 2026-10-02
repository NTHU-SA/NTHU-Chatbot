// "我的資料": nickname, department and the things the assistant remembers.
// Used both as the optional first-run step (after consent) and from the sidebar.

import { api } from "./api.js";
import { $, closeSidebar } from "./dom.js";

const dlg = $("profileDlg");
const form = $("profileForm");
const nickname = $("nicknameInput");
const department = $("departmentInput");
const choices = $("departmentChoices");
const error = $("profileError");
const memoryList = $("memoryList");
const memoryEmpty = $("memoryEmpty");
let departmentsLoaded = false;

async function loadDepartments() {
  if (departmentsLoaded) return;
  try {
    const names = await (await api("/api/departments")).json();
    const list = $("departmentList");
    list.replaceChildren(...names.map((name) => Object.assign(document.createElement("option"), { value: name })));
    departmentsLoaded = names.length > 0;
  } catch (_) { /* autocomplete is optional */ }
}

function renderMemories(memories) {
  memoryList.replaceChildren();
  for (const memory of memories) {
    const li = document.createElement("li");
    const text = document.createElement("span");
    text.textContent = memory.value;
    const del = document.createElement("button");
    del.type = "button";
    del.textContent = "×";
    del.setAttribute("aria-label", `刪除「${memory.value}」`);
    del.addEventListener("click", async () => {
      await api(`/api/memories/${encodeURIComponent(memory.id)}`, { method: "DELETE" });
      li.remove();
      memoryEmpty.hidden = memoryList.children.length > 0;
    });
    li.append(text, del);
    memoryList.append(li);
  }
  memoryEmpty.hidden = memories.length > 0;
}

function showError(message, candidates = []) {
  error.textContent = message;
  error.hidden = !message;
  choices.replaceChildren();
  choices.hidden = candidates.length === 0;
  for (const name of candidates) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = name;
    chip.addEventListener("click", () => { department.value = name; showError(""); });
    choices.append(chip);
  }
}

// Resolves when the dialog is dismissed. `onboarding` shows the intro and a skip
// button and hides the memory list (there is nothing to show yet).
export async function openProfile({ onboarding = false } = {}) {
  closeSidebar();
  const profile = await (await api("/api/profile")).json();
  nickname.value = profile.nickname || "";
  department.value = profile.department || "";
  renderMemories(profile.memories || []);
  showError("");
  $("profileIntro").hidden = !onboarding;
  $("memorySection").hidden = onboarding;
  $("profileSkip").hidden = !onboarding;
  $("profileClose").hidden = onboarding;
  loadDepartments();

  return new Promise((resolve) => {
    let settled = false;
    const cleanup = () => {
      if (settled) return;
      settled = true;
      form.removeEventListener("submit", onSubmit);
      dlg.removeEventListener("cancel", cleanup);
      dlg.removeEventListener("close", cleanup);
      resolve();
    };
    const onSubmit = async (e) => {
      const action = e.submitter && e.submitter.value;
      if (action !== "save" && action !== "skip") { cleanup(); return; } // close
      e.preventDefault();
      const body = action === "skip"
        ? { skip_onboarding: true }
        : { nickname: nickname.value.trim(), department: department.value.trim() };
      try {
        await api("/api/profile", { method: "PATCH", body });
        dlg.close();
        cleanup();
      } catch (err) {
        showError(err.message, err.candidates || []);
      }
    };
    form.addEventListener("submit", onSubmit);
    dlg.addEventListener("cancel", cleanup); // Escape
    dlg.addEventListener("close", cleanup);
    dlg.showModal();
  });
}
