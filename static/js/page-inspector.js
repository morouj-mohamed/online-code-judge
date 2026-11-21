// page-inspector.js - the OS Inspector page.
//
// 1. GET /api/inspector/state: a consistent snapshot (workers, queue with its
//    items and counters, isolation, last rlimits, language multipliers) plus
//    the event sequence number at that moment.
// 2. Open ONE Server-Sent Events stream that replays the last ~300 events
//    (so the log and the timeline are not empty) and then stays live.
//    Replayed worker/queue events older than the snapshot are shown in the log
//    only; they must not overwrite the newer snapshot.
// Nothing here is simulated: every value comes from the judge.

import { api, describeError } from "./api.js";
import { announce, boot } from "./app.js";
import { escapeHtml, FINAL } from "./format.js";
import { icon } from "./icons.js";
import { initConcepts } from "./inspector-concepts.js";
import { createLog } from "./inspector-log.js";
import { createQueuePanel } from "./inspector-queue.js";
import { createTimeline } from "./inspector-timeline.js";
import { createWorkersPanel } from "./inspector-workers.js";
import { openStream } from "./sse.js";
import { toast } from "./toast.js";

boot();

const $ = (sel) => document.querySelector(sel);
const isAdmin = document.body.dataset.admin === "1";
let offset = 0;                                   // server clock - browser clock (seconds)
const serverNow = () => Date.now() / 1000 + offset;

const workers = createWorkersPanel($(".js-workers"), $(".js-workers-sum"), serverNow);
const queue = createQueuePanel($(".js-queue-cells"), $(".js-queue-out"), $(".js-queue-stats"));
const timeline = createTimeline($(".js-run-select"), $(".js-follow"), $(".js-timeline"));
const log = createLog($(".js-log"), $(".js-log-filters"), $(".js-log-pause"), $(".js-log-clear"), $(".js-log-paused"));
initConcepts($(".js-drawer"));

// ------------------------------------------------------------- 5. isolation
const ISOLATION = {
  full: "chroot + uid drop + rlimits + process group",
  "setuid-only": "uid drop + rlimits + process group",
  "chroot-only": "chroot + rlimits + process group",
  basic: "rlimits + process group",
};
function renderIsolation(iso) {
  const label = ISOLATION[iso.level] || iso.level;
  $(".js-isolation").innerHTML = `<span class="iso-badge" data-level="${escapeHtml(iso.level)}">${icon("shield", { size: 14 })}
    <span>Isolation: <strong>${escapeHtml(label)}</strong></span></span>`;
  const items = [
    ["chroot jail", iso.chroot, "the program sees only the jail folder as '/'"],
    [`uid drop (uid ${iso.sandbox_uid_base}+worker)`, iso.drop_privileges, "runs without root, one uid per worker"],
    ["resource limits (setrlimit)", true, "CPU, memory, processes, file size, open files"],
    ["own process group (setsid)", true, "killpg() stops the program and all its children"],
    ["wall-clock watchdog", true, "SIGKILL for programs that sleep or block"],
  ];
  $(".js-isolation-detail").innerHTML = `<p class="iso-level">${icon("shield", { size: 18 })}<strong>${escapeHtml(label)}</strong></p>
    <ul class="iso-list">${items.map(([name, on, why]) => `<li data-on="${on}">
      ${icon(on ? "circle-check" : "circle-x", { size: 15 })}<span><strong>${escapeHtml(name)}</strong>
      <span class="subtle">${on ? "" : "not available here · "}${escapeHtml(why)}</span></span>
      <span class="sr-only">${on ? "active" : "not active"}</span></li>`).join("")}</ul>
    <p class="subtle hint">Probed at start-up: the judge TRIES chroot and setuid in a throw-away child and reports what the kernel allowed.</p>`;
}

// ------------------------------------------------------------- 4. limits
const LIMIT_INFO = {
  RLIMIT_CPU_s: ["CPU time", "s", "SIGXCPU at the soft limit, SIGKILL one second later"],
  RLIMIT_AS_mb: ["Address space", "MB", "malloc / new / mmap fail (MemoryError, bad_alloc)"],
  RLIMIT_FSIZE_mb: ["File size", "MB", "SIGXFSZ on the write that crosses it"],
  RLIMIT_NOFILE: ["Open files", "", "open() fails with EMFILE"],
  RLIMIT_NPROC: ["Processes per uid", "", "fork() fails with EAGAIN (stops fork bombs)"],
  RLIMIT_CORE: ["Core dump size", "bytes", "no core file is written on a crash"],
};
let lastLimits = null;
function renderLimits(last) {
  if (!last || !last.values) return;
  lastLimits = last;
  $(".js-limits-source").innerHTML = `Applied to <strong>${escapeHtml(last.label)}</strong>${last.submission_id ? ` of submission <span class="mono">#${last.submission_id}</span>` : ""} · child PID <span class="mono">${last.pid}</span>`;
  $(".js-limits").innerHTML = Object.entries(LIMIT_INFO).map(([key, [name, unit, what]]) => {
    const value = last.values[key];
    return `<tr><td><code>${key.replace(/_(s|mb)$/, "")}</code><div class="subtle" style="font-size: var(--text-xs)">${name}</div></td>
      <td class="num">${value == null ? "—" : `${value}${unit ? ` ${unit}` : ""}`}</td><td class="msg">${what}</td></tr>`;
  }).join("");
}
function renderMultipliers(state) {
  const cfg = state.limits_config;
  $(".js-multipliers").innerHTML = Object.entries(state.languages).map(([k, l]) =>
    `<tr><td>${escapeHtml(l.label)}</td><td class="num">${l.time_mult}×</td><td class="num">${l.mem_mult}×</td></tr>`).join("");
  $(".js-watchdog").textContent = `Wall-clock watchdog = time limit × ${cfg.watchdog_wall_factor} + ${cfg.watchdog_extra_s} s. ` +
    `RLIMIT_CPU is the time limit rounded up to whole seconds; the exact millisecond check uses the measured CPU time.`;
}

