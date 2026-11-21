// inspector-timeline.js - panel 3: the life of ONE process, step by step.
//
// A "run" is one fork() of the sandbox: the compiler, or the program on one
// test. All its events carry the same PID (the child's), so we group by
// (submission id, pid). Steps are shown in the order they REALLY happened,
// with the real time since fork(), and the numbers the kernel returned.

import { escapeHtml, formatKb } from "./format.js";
import { icon } from "./icons.js";

// What each event means. `explain` is the one-line OS concept.
const STEPS = {
  fork: { title: "fork()", explain: "The judge clones itself. The copy is a new process with its own PID; it shares nothing writable with the parent (copy-on-write)." },
  setsid: { title: "setsid()", explain: "The child starts a new session and process group (PGID = PID), so killpg() can later stop it AND everything it forks." },
  stdio_ready: { title: "dup2() + close fds", explain: "File descriptors 0, 1, 2 now point to the test input and the output files; every other inherited descriptor is closed and signals are reset." },
  limits_applied: { title: "setrlimit()", explain: "Kernel-enforced caps: CPU seconds, file size, open files, processes, no core dump. The program can never raise them again." },
  chroot_done: { title: "chroot()", explain: "The jail folder becomes '/': the real file system is invisible to the program." },
  privileges_dropped: { title: "setresuid()", explain: "Root is given up for good: the program runs as an unprivileged uid (one per worker)." },
  address_space_limit_applied: { title: "setrlimit(RLIMIT_AS)", explain: "The memory cap is set LAST, so the Python copy still has room to finish the steps above." },
  exec: { title: "execve()", explain: "The copy is replaced by the user's program (same PID). The close-on-exec pipe hits end-of-file, which tells the parent exec succeeded." },
  exec_failed: { title: "execve() failed", explain: "The kernel refused to start the program; the child reports errno through the pipe and exits." },
  running: { title: "running", explain: "The program runs. The parent polls with wait4(WNOHANG) and samples /proc/<pid>/status for memory, watching the wall clock." },
  watchdog_fired: { title: "watchdog", explain: "The wall-clock limit passed (the program was sleeping or blocked, so RLIMIT_CPU could not stop it): killpg(pgid, SIGKILL)." },
  exited: { title: "exit()", explain: "The program returned from main or called exit(); the low 8 bits of the status are the exit code." },
  killed_by_signal: { title: "killed by a signal", explain: "The kernel terminated the program with a signal (WIFSIGNALED); WTERMSIG says which one." },
  stray_processes_killed: { title: "strays killed", explain: "Processes the program left in its group were killed with killpg()." },
  orphans_reaped: { title: "orphans reaped", explain: "Grandchildren that escaped the group came back to the judge (it is a subreaper) and were waited for." },
  reaped: { title: "wait4() → reaped", explain: "The parent collected the exit status and the rusage (CPU user/system time, max RSS). The zombie entry is gone." },
};

const SIGNAL_MEANING = {
  SIGKILL: "cannot be caught: sent by the watchdog or by the hard CPU limit",
  SIGXCPU: "CPU time limit (RLIMIT_CPU soft limit) reached",
  SIGSEGV: "invalid memory access",
  SIGFPE: "arithmetic error, e.g. integer division by zero",
  SIGABRT: "the program aborted itself (e.g. failed assert, std::bad_alloc not caught)",
  SIGXFSZ: "wrote past RLIMIT_FSIZE",
  SIGBUS: "bad memory access (bus error)",
};

