// page-admin-contest.js - create or edit a contest: times, problems (ordered),
// exam mode, publish/hide, delete.

import { api, describeError } from "./api.js";
import { boot } from "./app.js";
import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";
import { toast } from "./toast.js";

boot();

const root = document.getElementById("admin-contest");
let slug = root.dataset.slug;
const form = document.querySelector(".js-form");
const errorBox = form.querySelector(".js-error");
const saveButton = form.querySelector(".js-save");
const picksEl = document.querySelector(".js-picks");
const addSelect = document.querySelector(".js-add");
const examBox = document.getElementById("c-exam");
const exitsField = document.querySelector(".js-exits-field");
let allProblems = [];
let picks = [];
let contest = null;

// datetime-local works in the browser's local time; the server stores Unix seconds.
const toInput = (unix) => {
  const d = new Date(unix * 1000);
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
};
const fromInput = (value) => (value ? new Date(value).getTime() / 1000 : NaN);

function renderPicks() {
  const bySlug = Object.fromEntries(allProblems.map((p) => [p.slug, p]));
  picksEl.innerHTML = picks.length ? picks.map((s, i) => {
    const p = bySlug[s] || { title: s, hidden: false };
    return `<li class="pick"><span class="pick-letter mono">${String.fromCharCode(65 + i)}</span>
      <span class="pick-title">${escapeHtml(p.title)} ${p.hidden ? '<span class="state-badge" data-state="hidden">hidden</span>' : ""}</span>
      <button class="btn btn-ghost btn-sm btn-icon" type="button" data-move="-1" data-i="${i}" aria-label="Move ${escapeHtml(p.title)} up" ${i === 0 ? "disabled" : ""}>${icon("arrow-up", { size: 14 })}</button>
      <button class="btn btn-ghost btn-sm btn-icon" type="button" data-move="1" data-i="${i}" aria-label="Move ${escapeHtml(p.title)} down" ${i === picks.length - 1 ? "disabled" : ""}>${icon("arrow-down", { size: 14 })}</button>
      <button class="btn btn-ghost btn-sm btn-icon" type="button" data-remove="${i}" aria-label="Remove ${escapeHtml(p.title)}">${icon("x", { size: 14 })}</button></li>`;
  }).join("") : '<li class="muted">No problem yet: add some below.</li>';
  addSelect.innerHTML = '<option value="">Choose…</option>' + allProblems.filter((p) => !picks.includes(p.slug))
    .map((p) => `<option value="${escapeHtml(p.slug)}">${escapeHtml(p.index)}. ${escapeHtml(p.title)}${p.hidden ? " (hidden)" : ""}</option>`).join("");
}

picksEl.addEventListener("click", (event) => {
  const move = event.target.closest("[data-move]");
  const remove = event.target.closest("[data-remove]");
  if (move) {
    const i = Number(move.dataset.i);
    const j = i + Number(move.dataset.move);
    [picks[i], picks[j]] = [picks[j], picks[i]];
  } else if (remove) {
    picks.splice(Number(remove.dataset.remove), 1);
  } else return;
  renderPicks();
});
addSelect.addEventListener("change", () => {
  if (addSelect.value) picks.push(addSelect.value);
  renderPicks();
  addSelect.focus();
});
examBox.addEventListener("change", () => { exitsField.hidden = !examBox.checked; });

function data() {
  return {
    slug: form.elements.slug.value.trim(),
    title: form.elements.title.value.trim(),
    description: form.elements.description.value,
    starts_at: fromInput(form.elements.starts.value),
    ends_at: fromInput(form.elements.ends.value),
    problems: picks,
    exam_mode: examBox.checked,
    max_exits: Number(form.elements.max_exits.value),
  };
}

