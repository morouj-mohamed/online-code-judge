// page-result.js - the live result of one submission.
//
// Data flow:
//   1. GET /api/submissions/<id>?events=1   what is already known (also after a reload)
//   2. GET /api/problems/<slug>             title and number of tests (for the strip)
//   3. SSE /api/stream?submission=<id>      Pending -> Running -> one "test" event per
//                                           test -> final verdict -> "end"
//   4. On the final verdict: fetch the detail again (exact messages), reveal the
//      banner, count the numbers up, and for Wrong Answer run the code on the
//      PUBLIC sample to show a real diff.

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { renderDiff } from "./diff.js";
import {
  absoluteTime, countUp, escapeHtml, FINAL, formatKb, formatMs, LANGUAGE_LABELS,
  reducedMotion, relativeTime, splitUnit, verdictBadge, verdictInfo,
} from "./format.js";
import { icon } from "./icons.js";
import { openStream } from "./sse.js";
import { recordVerdict } from "./store.js";
import { toast } from "./toast.js";

boot();

const root = document.getElementById("result");
const id = Number(root.dataset.id);
const since = new URLSearchParams(location.search).get("since");
const body = document.querySelector(".js-body");

const state = {
  sub: null,           // the submission row (public fields)
  problem: null,
  total: 0,
  results: new Map(),  // test number -> {verdict, time_ms, memory_kb, message?}
  status: "Pending",
  phase: "",           // "compiling" while the compiler runs
  timeline: [],
  seen: new Set(),     // de-duplicates timeline entries (history + live)
  finalized: false,
  sawLive: false,      // true if we watched the verdict arrive (for the confetti)
};

// ================================================================ layout ====
function skeletonShell() {
  body.innerHTML = `
    <section class="banner js-banner" data-verdict="pending" aria-labelledby="banner-title">
      <span class="banner-icon js-banner-icon"></span>
      <div><p class="banner-title" id="banner-title"></p><p class="banner-sub js-banner-sub"></p></div>
      <div class="banner-meta js-banner-meta"></div>
    </section>

    <div class="metrics" role="group" aria-label="Measurements">
      ${metric("time", "clock", "Max CPU time", "measured by the kernel (wait4)")}
      ${metric("memory", "memory", "Peak memory", "resident set size")}
      ${metric("tests", "list-checks", "Tests passed", "stops at the first failure")}
      ${metric("lang", "file-code", "Language", "")}
    </div>

    <section class="card card-pad" aria-labelledby="strip-title">
      <div class="section-title"><h2 id="strip-title">Test progress</h2><span class="spacer"></span>
        <span class="subtle js-strip-caption"></span></div>
      <ol class="strip js-strip" aria-label="Per-test status"></ol>
      <div class="strip-legend" aria-hidden="true">
        <span class="row">${icon("hourglass", { size: 13 })}queued</span>
        <span class="row">${icon("loader", { size: 13 })}running</span>
        <span class="row">${icon("circle-check", { size: 13 })}passed</span>
        <span class="row">${icon("circle-x", { size: 13 })}failed</span>
        <span class="row">${icon("minus", { size: 13 })}not run</span>
      </div>
    </section>

    <section class="card js-ce" aria-labelledby="ce-title" hidden>
      <div class="card-head">${icon("braces")}<h2 id="ce-title">Compiler output</h2></div>
      <div class="card-pad"><pre class="console js-ce-text"></pre></div>
    </section>

    <section class="card js-diff" aria-labelledby="diff-title" hidden>
      <div class="card-head">${icon("columns")}<h2 id="diff-title">Sample check</h2>
        <span class="spacer"></span><span class="subtle" style="font-size: var(--text-xs)">public sample only</span></div>
      <div class="card-pad js-diff-body"></div>
    </section>

    <section class="card" aria-labelledby="tests-title">
      <div class="card-head">${icon("list")}<h2 id="tests-title">Per-test results</h2></div>
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th scope="col">Test</th><th scope="col">Verdict</th><th scope="col" class="num">CPU time</th>
            <th scope="col" class="num">Memory</th><th scope="col">Details</th></tr></thead>
          <tbody class="js-rows"></tbody>
        </table>
      </div>
    </section>

    <details class="card disclosure">
      <summary>${icon("chevron-right", { cls: "chev" })}${icon("file-code")}Source code
        <span class="spacer"></span><span class="subtle js-src-meta" style="font-weight: 500"></span></summary>
      <pre class="diff-lines code-view js-source" aria-label="Submitted source code"></pre>
    </details>

    <details class="card disclosure">
      <summary>${icon("chevron-right", { cls: "chev" })}${icon("terminal")}Under the hood: process lifecycle
        <span class="spacer"></span><span class="subtle js-tl-count" style="font-weight: 500"></span></summary>
      <ol class="timeline js-timeline" aria-label="Sandbox events"></ol>
    </details>`;
}

