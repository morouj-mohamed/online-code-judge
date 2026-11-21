// page-contest.js - one contest: its problems (overview) or its standings.
// The standings refresh every 10 seconds while the contest runs.

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { escapeHtml, formatCountdown, formatDateTime, startCountdown } from "./format.js";
import { icon } from "./icons.js";
import { renderMarkdown } from "./markdown.js";
import { toast } from "./toast.js";

boot();

const root = document.getElementById("contest");
const slug = root.dataset.slug;
const view = root.dataset.view;
const region = document.getElementById("contest-region");
const isAdmin = document.body.dataset.admin === "1";
const loggedIn = Boolean(document.body.dataset.user);
let stopClock = null;
let refreshTimer = null;

function renderClock(c) {
  const clock = document.querySelector(".js-clock");
  if (stopClock) stopClock();
  stopClock = startCountdown({ startsAt: c.starts_at, endsAt: c.ends_at, serverTime: c.server_time }, (left, phase, before) => {
    const label = phase === "upcoming" ? "Starts in" : phase === "running" ? "Ends in" : "Finished";
    clock.dataset.phase = phase;
    clock.innerHTML = `<span class="label">${label}</span>${phase === "ended" ? "" : `<span class="time">${formatCountdown(left)}</span>`}`;
    if (before && before !== phase) {
      announce(phase === "running" ? "The contest has started" : "The contest is over");
      load();
    }
  });
}

function renderMeta(c) {
  document.querySelector(".js-meta").innerHTML = `
    <span class="phase-pill" data-phase="${c.phase}">${c.phase === "running" ? "Running" : c.phase === "upcoming" ? "Upcoming" : "Finished"}</span>
    <span class="chip">${icon("calendar", { size: 13 })}${escapeHtml(formatDateTime(c.starts_at))} → ${escapeHtml(formatDateTime(c.ends_at))}</span>
    ${c.exam_mode ? `<span class="state-badge" data-state="hidden">${icon("lock", { size: 13 })}Exam mode${c.max_exits ? ` · locks after ${c.max_exits} exit${c.max_exits > 1 ? "s" : ""}` : ""}</span>` : ""}
    ${c.hidden ? `<span class="state-badge">${icon("eye-off", { size: 13 })}Hidden from students</span>` : ""}`;
}

function overview(c) {
  const notice = !loggedIn && c.phase !== "ended"
    ? `<div class="notice">${icon("user")}<span>You need an account to take part. <a href="/login?next=${encodeURIComponent(location.pathname)}">Log in</a> or <a href="/signup">create one</a>.</span></div>` : "";
  const exam = c.exam_mode && c.phase !== "ended"
    ? `<div class="notice notice-warn">${icon("lock")}<span><strong>Exam mode.</strong> Solve in fullscreen. Leaving the exam page
        (another tab, another program, leaving fullscreen) is recorded with the time${c.max_exits ? `, and after ${c.max_exits} exit${c.max_exits > 1 ? "s" : ""} your exam is locked: you cannot submit anymore` : ""}.</span></div>` : "";
  const description = c.description ? `<article class="prose card card-pad">${renderMarkdown(c.description, { skipTitle: false })}</article>` : "";
  if (c.phase === "upcoming" && !c.problems.length) {
    return `${notice}${exam}${description}<div class="state"><span class="state-icon">${icon("calendar", { size: 22 })}</span>
      <h2>The problems appear when the contest starts</h2><p>This page refreshes by itself at the start.</p></div>`;
  }
  const rows = c.problems.map((p) => {
    const href = `/problems/${encodeURIComponent(p.slug)}?contest=${encodeURIComponent(slug)}`;
    const mine = p.mine === "solved" ? `<span class="status-label" data-status="solved">${icon("circle-check", { size: 15 })}Solved</span>`
      : p.mine === "attempted" ? `<span class="status-label" data-status="attempted">${icon("circle-half", { size: 15 })}Attempted</span>`
        : `<span class="status-label">${icon("circle", { size: 15 })}—</span>`;
    return `<tr><td class="mono letter-cell">${p.letter}</td>
      <td><a class="row-link" href="${href}">${escapeHtml(p.title)}</a><div class="subtle" style="font-size: var(--text-xs)">${escapeHtml(p.difficulty)}</div></td>
      <td class="num">${p.solved_by} / ${p.tried_by}</td><td>${mine}</td></tr>`;
  }).join("");
  return `${notice}${exam}${description}<div class="card"><div class="table-wrap"><table class="table">
    <caption class="sr-only">Contest problems</caption>
    <thead><tr><th scope="col">#</th><th scope="col">Problem</th><th scope="col" class="num">Solved / tried</th><th scope="col">You</th></tr></thead>
    <tbody>${rows}</tbody></table></div></div>`;
}