export function createTimeline(listSelect, followBox, target) {
  const runs = new Map();       // key -> run
  const order = [];             // keys, oldest first
  let selected = null;

  const keyOf = (sid, pid) => `${sid}|${pid}`;

  function runTitle(run) {
    const verdict = run.verdict ? ` · ${run.verdict}` : run.done ? "" : " · running";
    return `#${run.sid} · ${run.label} · pid ${run.pid}${verdict}`;
  }

  function refreshSelect() {
    const keep = selected;
    listSelect.innerHTML = order.length
      ? [...order].reverse().slice(0, 80).map((k) => `<option value="${escapeHtml(k)}">${escapeHtml(runTitle(runs.get(k)))}</option>`).join("")
      : '<option value="">Waiting for a run… (submit something, or press Stress test)</option>';
    if (keep && runs.has(keep)) listSelect.value = keep;
  }

  function endText(run) {
    const killed = run.events.find((e) => e.step === "killed_by_signal");
    const exited = run.events.find((e) => e.step === "exited");
    if (killed) {
      const name = killed.details.signal;
      return `killed by ${name} (${killed.details.number})${SIGNAL_MEANING[name] ? ` — ${SIGNAL_MEANING[name]}` : ""}`;
    }
    if (exited) return exited.details.exit_code === 0 ? "exited normally, code 0" : `exited with code ${exited.details.exit_code}`;
    return null;
  }

  function stepDetail(e, run) {
    const d = e.details || {};
    switch (e.step) {
      case "fork": return `child PID <b>${d.child_pid}</b> · ${escapeHtml((d.argv || []).join(" "))}`;
      case "setsid": return `session ${d.sid}, process group ${d.pgid}`;
      case "limits_applied": return Object.entries(d).filter(([k]) => k.startsWith("RLIMIT")).map(([k, v]) => `${k.replace("RLIMIT_", "")}=${v}`).join(" · ");
      case "address_space_limit_applied": return `RLIMIT_AS = ${d.RLIMIT_AS_mb} MB`;
      case "chroot_done": return `new root: ${escapeHtml(d.new_root)}, cwd ${escapeHtml(d.cwd)}`;
      case "privileges_dropped": return `uid ${d.uid}, euid ${d.euid}, gid ${d.gid}`;
      case "exec_failed": return `${escapeHtml(d.error)} (errno ${d.errno})`;
      case "watchdog_fired": return `wall-clock limit ${d.wall_limit_s} s · ${escapeHtml(d.action)}`;
      case "exited": return d.exit_code === 0 ? "exit code <b>0</b>" : `exit code <b>${d.exit_code}</b>`;
      case "killed_by_signal": return `<b>${escapeHtml(d.signal)} (${d.number})</b>${SIGNAL_MEANING[d.signal] ? ` — ${SIGNAL_MEANING[d.signal]}` : ""}`;
      case "stray_processes_killed": return `${d.count} process(es): ${escapeHtml((d.pids || []).join(", "))}`;
      case "orphans_reaped": return `${d.count} orphan(s)`;
      case "reaped": return `user <b>${d.cpu_user_ms} ms</b> · system <b>${d.cpu_sys_ms} ms</b> · wall ${d.wall_ms} ms · ru_maxrss <b>${formatKb(d.maxrss_kb)}</b>` +
        (run.memoryKb != null ? ` · program peak <b>${formatKb(run.memoryKb)}</b>` : "");
      default: return d.note ? escapeHtml(d.note) : "";
    }
  }

  function render() {
    const run = selected && runs.get(selected);
    if (!run) {
      target.innerHTML = `<div class="state"><span class="state-icon">${icon("terminal", { size: 22 })}</span>
        <h3>No process yet</h3><p>Submit a solution or press <strong>Stress test</strong>: every fork() appears here.</p></div>`;
      return;
    }
    const t0 = run.events[0].ts;
    const rows = [];
    let execTs = null;
    for (const e of run.events) {
      const def = STEPS[e.step];
      if (!def) continue;
      if (e.step === "exec") execTs = e.ts;
      // The "running" phase sits between exec and the end.
      if (execTs && ["exited", "killed_by_signal", "watchdog_fired"].includes(e.step) && !rows.some((r) => r.key === "running")) {
        const reaped = run.events.find((x) => x.step === "reaped");
        rows.push({ key: "running", ts: execTs, title: STEPS.running.title, explain: STEPS.running.explain,
          detail: reaped ? `about ${Math.round(reaped.details.wall_ms)} ms on the wall clock` : "" });
      }
      rows.push({ key: e.step, ts: e.ts, title: def.title, explain: def.explain, detail: stepDetail(e, run),
        tone: e.step === "killed_by_signal" || e.step === "watchdog_fired" || e.step === "exec_failed" ? "bad" : e.step === "exited" && e.details.exit_code !== 0 ? "bad" : "" });
    }
    if (execTs && !run.done && !rows.some((r) => r.key === "running")) {
      rows.push({ key: "running", ts: execTs, title: STEPS.running.title, explain: STEPS.running.explain, detail: "still running…", live: true });
    }
    const end = endText(run);
    target.innerHTML = `
      <div class="run-summary">
        <span class="mono">#${run.sid}</span><span>${escapeHtml(run.label)}</span><span class="mono">PID ${run.pid}</span>
        ${run.worker ? `<span>worker ${run.worker}</span>` : ""}
        ${end ? `<span class="run-end" data-tone="${/code 0/.test(end) ? "ok" : "bad"}">${escapeHtml(end)}</span>` : '<span class="run-end">running…</span>'}
        ${run.verdict ? `<span>verdict: <strong>${escapeHtml(run.verdict)}</strong></span>` : ""}
      </div>
      <ol class="lifecycle">${rows.map((r, i) => `
        <li class="lc-step${r.tone ? " is-bad" : ""}${r.live ? " is-live" : ""}">
          <span class="lc-dot" aria-hidden="true"></span>
          <div class="lc-body">
            <div class="row-wrap"><strong class="lc-title">${escapeHtml(r.title)}</strong>
              <span class="mono subtle">+${((r.ts - t0) * 1000).toFixed(1)} ms</span>
              <button class="btn btn-ghost btn-sm lc-why" type="button" aria-expanded="false" aria-controls="why-${i}" title="${escapeHtml(r.explain)}">why?</button></div>
            ${r.detail ? `<p class="lc-detail">${r.detail}</p>` : ""}
            <p class="lc-explain" id="why-${i}" hidden>${escapeHtml(r.explain)}</p>
          </div>
        </li>`).join("")}</ol>
      <p class="subtle hint">Steps appear in the order the kernel really performed them. ru_maxrss also counts the forked
        copy of the judge before execve(); "program peak" is the program's own peak memory the judge measured.</p>`;
  }

  target.addEventListener("click", (event) => {
    const button = event.target.closest(".lc-why");
    if (!button) return;
    const text = document.getElementById(button.getAttribute("aria-controls"));
    const open = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!open));
    text.hidden = open;
  });
  listSelect.addEventListener("change", () => {
    selected = listSelect.value || null;
    followBox.checked = false;
    render();
  });
  followBox.addEventListener("change", () => {
    if (followBox.checked && order.length) {
      selected = order[order.length - 1];
      refreshSelect();
      render();
    }
  });

  return {
    /** A judge event from the stream. */
    add(e) {
      if (e.pid == null || e.submission_id == null) return;
      const key = keyOf(e.submission_id, e.pid);
      let run = runs.get(key);
      if (!run) {
        if (e.step !== "fork") return;            // we only follow runs we saw from the start
        run = { key, sid: e.submission_id, pid: e.pid, label: e.details?.label || "run", worker: e.worker_id,
          events: [], done: false, verdict: null, memoryKb: null };
        runs.set(key, run);
        order.push(key);
        if (order.length > 200) runs.delete(order.shift());
        if (followBox.checked) selected = key;
      }
      run.events.push(e);
      if (e.step === "reaped") run.done = true;
      refreshSelect();
      if (selected === key) render();
    },
    /** A "test" event: the verdict and measured memory of one test run. */
    testResult(t) {
      for (let i = order.length - 1; i >= 0; i--) {
        const run = runs.get(order[i]);
        if (run && run.sid === t.submission_id && run.label === `test ${t.test}`) {
          run.verdict = t.verdict;
          run.memoryKb = t.memory_kb;
          refreshSelect();
          if (selected === run.key) render();
          return;
        }
      }
    },
    render,
  };
}