function metric(key, iconName, label, hint) {
  return `<div class="metric"><p class="label eyebrow">${icon(iconName, { size: 14 })}${label}</p>
    <p class="value js-m-${key}">—</p>${hint ? `<p class="hint">${hint}</p>` : ""}</div>`;
}

// ================================================================ render ====
function bannerText() {
  const s = state.sub;
  const v = verdictInfo(state.status);
  if (state.status === "Pending") return { title: "In queue", sub: "Waiting for a free worker…" };
  if (state.status === "Running") {
    if (state.phase === "compiling") return { title: "Compiling", sub: "The compiler runs in the sandbox too." };
    const done = state.results.size;
    return { title: "Judging", sub: `Running test ${Math.min(done + 1, state.total)} of ${state.total}…` };
  }
  if (state.status === "Accepted") {
    return { title: "Accepted", sub: `All ${s.tests_total ?? state.total} tests passed.` };
  }
  if (state.status === "Compilation Error") return { title: v.label, sub: "Your code did not compile. See the compiler output below." };
  if (state.status === "Judge Error") return { title: v.label, sub: "The judge itself failed. This is not caused by your code; please resubmit." };
  const msg = (s.message || "").replace(/^test \d+:\s*/, "");
  return { title: v.label, sub: `Failed on test ${s.failed_test}${msg ? ` · ${msg}` : ""}` };
}

function renderBanner() {
  const banner = body.querySelector(".js-banner");
  const v = verdictInfo(state.status);
  const { title, sub } = bannerText();
  banner.dataset.verdict = v.key;
  const glyph = state.status === "Pending" ? icon("hourglass", { size: 30 })
    : state.status === "Running" ? icon("loader", { size: 30, cls: "spinner" }) : icon(v.icon, { size: 30 });
  body.querySelector(".js-banner-icon").innerHTML = glyph;
  body.querySelector("#banner-title").textContent = title;
  body.querySelector(".js-banner-sub").textContent = sub;
  const s = state.sub;
  body.querySelector(".js-banner-meta").innerHTML = `
    ${verdictBadge(state.status, { large: true })}
    <span>${s.worker_id ? `worker ${s.worker_id} · ` : ""}<time datetime="${new Date(s.created_at * 1000).toISOString()}"
      title="${escapeHtml(absoluteTime(s.created_at))}">${escapeHtml(relativeTime(s.created_at))}</time></span>`;
}

function cellHtml(n) {
  const r = state.results.get(n);
  const failedAt = state.sub.failed_test;
  let cellState = "queued";
  let glyph = icon("hourglass", { size: 15 });
  let label = "queued";
  let verdictKey = "pending";
  if (r) {
    const v = verdictInfo(r.verdict);
    cellState = "done";
    glyph = icon(v.icon, { size: 15 });
    label = v.label;
    verdictKey = v.key;
  } else if (state.status === "Running" && state.phase !== "compiling" && n === state.results.size + 1) {
    cellState = "running";
    glyph = icon("loader", { size: 15 });
    label = "running";
    verdictKey = "running";
  } else if (FINAL.has(state.status) && (state.status === "Compilation Error" || (failedAt && n > failedAt))) {
    cellState = "skipped";
    glyph = icon("minus", { size: 15 });
    label = "not run";
  }
  return `<li class="cell" data-state="${cellState}" data-verdict="${verdictKey}" data-n="${n}"
    title="Test ${n}: ${escapeHtml(label)}">${glyph}<span class="n">${n}</span>
    <span class="sr-only">Test ${n}: ${escapeHtml(label)}</span></li>`;
}

function renderStrip() {
  const strip = body.querySelector(".js-strip");
  // Patch cells in place so a finished cell's "snap" animation plays only once.
  for (let n = 1; n <= state.total; n++) {
    const html = cellHtml(n);
    const existing = strip.querySelector(`[data-n="${n}"]`);
    const tpl = document.createElement("template");
    tpl.innerHTML = html.trim();
    const fresh = tpl.content.firstElementChild;
    if (!existing) strip.appendChild(fresh);
    else if (existing.dataset.state !== fresh.dataset.state || existing.dataset.verdict !== fresh.dataset.verdict) {
      existing.replaceWith(fresh);
    }
  }
  const passed = [...state.results.values()].filter((r) => r.verdict === "Accepted").length;
  body.querySelector(".js-strip-caption").textContent = `${passed} / ${state.total} passed`;
}