function cellHtml(cell, isFirst) {
  if (cell.solved) {
    return `<td class="st-cell" data-state="solved"${isFirst ? ' data-first="true"' : ""}>
      <span class="st-main">+${cell.attempts || ""}</span><span class="st-sub">${cell.time_min}'</span>
      <span class="sr-only">solved at minute ${cell.time_min} after ${cell.attempts} rejected attempts${isFirst ? ", first to solve" : ""}</span></td>`;
  }
  if (cell.attempts || cell.pending) {
    return `<td class="st-cell" data-state="${cell.pending ? "pending" : "rejected"}">
      <span class="st-main">${cell.pending ? "?" : `-${cell.attempts}`}</span>
      <span class="sr-only">${cell.attempts} rejected attempts${cell.pending ? ", one being judged" : ""}</span></td>`;
  }
  return `<td class="st-cell"><span class="sr-only">not tried</span></td>`;
}

function standings(t) {
  if (t.phase === "upcoming" && !t.rows.length && !t.problems.length) {
    return `<div class="state"><span class="state-icon">${icon("trophy", { size: 22 })}</span><h2>The standings start with the contest</h2></div>`;
  }
  const showIntegrity = isAdmin && t.exam_mode;
  const head = t.problems.map((p) => `<th scope="col" class="st-head" title="${escapeHtml(p.title)}">${p.letter}</th>`).join("");
  const rows = t.rows.map((r) => `<tr${r.username === t.me ? ' class="is-me"' : ""}>
      <td class="num">${r.rank}</td>
      <td class="st-user">${isAdmin ? `<a class="row-link" href="/admin/users/${encodeURIComponent(r.username)}">${escapeHtml(r.username)}</a>` : escapeHtml(r.username)}
        ${r.username === t.me ? '<span class="role-pill">you</span>' : ""}</td>
      <td class="num st-solved">${r.solved}</td><td class="num">${r.penalty}</td>
      ${t.problems.map((p) => cellHtml(r.cells[p.slug], t.first_solves[p.slug] === r.username)).join("")}
      ${showIntegrity ? `<td class="num">${r.integrity?.total
        ? `<span class="exit-count" title="${escapeHtml(Object.entries(r.integrity).filter(([k]) => k !== "total").map(([k, v]) => `${k}: ${v}`).join(", "))}">${icon("alert", { size: 13 })}${r.integrity.total}</span>` : "0"}</td>
        <td>${r.locked ? `<button class="btn btn-sm js-unlock" type="button" data-user="${escapeHtml(r.username)}">${icon("unlock", { size: 14 })}Unlock</button>` : ""}</td>` : ""}
    </tr>`).join("");
  return `<div class="card"><div class="table-wrap"><table class="table standings">
      <caption class="sr-only">Standings: solved problems, then penalty minutes</caption>
      <thead><tr><th scope="col" class="num">#</th><th scope="col">User</th><th scope="col" class="num">Solved</th>
        <th scope="col" class="num">Penalty</th>${head}
        ${showIntegrity ? '<th scope="col" class="num" title="Times the student left the exam page">Exits</th><th scope="col"><span class="sr-only">Exam lock</span></th>' : ""}</tr></thead>
      <tbody>${rows || `<tr><td colspan="${4 + t.problems.length + (showIntegrity ? 2 : 0)}" class="muted">Nobody has submitted yet.</td></tr>`}</tbody>
    </table></div></div>
    <p class="strip-legend"><span><strong>+2 37'</strong> solved at minute 37 after 2 rejected tries</span>
      <span><strong>-3</strong> three rejected tries</span><span><strong>?</strong> being judged</span>
      <span>Penalty = minute of each first Accepted + 20 per rejected try (Compilation Errors are free)</span>
      ${showIntegrity ? "<span><strong>Exits</strong> times the student left the exam page (admins only)</span>" : ""}</p>`;
}

region.addEventListener("click", async (event) => {
  const button = event.target.closest(".js-unlock");
  if (!button) return;
  button.disabled = true;
  try {
    await api.admin.unlock(slug, button.dataset.user);
    toast({ title: `${button.dataset.user} unlocked`, message: "Their exit count starts again from zero.", kind: "success" });
    load();
  } catch (error) {
    toast({ title: "Could not unlock", message: describeError(error), kind: "error" });
    button.disabled = false;
  }
});

async function load() {
  try {
    const data = view === "standings" ? await api.standings(slug) : await api.contest(slug);
    renderMeta(data);
    renderClock(data);
    region.setAttribute("aria-busy", "false");
    region.innerHTML = view === "standings" ? standings(data) : overview(data);
    clearTimeout(refreshTimer);
    if (view === "standings" && data.phase === "running") refreshTimer = setTimeout(load, 10000);
  } catch (error) {
    region.setAttribute("aria-busy", "false");
    region.innerHTML = `<div class="state" data-kind="error" role="alert"><span class="state-icon">${icon("offline")}</span>
      <h2>Could not load the contest</h2><p>${escapeHtml(describeError(error))}</p></div>`;
  }
}

load();
