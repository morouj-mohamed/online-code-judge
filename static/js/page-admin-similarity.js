// page-admin-similarity.js - find pairs of students with suspiciously similar
// solutions, and look at them side by side (shared lines highlighted).

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { escapeHtml, LANGUAGE_LABELS, verdictBadge } from "./format.js";
import { icon } from "./icons.js";

boot();

const form = document.querySelector(".js-form");
const problemSelect = document.getElementById("s-problem");
const contestSelect = document.getElementById("s-contest");
const thresholdSelect = document.getElementById("s-threshold");
const runButton = form.querySelector(".js-run");
const region = document.getElementById("sim-region");
const pairBox = document.querySelector(".js-pair");

function scoreHtml(score) {
  const pct = Math.round(score * 100);
  const level = pct >= 90 ? "high" : pct >= 70 ? "mid" : "low";
  return `<span class="score" data-level="${level}"><span class="score-track"><span class="score-fill" style="width:${pct}%"></span></span>
    <span class="num">${pct}%</span></span>`;
}

function codeHtml(side) {
  const shared = new Set(side.shared_lines);
  return side.source.split("\n").map((line, i) => `<div class="diff-line${shared.has(i + 1) ? " is-shared" : ""}">
    <span class="ln">${i + 1}</span><span class="tx">${escapeHtml(line) || " "}</span></div>`).join("");
}

async function showPair(a, b) {
  pairBox.innerHTML = `<p class="muted row">${icon("loader", { cls: "spinner" })}Loading both programs…</p>`;
  try {
    const p = await api.admin.pair(a, b);
    pairBox.innerHTML = `<div class="section-title"><h2>Side by side</h2>${scoreHtml(p.score)}<span class="spacer"></span>
      <span class="subtle" style="font-size: var(--text-xs)">highlighted: lines with the same structure in both</span></div>
      <div class="diff">${[p.a, p.b].map((s) => `<div class="diff-col sim-col"><header>${icon("user", { size: 14 })}
        <a class="row-link" href="/admin/users/${encodeURIComponent(s.username)}">${escapeHtml(s.username)}</a>
        <span class="lang-pill">${escapeHtml(LANGUAGE_LABELS[s.language] || s.language)}</span>${verdictBadge(s.status)}
        <span class="spacer"></span><a class="row-link mono" href="/submissions/${s.submission_id}">#${s.submission_id}</a></header>
        <pre class="diff-lines code-view">${codeHtml(s)}</pre></div>`).join("")}</div>`;
    pairBox.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    pairBox.innerHTML = `<div class="form-error" role="alert">${icon("alert-octagon")}<span>${escapeHtml(describeError(error))}</span></div>`;
  }
}

region.addEventListener("click", (event) => {
  const button = event.target.closest("[data-a]");
  if (button) showPair(button.dataset.a, button.dataset.b);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  runButton.classList.add("is-busy");
  runButton.disabled = true;
  pairBox.innerHTML = "";
  try {
    const r = await api.admin.similarity(problemSelect.value, contestSelect.value, thresholdSelect.value);
    if (!r.pairs.length) {
      region.innerHTML = `<div class="state"><span class="state-icon">${icon("shield", { size: 22 })}</span>
        <h2>No similar pairs found</h2><p>${r.compared} student solution${r.compared === 1 ? "" : "s"} compared at ≥ ${Math.round(r.threshold * 100)}%.</p></div>`;
    } else {
      region.innerHTML = `<p class="muted" style="margin-bottom: 12px">${r.compared} students compared (their latest Accepted
        solution, else their latest one). ${r.pairs.length} pair${r.pairs.length === 1 ? "" : "s"} at ≥ ${Math.round(r.threshold * 100)}%:</p>
        <div class="card"><div class="table-wrap"><table class="table"><thead><tr><th scope="col">Similarity</th>
          <th scope="col">Student A</th><th scope="col">Student B</th><th scope="col">Language</th><th scope="col"><span class="sr-only">Compare</span></th></tr></thead>
        <tbody>${r.pairs.map((p) => `<tr><td>${scoreHtml(p.score)}</td>
          <td><strong>${escapeHtml(p.a.username)}</strong> <span class="subtle mono">#${p.a.submission_id}</span></td>
          <td><strong>${escapeHtml(p.b.username)}</strong> <span class="subtle mono">#${p.b.submission_id}</span></td>
          <td>${escapeHtml(LANGUAGE_LABELS[p.a.language] || p.a.language)}${p.a.language !== p.b.language ? ` / ${escapeHtml(LANGUAGE_LABELS[p.b.language])}` : ""}</td>
          <td><button class="btn btn-sm" type="button" data-a="${p.a.submission_id}" data-b="${p.b.submission_id}">${icon("columns", { size: 14 })}Compare</button></td></tr>`).join("")}
        </tbody></table></div></div>`;
    }
  } catch (error) {
    region.innerHTML = `<div class="form-error" role="alert">${icon("alert-octagon")}<span>${escapeHtml(describeError(error))}</span></div>`;
  } finally {
    runButton.classList.remove("is-busy");
    runButton.disabled = false;
  }
});

async function start() {
  const [problems, contests] = await Promise.all([api.admin.problems(), api.admin.contests()]);
  problemSelect.innerHTML = problems.problems.map((p) =>
    `<option value="${escapeHtml(p.slug)}">${escapeHtml(p.index)}. ${escapeHtml(p.title)} (${p.submissions} submissions)</option>`).join("");
  contestSelect.innerHTML += contests.contests.map((c) => `<option value="${escapeHtml(c.slug)}">${escapeHtml(c.title)}</option>`).join("");
  const params = new URLSearchParams(location.search);
  if (params.get("problem")) problemSelect.value = params.get("problem");
}

start().catch((error) => { region.innerHTML = `<p class="muted">${escapeHtml(describeError(error))}</p>`; });