function renderRows() {
  const rows = [...state.results.entries()].sort((a, b) => a[0] - b[0]).map(([n, r]) => `
    <tr><td class="mono">#${n}</td><td>${verdictBadge(r.verdict)}</td>
      <td class="num">${formatMs(r.time_ms)}</td><td class="num">${formatKb(r.memory_kb)}</td>
      <td class="msg">${escapeHtml(r.message || (r.verdict === "Accepted" ? "output matches" : ""))}</td></tr>`).join("");
  body.querySelector(".js-rows").innerHTML = rows ||
    `<tr><td colspan="5" class="muted">${state.status === "Compilation Error" ? "No test was run: the code did not compile." : "No test has finished yet."}</td></tr>`;
}

function setMetric(key, html) {
  body.querySelector(`.js-m-${key}`).innerHTML = html;
}
const unitHtml = (text) => {
  const [n, u] = splitUnit(text);
  return `${n}<small>${u}</small>`;
};

function liveMaxima() {
  const values = [...state.results.values()];
  return {
    time: values.length ? Math.max(...values.map((r) => r.time_ms ?? 0)) : null,
    memory: values.length ? Math.max(...values.map((r) => r.memory_kb ?? 0)) : null,
    passed: values.filter((r) => r.verdict === "Accepted").length,
  };
}

function renderMetrics(animate = false) {
  const finished = FINAL.has(state.status);
  const m = liveMaxima();
  // Nothing ran after a compilation error: show "—", not a misleading "0 ms".
  const ranNothing = state.status === "Compilation Error" || state.results.size === 0;
  const time = ranNothing ? null : finished ? state.sub.time_ms ?? m.time : m.time;
  const memory = ranNothing ? null : finished ? state.sub.memory_kb ?? m.memory : m.memory;
  const timeEl = body.querySelector(".js-m-time");
  const memEl = body.querySelector(".js-m-memory");
  if (animate) {
    countUp(timeEl, time, (v) => (v == null ? "—" : unitHtml(formatMs(v))));
    countUp(memEl, memory, (v) => (v == null ? "—" : unitHtml(formatKb(v))));
  } else {
    timeEl.innerHTML = time == null ? "—" : unitHtml(formatMs(time));
    memEl.innerHTML = memory == null ? "—" : unitHtml(formatKb(memory));
  }
  setMetric("tests", `${m.passed}<small>/ ${state.total}</small>`);
  setMetric("lang", escapeHtml(LANGUAGE_LABELS[state.sub.language] || state.sub.language));
}

function renderAll() {
  renderBanner();
  renderStrip();
  renderRows();
  renderMetrics();
}

// ============================================================== timeline ====
function addTimeline(entry) {
  const key = `${entry.ts}|${entry.step}|${entry.pid}|${entry.details?.label ?? ""}`;
  if (state.seen.has(key) || state.timeline.length > 600) return;
  state.seen.add(key);
  state.timeline.push(entry);
  const list = body.querySelector(".js-timeline");
  const t0 = state.timeline[0].ts;
  const d = entry.details || {};
  const parts = Object.entries(d).filter(([k]) => k !== "label")
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" ");
  const li = document.createElement("li");
  li.innerHTML = `<span class="t">+${((entry.ts - t0) * 1000).toFixed(0)}ms</span>
    <span class="pid">${entry.pid ?? "—"}</span><span class="step">${escapeHtml(entry.step)}</span>
    <span class="d" title="${escapeHtml(parts)}">${escapeHtml(d.label ? `[${d.label}] ` : "")}${escapeHtml(parts)}</span>`;
  list.appendChild(li);
  body.querySelector(".js-tl-count").textContent = `${state.timeline.length} events`;
}

