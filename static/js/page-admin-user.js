// page-admin-user.js - one student, for the teacher: totals, verdicts,
// every problem, every exam exit, every submission (with its code).

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import {
  absoluteTime, escapeHtml, formatKb, formatMs, LANGUAGE_LABELS, relativeTime, verdictBadge, verdictInfo,
} from "./format.js";
import { icon } from "./icons.js";

boot();

const username = document.getElementById("admin-user").dataset.username;
const region = document.getElementById("user-region");

function render(d) {
  document.querySelector(".js-subtitle").textContent =
    `${d.user.is_admin ? "Admin" : "Student"} · joined ${relativeTime(d.user.created_at)}` +
    (d.user.last_login_at ? ` · last login ${relativeTime(d.user.last_login_at)}` : "");
  const total = d.totals.submissions || 1;
  const order = ["Accepted", "Wrong Answer", "Time Limit Exceeded", "Memory Limit Exceeded", "Runtime Error", "Compilation Error"];
  const bars = order.filter((v) => d.totals.verdicts[v]).map((v) => {
    const info = verdictInfo(v);
    const n = d.totals.verdicts[v];
    return `<div class="vbar" data-verdict="${info.key}"><span class="vbar-label">${icon(info.icon, { size: 13 })}${info.label}</span>
      <span class="vbar-track"><span class="vbar-fill" style="width:${(100 * n / total).toFixed(1)}%"></span></span><span class="num">${n}</span></div>`;
  }).join("");
  region.setAttribute("aria-busy", "false");
  region.innerHTML = `
    <div class="metrics">
      <div class="metric"><p class="label eyebrow">${icon("circle-check", { size: 14 })}Solved</p><p class="value">${d.totals.solved}</p></div>
      <div class="metric"><p class="label eyebrow">${icon("file-code", { size: 14 })}Problems tried</p><p class="value">${d.totals.tried}</p></div>
      <div class="metric"><p class="label eyebrow">${icon("list", { size: 14 })}Submissions</p><p class="value">${d.totals.submissions}</p></div>
      <div class="metric"><p class="label eyebrow">${icon("alert", { size: 14 })}Exam exits</p><p class="value">${d.exam_events.filter((e) => e.kind !== "large_paste").length}</p></div>
    </div>
    <div class="two-col">
      <section class="card card-pad" aria-labelledby="v-title"><h2 id="v-title" class="card-title" style="margin-bottom: 12px">Verdicts</h2>
        ${bars || '<p class="muted">No submissions yet.</p>'}</section>
      <section class="card" aria-labelledby="p-title"><div class="card-head"><h2 id="p-title">Problems</h2></div>
        <div class="table-wrap"><table class="table"><thead><tr><th scope="col">Problem</th><th scope="col">Status</th>
          <th scope="col" class="num">Tries</th><th scope="col">Last</th></tr></thead><tbody>
        ${d.problems.map((p) => `<tr><td><strong>${escapeHtml(p.title)}</strong></td>
          <td>${p.solved ? `<span class="status-label" data-status="solved">${icon("circle-check", { size: 15 })}Solved</span>`
            : `<span class="status-label" data-status="attempted">${icon("circle-half", { size: 15 })}Not yet</span>`}</td>
          <td class="num">${p.attempts}</td><td><a class="row-link" href="/submissions/${p.last_id}">#${p.last_id}</a></td></tr>`).join("")
          || '<tr><td colspan="4" class="muted">Nothing tried yet.</td></tr>'}</tbody></table></div></section>
    </div>
    ${d.exam_events.length ? `<section class="card" aria-labelledby="e-title"><div class="card-head">${icon("lock")}<h2 id="e-title">Exam events</h2></div>
      <div class="table-wrap"><table class="table"><thead><tr><th scope="col">When</th><th scope="col">Contest</th><th scope="col">What happened</th></tr></thead>
      <tbody>${d.exam_events.map((e) => `<tr><td><time title="${escapeHtml(absoluteTime(e.ts))}">${escapeHtml(new Date(e.ts * 1000).toLocaleTimeString())}</time>
        <div class="subtle" style="font-size: var(--text-xs)">${escapeHtml(new Date(e.ts * 1000).toLocaleDateString())}</div></td>
        <td><a class="row-link" href="/contests/${encodeURIComponent(e.contest)}/standings">${escapeHtml(e.contest_title)}</a></td>
        <td>${escapeHtml(e.label)}${e.detail ? ` <span class="subtle">(${escapeHtml(e.detail)})</span>` : ""}</td></tr>`).join("")}</tbody></table></div></section>` : ""}
    <section class="card" aria-labelledby="s-title"><div class="card-head">${icon("list")}<h2 id="s-title">Submissions</h2>
      <span class="spacer"></span><span class="subtle" style="font-size: var(--text-xs)">open one to see the code</span></div>
      <div class="table-wrap"><table class="table"><thead><tr><th scope="col">ID</th><th scope="col">Problem</th><th scope="col">Language</th>
        <th scope="col">Verdict</th><th scope="col" class="num">Time</th><th scope="col" class="num">Memory</th><th scope="col">When</th></tr></thead>
      <tbody>${d.submissions.map((s) => `<tr><td class="mono"><a class="row-link" href="/submissions/${s.id}">#${s.id}</a></td>
        <td>${escapeHtml(s.problem)}</td><td><span class="lang-pill">${escapeHtml(LANGUAGE_LABELS[s.language] || s.language)}</span></td>
        <td>${verdictBadge(s.status)}</td><td class="num">${formatMs(s.time_ms)}</td><td class="num">${formatKb(s.memory_kb)}</td>
        <td><time title="${escapeHtml(absoluteTime(s.created_at))}">${escapeHtml(relativeTime(s.created_at))}</time></td></tr>`).join("")
        || '<tr><td colspan="7" class="muted">No submissions.</td></tr>'}</tbody></table></div></section>`;
}

document.querySelector(".js-reset-password").addEventListener("click", async (event) => {
  if (!window.confirm(`Give ${username} a new password? The old one stops working and they are logged out.`)) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api.admin.resetPassword(username);
    const box = document.querySelector(".js-new-password");
    box.innerHTML = `New password: <strong class="mono">${escapeHtml(result.password)}</strong> <span class="subtle">(shown once)</span>`;
    box.hidden = false;
  } catch (error) {
    alert(describeError(error));
  } finally {
    button.disabled = false;
  }
});

api.admin.user(username).then(render).catch((error) => {
  region.setAttribute("aria-busy", "false");
  region.innerHTML = `<div class="state" data-kind="error" role="alert"><span class="state-icon">${icon("alert")}</span>
    <h2>Could not load</h2><p>${escapeHtml(describeError(error))}</p></div>`;
});
