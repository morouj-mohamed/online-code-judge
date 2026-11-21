// page-problems.js - the problem list: search, difficulty and tag filters,
// your status per problem, and the live judge-status chip in the hero.

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { escapeHtml, formatMs } from "./format.js";
import { icon } from "./icons.js";
import { startJudgeStatus } from "./judge-status.js";
import { getStatuses, reconcileStatuses, STATUS_META } from "./store.js";

boot();

const region = document.getElementById("problem-region");
const searchInput = document.getElementById("problem-search");
const tagBox = document.getElementById("tag-filters");
const countEl = document.querySelector(".js-count");
const LEVEL = { Easy: 1, Medium: 2, Hard: 3 };

let problems = [];
let statuses = getStatuses();
const filters = { q: "", difficulty: "", tags: new Set() };

// --- filters <-> URL (so a filtered view can be bookmarked or shared) -------
function readUrl() {
  const params = new URLSearchParams(location.search);
  filters.q = params.get("q") || "";
  filters.difficulty = params.get("difficulty") || "";
  filters.tags = new Set((params.get("tags") || "").split(",").filter(Boolean));
  searchInput.value = filters.q;
  const radio = document.querySelector(`input[name="difficulty"][value="${CSS.escape(filters.difficulty)}"]`);
  if (radio) radio.checked = true;
}

function writeUrl() {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.difficulty) params.set("difficulty", filters.difficulty);
  if (filters.tags.size) params.set("tags", [...filters.tags].join(","));
  const query = params.toString();
  history.replaceState(null, "", query ? `?${query}` : location.pathname);
}

function matches(problem) {
  const q = filters.q.trim().toLowerCase();
  if (q) {
    const hay = `${problem.index} ${problem.title} ${problem.slug} ${problem.tags.join(" ")}`.toLowerCase();
    if (!q.split(/\s+/).every((word) => hay.includes(word))) return false;
  }
  if (filters.difficulty && problem.difficulty !== filters.difficulty) return false;
  for (const tag of filters.tags) if (!problem.tags.includes(tag)) return false;
  return true;
}

// --- rendering ---------------------------------------------------------------
function renderTags() {
  const all = [...new Set(problems.flatMap((p) => p.tags))].sort();
  tagBox.innerHTML = all.map((tag) => `<button type="button" class="chip" data-tag="${escapeHtml(tag)}"
      aria-pressed="${filters.tags.has(tag)}">${escapeHtml(tag)}</button>`).join("");
}

function row(problem, i) {
  const status = statuses[problem.slug] || "unsolved";
  const meta = STATUS_META[status];
  const cpp = problem.limits_by_language.cpp;
  return `<li class="problem-row" data-status="${status}" style="--i:${i}">
    <span class="problem-index" aria-hidden="true">${escapeHtml(problem.index)}</span>
    <div class="problem-main">
      <a class="problem-title" href="/problems/${encodeURIComponent(problem.slug)}">
        <span class="sr-only">Problem ${escapeHtml(problem.index)}: </span>${escapeHtml(problem.title)}</a>
      <div class="problem-meta">
        <span class="difficulty" data-level="${LEVEL[problem.difficulty] || 1}">
          <span class="bars" aria-hidden="true"><i></i><i></i><i></i></span>${escapeHtml(problem.difficulty)}</span>
        ${problem.tags.map((t) => `<span class="tag">${escapeHtml(t)}</span>`).join("")}
      </div>
    </div>
    <div class="problem-side">
      <span class="limits">
        <span class="chip" title="Time limit (C/C++)">${icon("clock", { size: 13 })}<span class="num">${formatMs(cpp.time_ms)}</span></span>
        <span class="chip" title="Memory limit (C/C++)">${icon("memory", { size: 13 })}<span class="num">${cpp.memory_mb} MB</span></span>
      </span>
      <span class="status-label" data-status="${status}">${icon(meta.icon, { size: 15 })}${meta.label}</span>
    </div>
  </li>`;
}

function render() {
  const visible = problems.filter(matches);
  const solved = problems.filter((p) => statuses[p.slug] === "solved").length;
  countEl.textContent = `${visible.length} of ${problems.length} problems · ${solved} solved`;
  tagBox.querySelectorAll("[data-tag]").forEach((b) => b.setAttribute("aria-pressed", filters.tags.has(b.dataset.tag)));
  region.setAttribute("aria-busy", "false");

  if (!visible.length) {
    region.innerHTML = `<div class="state">
      <span class="state-icon">${icon("search", { size: 22 })}</span>
      <h3>No problem matches these filters</h3>
      <p>Try another word, or clear the filters to see all ${problems.length} problems.</p>
      <button class="btn js-clear" type="button">${icon("x")}Clear filters</button></div>`;
    region.querySelector(".js-clear").addEventListener("click", clearFilters);
    return;
  }
  region.innerHTML = `<ul class="problem-list">${visible.map(row).join("")}</ul>`;
}

function renderError(error) {
  region.setAttribute("aria-busy", "false");
  region.innerHTML = `<div class="state" data-kind="error" role="alert">
    <span class="state-icon">${icon("offline", { size: 22 })}</span>
    <h3>Could not load the problems</h3><p>${escapeHtml(describeError(error))}</p>
    <button class="btn js-retry" type="button">${icon("refresh")}Try again</button></div>`;
  region.querySelector(".js-retry").addEventListener("click", load);
}

function clearFilters() {
  filters.q = "";
  filters.difficulty = "";
  filters.tags.clear();
  searchInput.value = "";
  document.getElementById("d-all").checked = true;
  writeUrl();
  render();
}

// --- events ------------------------------------------------------------------
searchInput.addEventListener("input", () => {
  filters.q = searchInput.value;
  writeUrl();
  render();
});
document.querySelectorAll('input[name="difficulty"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    filters.difficulty = radio.value;
    writeUrl();
    render();
  });
});
tagBox.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tag]");
  if (!button) return;
  const tag = button.dataset.tag;
  if (filters.tags.has(tag)) filters.tags.delete(tag);
  else filters.tags.add(tag);
  writeUrl();
  render();
});
// "/" focuses the search box, like many developer tools.
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !event.ctrlKey && !event.metaKey &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
    event.preventDefault();
    searchInput.focus();
  }
});

async function load() {
  region.setAttribute("aria-busy", "true");
  try {
    const data = await api.problems();
    problems = data.problems.sort((a, b) => a.index.localeCompare(b.index));
    renderTags();
    render();
  } catch (error) {
    renderError(error);
    return;
  }
  // Make the solved/attempted marks agree with the server's history.
  try {
    const subs = await api.submissions(100);
    statuses = reconcileStatuses(subs.submissions);
    render();
  } catch {
    /* keep the local statuses */
  }
}

readUrl();
load();
startJudgeStatus(document.getElementById("judge-status"));
