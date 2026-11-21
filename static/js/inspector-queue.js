// inspector-queue.js - panel 2: the bounded producer/consumer queue.
// Data: queue snapshots {items, length, capacity, produced, consumed}, sent by
// WorkerPool._publish_queue after every put() and get().

import { escapeHtml } from "./format.js";

export function createQueuePanel(cellsEl, outEl, statsEl) {
  let previous = [];
  const recentOut = [];           // [{id, worker}] newest first
  const takenBy = new Map();      // submission id -> worker id (from worker events)

  function render(q) {
    const before = new Set(previous);
    const now = new Set(q.items);
    // Items that left the queue since the last snapshot: they went to a worker.
    for (const id of previous) {
      if (!now.has(id)) {
        recentOut.unshift({ id, worker: takenBy.get(id) });
        recentOut.length = Math.min(recentOut.length, 6);
      }
    }
    const cells = [];
    const shown = Math.min(q.capacity, 100);
    for (let i = 0; i < shown; i++) {
      const id = q.items[i];
      if (id === undefined) cells.push('<li class="q-cell" data-empty="true"><span class="sr-only">empty slot</span></li>');
      else cells.push(`<li class="q-cell${before.has(id) ? "" : " is-new"}" title="submission #${id}"><span class="mono">${id}</span></li>`);
    }
    cellsEl.innerHTML = cells.join("");
    cellsEl.style.setProperty("--cols", String(Math.min(shown, 20)));
    outEl.innerHTML = recentOut.map((o, i) => `<li class="q-out${i === 0 ? " is-new" : ""}">
      <span class="mono">#${o.id}</span>${o.worker ? `<span class="subtle">→ W${o.worker}</span>` : ""}</li>`).join("");
    statsEl.innerHTML = `
      <span class="qs"><span class="qs-label">length / capacity</span><span class="num">${q.length} / ${q.capacity}</span></span>
      <span class="qs"><span class="qs-label">produced (put)</span><span class="num">${q.produced}</span></span>
      <span class="qs"><span class="qs-label">consumed (get)</span><span class="num">${q.consumed}</span></span>
      <span class="qs"><span class="qs-label"><code>empty_slots</code> semaphore</span><span class="num">${q.capacity - q.length}</span></span>
      <span class="qs"><span class="qs-label"><code>full_slots</code> semaphore</span><span class="num">${q.length}</span></span>`;
    previous = q.items.slice();
  }

  return {
    render,
    workerTook(submissionId, workerId) {
      if (submissionId) takenBy.set(submissionId, workerId);
      const entry = recentOut.find((o) => o.id === submissionId);
      if (entry && !entry.worker) {
        entry.worker = workerId;
        outEl.querySelectorAll(".q-out").forEach((li) => {
          if (li.textContent.includes(`#${submissionId}`) && !li.textContent.includes("→")) {
            li.insertAdjacentHTML("beforeend", `<span class="subtle">→ W${escapeHtml(String(workerId))}</span>`);
          }
        });
      }
    },
  };
}