// ========================================================== final verdict ===
async function loadSampleDiff() {
  const card = body.querySelector(".js-diff");
  const target = body.querySelector(".js-diff-body");
  card.hidden = false;
  const cacheKey = `ocj-samplerun:${id}`;
  let result = null;
  try {
    result = JSON.parse(sessionStorage.getItem(cacheKey) || "null");
  } catch {
    result = null;
  }
  if (!result) {
    target.innerHTML = `<div class="stack" aria-busy="true"><p class="muted row">${icon("loader", { cls: "spinner" })}Running your code on the public sample…</p>
      <div class="skeleton" style="height: 120px"></div></div>`;
    try {
      result = await api.sampleRun(id);
      sessionStorage.setItem(cacheKey, JSON.stringify(result));
    } catch (error) {
      target.innerHTML = `<div class="state" data-kind="error"><span class="state-icon">${icon("alert")}</span>
        <p>${escapeHtml(describeError(error))}</p><button class="btn js-diff-retry" type="button">${icon("refresh")}Try again</button></div>`;
      target.querySelector(".js-diff-retry").addEventListener("click", loadSampleDiff);
      return;
    }
  }
  const diff = renderDiff(result.expected_output, result.stdout);
  let lead;
  if (result.verdict === "Wrong Answer") {
    lead = `Your program also fails the sample${diff.firstLine ? `, first on line ${diff.firstLine}` : ""}. Expected and actual output, side by side:`;
  } else if (result.verdict === "Accepted") {
    lead = `Your program passes the sample, so the bug only shows on hidden test ${state.sub.failed_test}. Hidden data is never shown; think about edge cases (limits, negative numbers, empty or very long input).`;
  } else {
    lead = `On the sample your program ends with: ${escapeHtml(result.verdict)}${result.message ? ` (${escapeHtml(result.message)})` : ""}.`;
  }
  target.innerHTML = `<p class="muted" style="margin-bottom: 16px">${lead}</p>${diff.html}`;
}

function confetti() {
  if (reducedMotion()) return;
  const canvas = document.createElement("canvas");
  canvas.className = "confetti";
  canvas.setAttribute("aria-hidden", "true");
  document.body.appendChild(canvas);
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = innerWidth * dpr;
  canvas.height = innerHeight * dpr;
  ctx.scale(dpr, dpr);
  const banner = body.querySelector(".js-banner-icon").getBoundingClientRect();
  const styles = getComputedStyle(document.documentElement);
  const colors = ["--v-ok", "--accent-strong", "--v-running", "--v-tle"].map((v) => styles.getPropertyValue(v).trim());
  const pieces = Array.from({ length: 90 }, () => ({
    x: banner.left + banner.width / 2,
    y: banner.top + banner.height / 2,
    vx: (Math.random() - 0.3) * 9,
    vy: -Math.random() * 9 - 3,
    size: 4 + Math.random() * 4,
    rot: Math.random() * Math.PI,
    vr: (Math.random() - 0.5) * 0.3,
    color: colors[Math.floor(Math.random() * colors.length)],
  }));
  const start = performance.now();
  const frame = (now) => {
    const t = now - start;
    ctx.clearRect(0, 0, innerWidth, innerHeight);
    ctx.globalAlpha = Math.max(0, 1 - t / 1400);
    for (const p of pieces) {
      p.vy += 0.28;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      ctx.save();
      ctx.translate(p.x, p.y);
      ctx.rotate(p.rot);
      ctx.fillStyle = p.color;
      ctx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
      ctx.restore();
    }
    if (t < 1400) requestAnimationFrame(frame);
    else canvas.remove();
  };
  requestAnimationFrame(frame);
}

async function finalize() {
  if (state.finalized) return;
  state.finalized = true;
  // Fetch the stored result: exact per-test messages and the final numbers.
  try {
    const fresh = await api.submission(id);
    applyDetail(fresh);
  } catch {
    /* keep what the stream told us */
  }
  renderAll();
  renderMetrics(true);
  const banner = body.querySelector(".js-banner");
  banner.classList.remove("is-revealed");
  void banner.offsetWidth;                      // restart the reveal animation
  banner.classList.add("is-revealed");
  if (state.status === "Accepted") {
    banner.classList.add("is-accepted");
    if (state.sawLive) confetti();
  }
  if (state.status === "Compilation Error") {
    body.querySelector(".js-ce").hidden = false;
    body.querySelector(".js-ce-text").textContent = state.sub.message || "(no compiler output)";
  }
  if (state.status === "Wrong Answer") loadSampleDiff();
  recordVerdict(state.sub.problem, state.status);
  body.setAttribute("aria-busy", "false");
  const { title, sub } = bannerText();
  announce(`Verdict: ${title}. ${sub}`);
  document.title = `${verdictInfo(state.status).short} · #${id} · ForkJudge`;
}

// ================================================================= data ====
function applyDetail(detail) {
  state.sub = detail;
  state.status = detail.status;
  for (const t of detail.tests || []) {
    state.results.set(t.test, { verdict: t.verdict, time_ms: t.time_ms, memory_kb: t.memory_kb, message: t.message });
  }
}

