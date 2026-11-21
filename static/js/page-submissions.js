// page-submissions.js - this browser's submissions, updated live.
//
// The table is loaded once, then every "submission" Server-Sent Event updates
// its row in place (status badge, time, memory), with a short highlight. A
// submission we have not seen yet (made in another tab) reloads the list.

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import {
  absoluteTime, debounce, escapeHtml, FINAL, formatKb, formatMs, LANGUAGE_LABELS,
  relativeTime, verdictBadge, verdictInfo,
} from "./format.js";
import { icon } from "./icons.js";
import { openStream } from "./sse.js";
import { reconcileStatuses } from "./store.js";

boot();

const region = document.getElementById("subs-region");
const chip = document.getElementById("stream-state");
let rows = new Map();          // id -> submission
let titles = {};               // slug -> "A. Sum of Two Numbers"
const isAdmin = document.body.dataset.admin === "1";
let scope = new URLSearchParams(location.search).get("scope") === "all" && isAdmin ? "all" : "mine";

function rowHtml(s) {
  const title = titles[s.problem] || s.problem;
  const failed = s.failed_test && FINAL.has(s.status) && s.status !== "Accepted" ? ` on test ${s.failed_test}` : "";
  return `<tr data-id="${s.id}">
    <td>#${s.id}</td>
    <td><a class="row-link" href="/submissions/${s.id}">${escapeHtml(title)}</a>
      ${scope === "all" ? `<div class="subtle" style="font-size: var(--text-xs)">${escapeHtml(s.username || "anonymous")}</div>` : ""}</td>
    <td class="hide-sm"><span class="lang-pill">${escapeHtml(LANGUAGE_LABELS[s.language] || s.language)}</span></td>
    <td>${verdictBadge(s.status)}<span class="subtle" style="font-size: var(--text-xs)">${escapeHtml(failed)}</span></td>
    <td class="num">${FINAL.has(s.status) ? formatMs(s.time_ms) : "—"}</td>
    <td class="num hide-sm">${FINAL.has(s.status) ? formatKb(s.memory_kb) : "—"}</td>
    <td class="hide-sm"><time datetime="${new Date(s.created_at * 1000).toISOString()}" title="${escapeHtml(absoluteTime(s.created_at))}">${escapeHtml(relativeTime(s.created_at))}</time></td>
  </tr>`;
}

function render() {
  region.setAttribute("aria-busy", "false");
  const list = [...rows.values()].sort((a, b) => b.id - a.id);
  if (!list.length) {
    region.innerHTML = `<div class="state">
      <span class="state-icon">${icon("inbox", { size: 22 })}</span>
      <h2>No submissions yet</h2>
      <p>Pick a problem, write a solution and press Submit. Every verdict will appear here, live.</p>
      <a class="btn btn-primary" href="/">${icon("grid")}Browse problems</a></div>`;
    return;
  }
  region.innerHTML = `<div class="card"><div class="table-wrap">
    <table class="table subs-table">
      <caption class="sr-only">Your submissions, newest first</caption>
      <thead><tr><th scope="col">ID</th><th scope="col">Problem</th><th scope="col" class="hide-sm">Language</th>
        <th scope="col">Verdict</th><th scope="col" class="num">Time</th><th scope="col" class="num hide-sm">Memory</th>
        <th scope="col" class="hide-sm">Submitted</th></tr></thead>
      <tbody>${list.map(rowHtml).join("")}</tbody>
    </table></div></div>`;
}

function renderError(error) {
  region.setAttribute("aria-busy", "false");
  region.innerHTML = `<div class="state" data-kind="error" role="alert">
    <span class="state-icon">${icon("offline", { size: 22 })}</span>
    <h2>Could not load your submissions</h2><p>${escapeHtml(describeError(error))}</p>
    <button class="btn js-retry" type="button">${icon("refresh")}Try again</button></div>`;
  region.querySelector(".js-retry").addEventListener("click", load);
}

function updateRow(sub) {
  const tr = region.querySelector(`tr[data-id="${sub.id}"]`);
  if (!tr) return;
  const tpl = document.createElement("template");
  tpl.innerHTML = rowHtml(sub).trim();
  const fresh = tpl.content.firstElementChild;
  fresh.classList.add("is-updated");
  tr.replaceWith(fresh);
}

async function load() {
  region.setAttribute("aria-busy", "true");
  try {
    const [subs, problems] = await Promise.all([api.submissions(100, scope), api.problems().catch(() => null)]);
    const intro = document.querySelector(".js-intro");
    if (intro) {
      intro.textContent = scope === "all" ? "Every submission on this server (admin view). Statuses update live."
        : subs.account ? `Submissions of ${subs.account.username}, from any device. Statuses update live.`
          : "Only this browser's submissions are listed. Log in to keep them across devices.";
    }
    if (problems) {
      titles = Object.fromEntries(problems.problems.map((p) => [p.slug, `${p.index}. ${p.title}`]));
    }
    rows = new Map(subs.submissions.map((s) => [s.id, s]));
    if (scope === "mine") reconcileStatuses(subs.submissions);
    render();
  } catch (error) {
    renderError(error);
  }
}

const reloadSoon = debounce(load, 300);

function setChip(stateName, text) {
  chip.querySelector(".dot").dataset.state = stateName;
  chip.querySelector(".js-text").textContent = text;
}

if (isAdmin) {
  const toggle = document.querySelector(".js-scope");
  toggle.hidden = false;
  toggle.querySelector(`input[value="${scope}"]`).checked = true;
  toggle.addEventListener("change", (event) => {
    scope = event.target.value;
    history.replaceState(null, "", scope === "all" ? "?scope=all" : location.pathname);
    load();
  });
}

function listen() {
  openStream({ types: ["submission"] }, {
    submission: (data) => {
      const sub = rows.get(data.submission_id);
      if (!sub) {
        if (data.status === "Pending") reloadSoon();     // a new submission (another tab)
        return;
      }
      if (data.status === "skipped" || data.status === "rejected") return;
      const merged = { ...sub, ...data, id: sub.id };
      rows.set(sub.id, merged);
      updateRow(merged);
      if (FINAL.has(data.status)) {
        announce(`Submission ${sub.id}: ${verdictInfo(data.status).label}`);
        if (scope === "mine") reconcileStatuses([merged]);
      }
    },
  }, (s) => {
    if (s === "open") setChip("live", "Live");
    else if (s === "reconnecting") setChip("offline", "Reconnecting…");
    else if (s === "closed") setChip("offline", "Disconnected");
  });
}

// Keep "2 minutes ago" texts fresh.
setInterval(() => {
  region.querySelectorAll("time[datetime]").forEach((el) => {
    el.textContent = relativeTime(new Date(el.getAttribute("datetime")).getTime() / 1000);
  });
}, 30000);

load();
listen();
