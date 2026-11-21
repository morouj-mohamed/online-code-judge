// page-admin.js - admin overview: every problem (published or hidden) with its
// tests, validation status and submission counts, plus the list of accounts.

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { absoluteTime, escapeHtml, relativeTime } from "./format.js";
import { stateBadge, validationBadge } from "./admin-badges.js";
import { icon } from "./icons.js";

boot();

const region = document.getElementById("admin-problems");
const usersRegion = document.getElementById("admin-users");

function render(data) {
  region.setAttribute("aria-busy", "false");
  if (!data.problems.length) {
    region.innerHTML = `<div class="state"><span class="state-icon">${icon("file-code", { size: 22 })}</span>
      <h2>No problems yet</h2><a class="btn btn-primary" href="/admin/problems/new">${icon("plus")}New problem</a></div>`;
  } else {
    region.innerHTML = `<div class="card"><div class="table-wrap"><table class="table">
      <caption class="sr-only">All problems</caption>
      <thead><tr><th scope="col">#</th><th scope="col">Problem</th><th scope="col">Visibility</th>
        <th scope="col" class="num">Tests</th><th scope="col">Reference check</th>
        <th scope="col" class="num">Accepted / submissions</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${data.problems.map((p) => `<tr>
        <td class="mono">${escapeHtml(p.index)}</td>
        <td><a class="row-link" href="/admin/problems/${encodeURIComponent(p.slug)}">${escapeHtml(p.title)}</a>
          <div class="subtle mono" style="font-size: var(--text-xs)">${escapeHtml(p.slug)} · ${escapeHtml(p.difficulty)}</div></td>
        <td>${stateBadge(p.hidden)}</td>
        <td class="num">${p.tests.count}</td>
        <td>${validationBadge(p.validation)}</td>
        <td class="num">${p.accepted} / ${p.submissions}</td>
        <td><div class="row" style="justify-content: flex-end">
          <a class="btn btn-ghost btn-sm" href="/problems/${encodeURIComponent(p.slug)}">${icon("eye", { size: 14 })}<span>View</span></a>
          <a class="btn btn-sm" href="/admin/problems/${encodeURIComponent(p.slug)}">${icon("edit", { size: 14 })}<span>Edit</span></a></div></td>
      </tr>`).join("")}</tbody></table></div></div>`;
  }
  usersRegion.innerHTML = data.users.length ? `<div class="card"><div class="table-wrap"><table class="table">
      <caption class="sr-only">Accounts</caption>
      <thead><tr><th scope="col">User</th><th scope="col">Role</th><th scope="col" class="num">Submissions</th>
        <th scope="col">Created</th><th scope="col">Last login</th></tr></thead>
      <tbody>${data.users.map((u) => `<tr><td><a class="row-link" href="/admin/users/${encodeURIComponent(u.username)}">${escapeHtml(u.username)}</a></td>
        <td>${u.is_admin ? '<span class="role-pill">admin</span>' : '<span class="subtle">user</span>'}</td>
        <td class="num">${u.submissions}</td>
        <td><time title="${escapeHtml(absoluteTime(u.created_at))}">${escapeHtml(relativeTime(u.created_at))}</time></td>
        <td>${u.last_login_at ? escapeHtml(relativeTime(u.last_login_at)) : "—"}</td></tr>`).join("")}</tbody>
    </table></div></div>` : `<p class="muted">No accounts yet.</p>`;
}

async function loadContests() {
  const target = document.getElementById("admin-contests");
  try {
    const { contests } = await api.admin.contests();
    target.innerHTML = contests.length ? `<div class="card"><div class="table-wrap"><table class="table">
      <caption class="sr-only">Contests</caption>
      <thead><tr><th scope="col">Contest</th><th scope="col">When</th><th scope="col">Status</th><th scope="col" class="num">Problems</th>
        <th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>${contests.map((c) => `<tr>
        <td><a class="row-link" href="/admin/contests/${encodeURIComponent(c.slug)}">${escapeHtml(c.title)}</a>
          ${c.exam_mode ? `<span class="state-badge" data-state="hidden">${icon("lock", { size: 13 })}exam</span>` : ""}</td>
        <td>${escapeHtml(new Date(c.starts_at * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }))}</td>
        <td><span class="phase-pill" data-phase="${c.phase}">${c.phase}</span> ${stateBadge(c.hidden)}</td>
        <td class="num">${c.problem_count}</td>
        <td><div class="row" style="justify-content: flex-end">
          <a class="btn btn-ghost btn-sm" href="/contests/${encodeURIComponent(c.slug)}/standings">${icon("trophy", { size: 14 })}<span>Standings</span></a>
          <a class="btn btn-sm" href="/admin/contests/${encodeURIComponent(c.slug)}">${icon("edit", { size: 14 })}<span>Edit</span></a></div></td>
      </tr>`).join("")}</tbody></table></div></div>`
      : `<div class="state"><span class="state-icon">${icon("trophy", { size: 22 })}</span><h3>No contests yet</h3>
        <a class="btn btn-primary" href="/admin/contests/new">${icon("plus")}New contest</a></div>`;
  } catch (error) {
    target.innerHTML = `<p class="muted">${escapeHtml(describeError(error))}</p>`;
  }
}

async function load() {
  loadContests();
  try {
    render(await api.admin.problems());
  } catch (error) {
    region.setAttribute("aria-busy", "false");
    region.innerHTML = `<div class="state" data-kind="error" role="alert"><span class="state-icon">${icon("alert")}</span>
      <h2>Could not load</h2><p>${escapeHtml(describeError(error))}</p>
      <button class="btn js-retry" type="button">${icon("refresh")}Try again</button></div>`;
    region.querySelector(".js-retry").addEventListener("click", load);
  }
}

load();
