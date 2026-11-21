// palette.js - the command palette (Ctrl+K / Cmd+K).
//
// A native <dialog> (focus trap and Escape for free) with a combobox: type to
// filter, arrow keys to move, Enter to go. Items: pages, every problem
// (loaded on first open) and a few actions.

import { api } from "./api.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";
import { toggleTheme } from "./theme.js";

let dialog = null;
let input = null;
let listEl = null;
let items = [];
let shown = [];
let active = 0;
let problemsLoaded = false;

const PAGES = [
  { group: "Pages", label: "Problems", hint: "Home", icon: "grid", href: "/" },
  { group: "Pages", label: "My submissions", hint: "History", icon: "list", href: "/submissions" },
];
const ACTIONS = [
  { group: "Actions", label: "Toggle light / dark theme", icon: "sun", run: () => toggleTheme() },
];

/** Subsequence match: "frm" matches "Fibonacci Modulo". Lower score = better. */
function score(text, query) {
  if (!query) return 0;
  const t = text.toLowerCase();
  const q = query.toLowerCase();
  const direct = t.indexOf(q);
  if (direct >= 0) return direct;
  let pos = 0;
  let gaps = 0;
  for (const ch of q) {
    const found = t.indexOf(ch, pos);
    if (found < 0) return -1;
    gaps += found - pos;
    pos = found + 1;
  }
  return 100 + gaps;
}

function render() {
  const query = input.value.trim();
  shown = items
    .map((item) => ({ item, s: score(`${item.label} ${item.keywords || ""}`, query) }))
    .filter((x) => x.s >= 0)
    .sort((a, b) => (query ? a.s - b.s : 0))
    .map((x) => x.item);
  active = Math.min(active, Math.max(0, shown.length - 1));

  if (!shown.length) {
    listEl.innerHTML = `<li class="palette-empty" role="presentation">No match for “${escapeHtml(query)}”</li>`;
    input.removeAttribute("aria-activedescendant");
    return;
  }
  let html = "";
  let group = null;
  shown.forEach((item, index) => {
    if (!query && item.group !== group) {
      group = item.group;
      html += `<li class="palette-group" role="presentation">${escapeHtml(group)}</li>`;
    }
    html += `<li class="palette-item" role="option" id="palette-opt-${index}" data-index="${index}"
      aria-selected="${index === active}">${icon(item.icon)}<span>${escapeHtml(item.label)}</span>
      ${item.meta ? `<span class="mono">${escapeHtml(item.meta)}</span>` : ""}
      ${item.hint ? `<span class="hint">${escapeHtml(item.hint)}</span>` : ""}</li>`;
  });
  listEl.innerHTML = html;
  input.setAttribute("aria-activedescendant", `palette-opt-${active}`);
  listEl.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
}

function choose(index) {
  const item = shown[index];
  if (!item) return;
  dialog.close();
  if (item.href) window.location.href = item.href;
  else if (item.run) item.run();
}

async function loadProblems() {
  if (problemsLoaded) return;
  problemsLoaded = true;
  try {
    const data = await api.problems();
    const problems = data.problems.map((p) => ({
      group: "Problems",
      label: p.title,
      meta: p.index,
      hint: p.difficulty,
      keywords: `${p.slug} ${p.tags.join(" ")}`,
      icon: "file-code",
      href: `/problems/${p.slug}`,
    }));
    items = [...PAGES, ...problems, ...ACTIONS];
    if (dialog.open) render();
  } catch {
    problemsLoaded = false;   // try again next time
  }
}

function build() {
  dialog = document.createElement("dialog");
  dialog.className = "palette";
  dialog.setAttribute("aria-label", "Command palette");
  dialog.innerHTML = `
    <div class="palette-search">
      ${icon("search", { size: 18 })}
      <input type="text" role="combobox" aria-expanded="true" aria-controls="palette-list"
        aria-autocomplete="list" aria-label="Search problems and pages"
        placeholder="Jump to a problem or page…" autocomplete="off" spellcheck="false">
      <kbd>Esc</kbd>
    </div>
    <ul class="palette-list" id="palette-list" role="listbox" aria-label="Results"></ul>
    <div class="palette-foot" aria-hidden="true">
      <span><kbd>↑</kbd><kbd>↓</kbd> move</span><span><kbd>Enter</kbd> open</span><span><kbd>Esc</kbd> close</span>
    </div>`;
  document.body.appendChild(dialog);
  input = dialog.querySelector("input");
  listEl = dialog.querySelector(".palette-list");
  items = [...PAGES, ...ACTIONS];

  input.addEventListener("input", () => {
    active = 0;
    render();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      active = (active + delta + shown.length) % Math.max(1, shown.length);
      render();
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(active);
    }
  });
  listEl.addEventListener("click", (event) => {
    const option = event.target.closest(".palette-item");
    if (option) choose(Number(option.dataset.index));
  });
  listEl.addEventListener("mousemove", (event) => {
    const option = event.target.closest(".palette-item");
    if (option && Number(option.dataset.index) !== active) {
      active = Number(option.dataset.index);
      render();
    }
  });
  // Click on the backdrop (outside the box) closes it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

export function openPalette() {
  if (!dialog) build();
  if (dialog.open) return;
  input.value = "";
  active = 0;
  render();
  dialog.showModal();
  input.focus();
  loadProblems();
}

export function initPalette() {
  document.querySelectorAll(".palette-trigger").forEach((button) => {
    button.addEventListener("click", openPalette);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (dialog?.open) dialog.close();
      else openPalette();
    }
  });
}