function renderHeader() {
  const s = state.sub;
  const p = state.problem;
  document.querySelector(".js-title").textContent = p ? `${p.index}. ${p.title}` : s.problem;
  document.querySelector(".js-subtitle").innerHTML =
    `${s.username ? `by <a href="/admin/users/${encodeURIComponent(s.username)}">${escapeHtml(s.username)}</a> · ` : ""}` +
    `${escapeHtml(LANGUAGE_LABELS[s.language] || s.language)} · submitted <time datetime="${new Date(s.created_at * 1000).toISOString()}">${escapeHtml(absoluteTime(s.created_at))}</time>` +
    `${s.contest ? ` · in <a href="/contests/${encodeURIComponent(s.contest.slug)}">${escapeHtml(s.contest.title)}</a>` : ""}`;
  if (typeof s.source === "string") {
    const lines = s.source.split("\n");
    body.querySelector(".js-source").innerHTML = lines.map((line, i) =>
      `<div class="diff-line"><span class="ln">${i + 1}</span><span class="tx">${escapeHtml(line) || " "}</span></div>`).join("");
    body.querySelector(".js-src-meta").textContent = `${lines.length} lines`;
  }
  document.querySelector(".js-actions").innerHTML = `
    <a class="btn" href="/submissions">${icon("list")}All submissions</a>
    <a class="btn btn-primary" href="/problems/${encodeURIComponent(s.problem)}${s.contest ? `?contest=${encodeURIComponent(s.contest.slug)}` : ""}">${icon("file-code")}Edit &amp; resubmit</a>`;
}

function listen() {
  openStream({ submission: id, since }, {
    snapshot: (row) => {
      state.sub = { ...state.sub, ...row };
      if (!FINAL.has(state.status)) state.status = row.status;
      renderAll();
    },
    submission: (data) => {
      if (data.status === "skipped" || data.status === "rejected") return;
      state.sub = { ...state.sub, ...data, id };
      state.status = data.status;
      if (data.status === "Running") state.phase = state.phase || "";
      if (FINAL.has(data.status)) {
        state.sawLive = !state.finalized;
        finalize();
      } else {
        renderAll();
        announce(data.status === "Running" ? "Judging started" : "Submission queued");
      }
    },
    test: (t) => {
      state.phase = "";
      state.results.set(t.test, { verdict: t.verdict, time_ms: t.time_ms, memory_kb: t.memory_kb });
      if (!FINAL.has(state.status)) state.status = "Running";
      renderAll();
    },
    judge: (e) => {
      addTimeline(e);
      if (e.step === "compile_start") state.phase = "compiling";
      if (e.step === "compile_done" || e.step === "compile_skipped" || e.step === "test_start") state.phase = "";
      if (!FINAL.has(state.status)) {
        if (state.status === "Pending") state.status = "Running";
        renderBanner();
        renderStrip();
      }
    },
  }, (s) => {
    if (s === "reconnecting" && !state.finalized) {
      toast({ title: "Connection lost", message: "Reconnecting to the live stream…", kind: "warning", timeout: 3000 });
    }
  });
}

async function start() {
  skeletonShell();
  let detail;
  try {
    detail = await api.submission(id, true);
  } catch (error) {
    const missing = error.status === 404;
    body.setAttribute("aria-busy", "false");
    document.querySelector(".js-title").textContent = missing ? "Submission not found" : "Could not load this submission";
    body.innerHTML = `<div class="state" data-kind="error" role="alert">
      <span class="state-icon">${icon(missing ? "search" : "offline", { size: 22 })}</span>
      <h2>${missing ? "No submission with this number in your history" : "The judge did not answer"}</h2>
      <p>${missing ? "Submissions are private to the browser that made them." : escapeHtml(describeError(error))}</p>
      <div class="row-wrap"><a class="btn" href="/submissions">${icon("list")}My submissions</a>
      ${missing ? "" : `<button class="btn btn-primary js-retry" type="button">${icon("refresh")}Try again</button>`}</div></div>`;
    body.querySelector(".js-retry")?.addEventListener("click", () => location.reload());
    return;
  }
  applyDetail(detail);
  try {
    state.problem = await api.problem(detail.problem);
    state.total = state.problem.tests_count;
  } catch {
    state.total = detail.tests_total || Math.max(1, (detail.tests || []).length);
  }
  renderHeader();
  for (const e of detail.events || []) if (e.source === "judge") addTimeline(e);
  renderAll();
  if (FINAL.has(state.status)) finalize();
  else listen();
}

start();