// ------------------------------------------------------------- 7. throughput
const finishedAt = [];      // server timestamps of finished submissions
const testsAt = [];
let totalSeen = 0;
function renderThroughput() {
  const now = serverNow();
  while (finishedAt.length && now - finishedAt[0] > 120) finishedAt.shift();
  while (testsAt.length && now - testsAt[0] > 60) testsAt.shift();
  const last60 = finishedAt.filter((t) => now - t <= 60).length;
  $(".js-tp-60").textContent = String(last60);
  $(".js-tp-rate").textContent = `${last60} / min`;
  $(".js-tp-tests").textContent = String(testsAt.length);
  $(".js-tp-total").textContent = `since this page opened: ${totalSeen}`;
  const buckets = Array(24).fill(0);
  for (const t of finishedAt) {
    const i = 23 - Math.floor((now - t) / 5);
    if (i >= 0 && i < 24) buckets[i] += 1;
  }
  const max = Math.max(1, ...buckets);
  $(".js-tp-bars").innerHTML = buckets.map((n) => `<span style="--h:${(n / max) * 100}%" title="${n}"></span>`).join("");
}

$(".js-stress").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.classList.add("is-busy");
  button.disabled = true;
  try {
    const r = await api.request("POST", "/api/inspector/stress", {});
    toast({ title: `${r.submitted} submissions queued`, message: r.rejected ? `${r.rejected} refused: the queue was full.` : "Watch the queue fill and the workers drain it.", kind: "success" });
    announce(`${r.submitted} submissions queued for the stress test`);
  } catch (error) {
    toast({ title: "Stress test refused", message: describeError(error), kind: "error" });
  } finally {
    button.classList.remove("is-busy");
    button.disabled = false;
  }
});

// ------------------------------------------------------------- stream
function setStream(state, text) {
  $(".js-stream .dot").dataset.state = state;
  $(".js-stream-text").textContent = text;
}

async function start() {
  let state;
  try {
    state = await api.inspector();
  } catch (error) {
    setStream("offline", "Judge unreachable");
    toast({ title: "Cannot reach the judge", message: describeError(error), kind: "error" });
    return;
  }
  offset = state.server_time - Date.now() / 1000;
  const snapshotSeq = state.events.published;
  workers.setAll(state.workers);
  queue.render(state.queue);
  renderIsolation(state.isolation);
  renderLimits(state.last_limits);
  renderMultipliers(state);
  timeline.render();

  openStream({ since: Math.max(0, snapshotSeq - 300), scope: isAdmin ? "all" : null }, {
    worker(w, id) {
      log.add("worker", w, w.since);
      if (Number(id) > snapshotSeq) {
        workers.update(w);
        if (w.state === "busy" && w.submission_id) queue.workerTook(w.submission_id, w.id);
      }
    },
    queue(q, id) {
      log.add("queue", q);
      if (Number(id) > snapshotSeq) queue.render(q);
    },
    judge(e) {
      log.add("judge", e, e.ts);
      timeline.add(e);
      workers.judgeStep(e);
      const label = String(e.details?.label || "");
      if ((e.step === "limits_applied" || e.step === "address_space_limit_applied") && label.startsWith("test")) {
        const sameRun = lastLimits && lastLimits.pid === e.pid;
        const values = { ...(sameRun ? lastLimits.values : {}) };
        for (const [k, v] of Object.entries(e.details)) if (k.startsWith("RLIMIT")) values[k] = v;
        renderLimits({ pid: e.pid, label, submission_id: e.submission_id, values });
      }
    },
    test(t, id) {
      log.add("test", t);
      timeline.testResult(t);
      if (Number(id) > snapshotSeq) testsAt.push(serverNow());
    },
    submission(s, id) {
      log.add("submission", s);
      if (FINAL.has(s.status) && Number(id) > snapshotSeq) {
        finishedAt.push(serverNow());
        totalSeen += 1;
      }
    },
    end() {
      setStream("offline", "Judge restarting…");
      setTimeout(() => location.reload(), 4000);
    },
  }, (s) => {
    if (s === "open") setStream("live", isAdmin ? "Live · all submissions" : "Live");
    else if (s === "reconnecting") setStream("offline", "Reconnecting…");
  });
}

setInterval(() => {
  workers.tick();
  renderThroughput();
}, 1000);
start();
