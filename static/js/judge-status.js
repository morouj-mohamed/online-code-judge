// judge-status.js - the small "judge status" chip: how many workers are busy
// and how long the queue is, kept live by worker/queue Server-Sent Events.

import { api } from "./api.js";
import { openStream } from "./sse.js";

export function startJudgeStatus(chip) {
  const dot = chip.querySelector(".dot");
  const text = chip.querySelector(".js-status-text");
  const workers = new Map();     // worker id -> state
  let queue = { length: 0, capacity: 0 };
  let online = false;

  function render() {
    if (!online) return;
    const total = workers.size;
    const busy = [...workers.values()].filter((s) => s === "busy").length;
    dot.dataset.state = busy ? "busy" : "live";
    text.innerHTML = `Judge online <span class="sep" aria-hidden="true"></span>
      <span class="num">${busy}</span>/<span class="num">${total}</span> workers busy
      <span class="sep" aria-hidden="true"></span> queue <span class="num">${queue.length}</span>`;
  }

  function offline(message) {
    online = false;
    dot.dataset.state = "offline";
    text.textContent = message;
  }

  // Fetch a full picture of the pool (used at start and after a reconnect).
  async function refresh() {
    try {
      const state = await api.inspector();
      workers.clear();
      state.workers.forEach((w) => workers.set(w.id, w.state));
      queue = state.queue;
      online = true;
      render();
    } catch {
      offline("Judge unreachable");
    }
  }

  let stream = null;
  function connect() {
    refresh();
    stream = openStream({ types: ["worker", "queue"] }, {
      worker: (w) => {
        workers.set(w.id, w.state);
        render();
      },
      queue: (q) => {
        queue = q;
        render();
      },
      // The server is shutting down: this stream is over, open a new one later.
      end: () => {
        offline("Judge restarting…");
        setTimeout(connect, 3000);
      },
    }, (s) => {
      // EventSource reconnects by itself; when it is back, re-sync the numbers.
      if (s === "reconnecting") offline("Reconnecting…");
      else if (s === "open" && !online) refresh();
    });
  }
  connect();
  return () => stream && stream.close();
}
