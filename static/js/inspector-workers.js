// inspector-workers.js - panel 1: one card per worker thread.
// Data: the "workers" list of /api/inspector/state, then "worker" SSE events
// (published by WorkerPool._set_worker whenever a worker changes state).

import { escapeHtml } from "./format.js";
import { icon } from "./icons.js";

const STATE = {
  idle: { label: "Idle", icon: "circle", hint: "blocked in queue.get(): sleeping on the full_slots semaphore" },
  busy: { label: "Busy", icon: "loader", hint: "judging: a child process is running, the thread waits in wait4()" },
  stopped: { label: "Stopped", icon: "x", hint: "the queue was closed and drained: the thread ended" },
};

export function createWorkersPanel(root, summary, clock) {
  const workers = new Map();      // id -> record from the server
  const doing = new Map();        // worker id -> "test 3 · exec" (last judge step)

  function card(w) {
    const st = STATE[w.state] || STATE.idle;
    const elapsed = Math.max(0, clock() - w.since);
    return `<li class="worker-card" data-state="${w.state}">
      <div class="row"><span class="worker-name">Worker ${w.id}</span><span class="spacer"></span>
        <span class="worker-state" data-state="${w.state}" title="${escapeHtml(st.hint)}">${icon(st.icon, { size: 14, cls: w.state === "busy" ? "spinner" : "" })}${st.label}</span></div>
      <dl class="worker-facts">
        <div><dt>Submission</dt><dd class="mono">${w.submission_id ? `#${w.submission_id}` : "—"}</dd></div>
        <div><dt>Child PID</dt><dd class="mono">${w.child_pid ?? "—"}</dd></div>
        <div><dt>${w.state === "busy" ? "Busy for" : "Idle for"}</dt><dd class="mono js-elapsed" data-since="${w.since}">${formatElapsed(elapsed)}</dd></div>
        <div><dt>Sandbox uid</dt><dd class="mono">${w.sandbox_uid ?? "—"}</dd></div>
      </dl>
      <p class="worker-doing">${w.state === "busy" && doing.get(w.id) ? escapeHtml(doing.get(w.id)) : `${w.jobs_done} job${w.jobs_done === 1 ? "" : "s"} done`}</p>
    </li>`;
  }

  function render() {
    const list = [...workers.values()].sort((a, b) => a.id - b.id);
    root.innerHTML = list.map(card).join("");
    const busy = list.filter((w) => w.state === "busy").length;
    summary.textContent = `${busy} of ${list.length} busy`;
  }

  return {
    setAll(list) {
      workers.clear();
      list.forEach((w) => workers.set(w.id, w));
      render();
    },
    update(w) {
      const old = workers.get(w.id) || {};
      workers.set(w.id, { ...old, ...w });
      if (w.state !== "busy") doing.delete(w.id);
      render();
    },
    judgeStep(e) {
      if (e.worker_id == null) return;
      const label = e.details?.label ? `${e.details.label} · ` : "";
      doing.set(e.worker_id, `now: ${label}${e.step.replace(/_/g, " ")}`);
      const w = workers.get(e.worker_id);
      if (w && w.state === "busy") render();
    },
    tick() {
      root.querySelectorAll(".js-elapsed").forEach((el) => {
        el.textContent = formatElapsed(Math.max(0, clock() - Number(el.dataset.since)));
      });
    },
  };
}

function formatElapsed(seconds) {
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m} min ${Math.floor(seconds % 60)} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}