function renderHead() {
  if (!contest) return;
  document.querySelector(".js-heading").textContent = contest.title;
  document.querySelector(".js-badges").innerHTML = `
    <span class="state-badge" data-state="${contest.hidden ? "hidden" : "published"}">${icon(contest.hidden ? "eye-off" : "eye", { size: 13 })}${contest.hidden ? "Hidden" : "Published"}</span>
    <span class="phase-pill" data-phase="${contest.phase}">${contest.phase}</span>`;
  const actions = document.querySelector(".js-head-actions");
  actions.innerHTML = `<a class="btn btn-ghost" href="/contests/${encodeURIComponent(contest.slug)}">${icon("eye")}Open</a>
    <a class="btn btn-ghost" href="/contests/${encodeURIComponent(contest.slug)}/standings">${icon("trophy")}Standings</a>
    <button class="btn ${contest.hidden ? "btn-primary" : ""} js-visibility" type="button">${icon(contest.hidden ? "eye" : "eye-off")}${contest.hidden ? "Publish" : "Hide"}</button>`;
  actions.querySelector(".js-visibility").addEventListener("click", async () => {
    try {
      contest = await api.admin.contestVisibility(slug, !contest.hidden);
      renderHead();
      toast({ title: contest.hidden ? "Hidden" : "Published", message: contest.hidden ? "Students cannot see it." : "Students can see it now.", kind: "success" });
    } catch (error) {
      toast({ title: "Could not change visibility", message: describeError(error), kind: "error" });
    }
  });
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  saveButton.classList.add("is-busy");
  saveButton.disabled = true;
  try {
    if (slug) {
      contest = await api.admin.updateContest(slug, data());
      toast({ title: "Saved", kind: "success" });
    } else {
      contest = await api.admin.createContest(data());
      slug = contest.slug;
      history.replaceState(null, "", `/admin/contests/${encodeURIComponent(slug)}`);
      form.elements.slug.readOnly = true;
      document.querySelector(".js-danger").hidden = false;
      saveButton.querySelector(".btn-label-idle").textContent = "Save changes";
      toast({ title: "Contest created", message: "It is hidden until you publish it.", kind: "success" });
    }
    renderHead();
    document.querySelector(".js-save-note").textContent = `Saved ${new Date().toLocaleTimeString()}`;
  } catch (error) {
    errorBox.innerHTML = `${icon("alert-octagon")}<span>${escapeHtml(describeError(error))}</span>`;
    errorBox.hidden = false;
    errorBox.scrollIntoView({ block: "center", behavior: "smooth" });
  } finally {
    saveButton.classList.remove("is-busy");
    saveButton.disabled = false;
  }
});

form.elements.title.addEventListener("input", () => {
  if (!slug && !form.elements.slug.dataset.touched) {
    form.elements.slug.value = form.elements.title.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
    document.querySelector(".js-slug-preview").textContent = form.elements.slug.value || "…";
  }
});
form.elements.slug.addEventListener("input", () => {
  form.elements.slug.dataset.touched = "1";
  document.querySelector(".js-slug-preview").textContent = form.elements.slug.value || "…";
});

const confirmInput = document.getElementById("c-delete-confirm");
const deleteButton = document.querySelector(".js-delete");
confirmInput.addEventListener("input", () => { deleteButton.disabled = confirmInput.value.trim() !== slug; });
deleteButton.addEventListener("click", async () => {
  try {
    await api.admin.deleteContest(slug);
    sessionStorage.setItem("ocj-flash", `Contest “${slug}” deleted.`);
    location.href = "/admin";
  } catch (error) {
    toast({ title: "Could not delete", message: describeError(error), kind: "error" });
  }
});

async function start() {
  const overview = await api.admin.problems();
  allProblems = overview.problems;
  if (slug) {
    contest = await api.admin.contest(slug);
    form.elements.slug.value = contest.slug;
    form.elements.slug.readOnly = true;
    form.elements.title.value = contest.title;
    form.elements.description.value = contest.description;
    form.elements.starts.value = toInput(contest.starts_at);
    form.elements.ends.value = toInput(contest.ends_at);
    examBox.checked = contest.exam_mode;
    form.elements.max_exits.value = String(contest.max_exits || 0);
    picks = [...contest.problems];
    document.querySelector(".js-slug-preview").textContent = contest.slug;
    renderHead();
  } else {
    const start = new Date(Date.now() + 10 * 60 * 1000);
    start.setSeconds(0, 0);
    form.elements.starts.value = toInput(start.getTime() / 1000);
    form.elements.ends.value = toInput(start.getTime() / 1000 + 2 * 3600);
    form.elements.title.focus();
  }
  exitsField.hidden = !examBox.checked;
  renderPicks();
}

start().catch((error) => toast({ title: "Could not load", message: describeError(error), kind: "error" }));
