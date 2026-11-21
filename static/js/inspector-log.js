// inspector-log.js - panel 6: every event as one line, newest at the bottom.
// Each line has a TEXT label ([JUDGE], [WORKER] ...) plus an icon, so the type
// never depends on color. Filterable by type, pausable, capped at 400 lines.

import { escapeHtml, formatKb } from "./format.js";
import { icon } from "./icons.js";

const TYPES = {
  judge: { label: "JUDGE", icon: "terminal" },
  test: { label: "TEST", icon: "list-checks" },
  submission: { label: "SUBMISSION", icon: "file-code" },
  worker: { label: "WORKER", icon: "server" },
  queue: { label: "QUEUE", icon: "layers" },
};
const MAX_LINES = 400;

function describe(type, d) {
  switch (type) {
    case "judge": {
      const label = d.details?.label ? `${d.details.label} · ` : "";
      const extra = d.step === "killed_by_signal" ? ` ${d.details.signal} (${d.details.number})`
        : d.step === "exited" ? ` code ${d.details.exit_code}`
          : d.step === "reaped" ? ` user ${d.details.cpu_user_ms} ms, sys ${d.details.cpu_sys_ms} ms, maxrss ${formatKb(d.details.maxrss_kb)}`
            : d.step === "fork" ? ` → child ${d.details.child_pid}` : "";
      return `#${d.submission_id}${d.worker_id ? ` W${d.worker_id}` : ""}${d.pid ? ` pid ${d.pid}` : ""} · ${label}${d.step}${extra}`;
    }
    case "test": return `#${d.submission_id} test ${d.test} → ${d.verdict} (${d.time_ms} ms, ${formatKb(d.memory_kb)})`;
    case "submission": return `#${d.submission_id} → ${d.status}${d.failed_test ? ` on test ${d.failed_test}` : ""}`;
    case "worker": return `W${d.id} → ${d.state}${d.submission_id ? ` (#${d.submission_id}${d.child_pid ? `, child ${d.child_pid}` : ""})` : ""}`;
    case "queue": return `length ${d.length}/${d.capacity} · produced ${d.produced} · consumed ${d.consumed}${d.closed ? " · closed" : ""}`;
    default: return JSON.stringify(d);
  }
}

export function createLog(listEl, filtersEl, pauseBtn, clearBtn, pausedEl) {
  const shown = new Set(Object.keys(TYPES));
  let paused = false;
  let buffered = [];
  let pending = [];
  let frame = 0;

  filtersEl.innerHTML = Object.entries(TYPES).map(([k, t]) =>
    `<button type="button" class="chip log-filter" data-type="${k}" aria-pressed="true">${icon(t.icon, { size: 13 })}${t.label}</button>`).join("");
  filtersEl.addEventListener("click", (event) => {
    const b = event.target.closest("[data-type]");
    if (!b) return;
    const on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(on));
    if (on) shown.add(b.dataset.type);
    else shown.delete(b.dataset.type);
    listEl.querySelectorAll(`[data-type="${b.dataset.type}"]`).forEach((li) => { li.hidden = !on; });
  });

  function line(entry) {
    const t = TYPES[entry.type];
    const time = new Date(entry.ts * 1000);
    const stamp = `${time.toLocaleTimeString([], { hour12: false })}.${String(time.getMilliseconds()).padStart(3, "0")}`;
    const bad = entry.type === "judge" && ["killed_by_signal", "watchdog_fired", "exec_failed"].includes(entry.data.step);
    return `<li class="log-line${bad ? " is-bad" : ""}" data-type="${entry.type}"${shown.has(entry.type) ? "" : " hidden"}>
      <span class="log-time mono">${stamp}</span>
      <span class="log-type" data-type="${entry.type}">${icon(t.icon, { size: 12 })}[${t.label}]</span>
      <span class="log-text">${escapeHtml(describe(entry.type, entry.data))}</span></li>`;
  }

  const scroller = listEl.parentElement;           // the role="log" box that scrolls

  function flush() {
    frame = 0;
    if (!pending.length) return;
    // Follow the newest line, unless the user scrolled up to read.
    const nearBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
    listEl.insertAdjacentHTML("beforeend", pending.map(line).join(""));
    pending = [];
    while (listEl.children.length > MAX_LINES) listEl.firstElementChild.remove();
    if (nearBottom) scroller.scrollTop = scroller.scrollHeight;
  }

  pauseBtn.addEventListener("click", () => {
    paused = !paused;
    pauseBtn.setAttribute("aria-pressed", String(paused));
    pauseBtn.querySelector("span:last-child").textContent = paused ? "Resume" : "Pause";
    if (!paused) {
      pending.push(...buffered);
      buffered = [];
      pausedEl.textContent = "";
      flush();
    }
  });
  clearBtn.addEventListener("click", () => {
    listEl.innerHTML = "";
    buffered = [];
    pausedEl.textContent = "";
  });

  return {
    add(type, data, ts) {
      const entry = { type, data, ts: ts || data.ts || Date.now() / 1000 };
      if (paused) {
        buffered.push(entry);
        if (buffered.length > MAX_LINES) buffered.shift();
        pausedEl.textContent = `paused · ${buffered.length} new`;
        return;
      }
      pending.push(entry);
      if (!frame) frame = requestAnimationFrame(flush);   // batch: one DOM update per frame
    },
  };
}
