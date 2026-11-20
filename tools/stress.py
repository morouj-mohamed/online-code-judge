#!/usr/bin/env python3
"""
tools/stress.py - throw 20 mixed submissions at the worker pool at once.

It uses the real sandbox: compiles and runs the programs from tests/ (good ones,
wrong ones, infinite loops, a memory hog, a fork bomb, ...) plus a few reference
solutions of other problems. While they run it shows a live view of the queue
length and of what every worker is doing, then checks the final results.

    python3 tools/stress.py                 # 3 workers
    python3 tools/stress.py --workers 1     # compare: one worker is much slower
    python3 tools/stress.py --plain         # no terminal tricks, one line per change

Exit status: 0 if every check passed, 1 otherwise.
"""

import argparse
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from judge import db as dbmod  # noqa: E402
from judge import events, sandbox, worker  # noqa: E402

TESTS = os.path.join(config.BASE_DIR, "tests")
REF = os.path.join(config.BASE_DIR, "tools", "reference")

A, WA, TLE, MLE, RE, CE = (config.ACCEPTED, config.WRONG_ANSWER, config.TIME_LIMIT_EXCEEDED,
                           config.MEMORY_LIMIT_EXCEEDED, config.RUNTIME_ERROR,
                           config.COMPILATION_ERROR)

# (label, problem, language, source file, expected verdict, expected failing test)
SUBMISSIONS = [
    ("correct.cpp",          "sum-two",        "cpp",    f"{TESTS}/correct.cpp",           A,   None),
    ("correct.cpp (again)",  "sum-two",        "cpp",    f"{TESTS}/correct.cpp",           A,   None),
    ("slow_but_correct.cpp", "sum-two",        "cpp",    f"{TESTS}/slow_but_correct.cpp",  A,   None),
    ("slow_but_correct.cpp", "sum-two",        "cpp",    f"{TESTS}/slow_but_correct.cpp",  A,   None),
    ("wrong_answer.cpp",     "sum-two",        "cpp",    f"{TESTS}/wrong_answer.cpp",      WA,  5),
    ("wrong_answer.cpp",     "sum-two",        "cpp",    f"{TESTS}/wrong_answer.cpp",      WA,  5),
    ("infinite_loop.c",      "sum-two",        "c",      f"{TESTS}/infinite_loop.c",       TLE, 1),
    ("memory_hog.cpp",       "sum-two",        "cpp",    f"{TESTS}/memory_hog.cpp",        MLE, 1),
    ("segfault.c",           "sum-two",        "c",      f"{TESTS}/segfault.c",            RE,  1),
    ("divide_by_zero.c",     "sum-two",        "c",      f"{TESTS}/divide_by_zero.c",      RE,  1),
    ("fork_bomb.c",          "sum-two",        "c",      f"{TESTS}/fork_bomb.c",           TLE, 1),
    ("file_write_bomb.c",    "sum-two",        "c",      f"{TESTS}/file_write_bomb.c",     RE,  1),
    ("compile_error.cpp",    "sum-two",        "cpp",    f"{TESTS}/compile_error.cpp",     CE,  None),
    ("correct.py",           "sum-two",        "python", f"{TESTS}/correct.py",            A,   None),
    ("infinite_loop.py",     "sum-two",        "python", f"{TESTS}/infinite_loop.py",      TLE, 1),
    ("memory_hog.py",        "sum-two",        "python", f"{TESTS}/memory_hog.py",         MLE, 1),
    ("runtime_error.py",     "sum-two",        "python", f"{TESTS}/runtime_error.py",      RE,  1),
    ("fib-mod.cpp",          "fib-mod",        "cpp",    f"{REF}/fib-mod.cpp",             A,   None),
    ("prime-check.py",       "prime-check",    "python", f"{REF}/prime-check.py",          A,   None),
    ("max-array.cpp",        "max-array",      "cpp",    f"{REF}/max-array.cpp",           A,   None),
]


def temp_dirs():
    return {n for n in os.listdir(config.TMP_ROOT)
            if n.startswith("judge-") and not n.startswith(("judge-pyruntime", "judge-probe"))}


class LiveView:
    """Draws the queue and the workers. Redraws in place on a terminal, else prints changes."""

    def __init__(self, labels, plain):
        self.labels = labels
        self.plain = plain
        self.drawn_lines = 0
        self.last_key = None
        self.recent = []

    def render(self, snap, total, t0):
        done = snap["stats"]["finished"]
        q = snap["queue"]
        key = (q["length"], done, tuple((w["state"], w["submission_id"], w["child_pid"]) for w in snap["workers"]))
        if self.plain and key == self.last_key:
            return
        self.last_key = key
        bar_len = 20
        filled = min(bar_len, round(bar_len * q["length"] / max(1, min(total, q["capacity"]))))
        lines = [f"t=+{time.time() - t0:5.1f}s  queue [{'#' * filled}{'.' * (bar_len - filled)}] "
                 f"{q['length']:>2} waiting   finished {done}/{total}"]
        for w in snap["workers"]:
            if w["state"] == worker.BUSY and w["submission_id"] is not None:
                what = f"#{w['submission_id']:<3} {self.labels.get(w['submission_id'], '?'):<22}"
                pid = f"child pid {w['child_pid']}" if w["child_pid"] else "(preparing)"
                lines.append(f"  worker {w['id']}  BUSY   {what} {pid}")
            else:
                lines.append(f"  worker {w['id']}  {w['state']:<6} jobs done: {w['jobs_done']}")
        if self.plain:
            print(" | ".join(l.strip() for l in lines), flush=True)
            return
        for r in self.recent[-4:]:
            lines.append("  finished: " + r)
        if self.drawn_lines:
            sys.stdout.write(f"\033[{self.drawn_lines}A")
        for line in lines:
            sys.stdout.write("\033[2K" + line + "\n")
        sys.stdout.flush()
        self.drawn_lines = len(lines)

    def finished(self, text):
        if self.plain:
            print("    -> " + text, flush=True)
        else:
            self.recent.append(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=config.WORKERS)
    parser.add_argument("--plain", action="store_true", help="no in-place redraw")
    parser.add_argument("--seed", type=int, default=2026, help="seed of the shuffle")
    args = parser.parse_args()

    plain = args.plain or not sys.stdout.isatty()
    iso = sandbox.get_isolation()
    print(f"Isolation: {iso.description}")
    print(f"Workers  : {args.workers}   (sandbox uids {config.SANDBOX_UID}..{config.SANDBOX_UID + args.workers - 1})")

    order = list(SUBMISSIONS)
    random.Random(args.seed).shuffle(order)

    workdir = tempfile.mkdtemp(prefix="stress-db-")
    db = dbmod.Database(os.path.join(workdir, "stress.sqlite3"))
    broker = events.EventBroker()
    pool = worker.WorkerPool(db, broker, n_workers=args.workers)
    status_feed = broker.subscribe(types={"submission"})
    temp_before = temp_dirs()
    pool.start()

    # ---- enqueue all 20 at once -------------------------------------------------
    t0 = time.time()
    specs = {}
    for label, problem, language, path, expected, expected_test in order:
        with open(path, encoding="utf-8") as f:
            sid = pool.submit(problem, language, f.read())
        specs[sid] = (label, problem, expected, expected_test)
    labels = {sid: spec[0] for sid, spec in specs.items()}
    print(f"Submitted {len(specs)} submissions in {time.time() - t0:.3f} s "
          f"(queue holds up to {config.QUEUE_CAPACITY})\n")

    # ---- live view ----------------------------------------------------------------
    view = LiveView(labels, plain)
    peak_busy = 0
    t0 = time.time()
    while True:
        snap = pool.snapshot()
        peak_busy = max(peak_busy, sum(1 for w in snap["workers"] if w["state"] == worker.BUSY))
        for ev in status_feed.drain():
            status = ev["data"]["status"]
            if status in dbmod.FINAL_STATUSES:
                extra = f" at test {ev['data']['failed_test']}" if ev["data"].get("failed_test") else ""
                view.finished(f"#{ev['submission_id']:<3} {labels[ev['submission_id']]:<22} {status}{extra}"
                              f"  (worker {ev['data']['worker_id']})")
        view.render(snap, len(specs), t0)
        if snap["outstanding"] == 0:
            break
        time.sleep(0.2)
    wall = time.time() - t0
    stopped = pool.shutdown(drain=True, timeout=10)
    print()

    # ---- results table --------------------------------------------------------------
    rows = []
    total_work = 0.0
    intervals = []
    for sid in sorted(specs):
        row = db.get_submission(sid)
        label, problem, expected, expected_test = specs[sid]
        duration = (row["finished_at"] or 0) - (row["started_at"] or 0)
        total_work += duration
        intervals.append((row["started_at"], row["finished_at"]))
        rows.append((sid, label, problem, expected, row, expected_test, duration))

    header = ["id", "program", "problem", "expected", "verdict", "fail@", "tests", "time", "worker"]
    table = [header]
    for sid, label, problem, expected, row, _et, duration in rows:
        table.append([str(sid), label, problem, expected, row["status"],
                      "-" if row["failed_test"] is None else str(row["failed_test"]),
                      f"{row['tests_run']}/{row['tests_total'] or '-'}", f"{duration:.1f}s",
                      str(row["worker_id"])])
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]
    fmt = lambda r: "  ".join(c.ljust(widths[i]) for i, c in enumerate(r))  # noqa: E731
    print(fmt(header))
    print("  ".join("-" * w for w in widths))
    for r in table[1:]:
        print(fmt(r))
    print()

    # ---- checks ----------------------------------------------------------------------
    stats = pool.snapshot()["stats"]
    # Peak overlap of the [started_at, finished_at] intervals stored in the database.
    points = sorted([(a, 1) for a, _ in intervals] + [(b, -1) for _, b in intervals])
    running = peak_db = 0
    for _, delta in points:
        running += delta
        peak_db = max(peak_db, running)
    leftovers = temp_dirs() - temp_before
    try:
        import os as _os
        _os.waitpid(-1, _os.WNOHANG)
        zombies = True
    except ChildProcessError:
        zombies = False

    checks = [
        ("every submission reached a final status",
         all(r[4]["status"] in dbmod.FINAL_STATUSES for r in rows)),
        ("every verdict matches the expected verdict and failing test",
         all(r[4]["status"] == r[3] and r[4]["failed_test"] == r[5] for r in rows)),
        (f"each submission was judged exactly once ({stats['started']} started, "
         f"{stats['finished']} finished, {stats['duplicates_skipped']} duplicates skipped)",
         stats["started"] == stats["finished"] == len(specs) and stats["duplicates_skipped"] == 0
         and stats["judge_errors"] == 0),
        (f"workers really ran in parallel (peak {peak_db} at once by the database, "
         f"{peak_busy} by the live view; {args.workers} workers)",
         peak_db >= min(2, args.workers)),
        ("every test result is stored (rows in test_results = sum of tests_run)",
         sum(len(db.get_test_results(r[0])) for r in rows) == sum(r[4]["tests_run"] for r in rows)),
        ("pool shut down gracefully and the queue is empty",
         stopped and pool.snapshot()["queue"]["length"] == 0),
        ("no zombie or orphan child left, no temp directory left", not zombies and not leftovers),
    ]
    for text, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {text}")
    speedup = total_work / wall if wall else 0
    print(f"\nWall time {wall:.1f} s; the same work done one after another would take "
          f"{total_work:.1f} s (x{speedup:.1f} faster)")
    print(f"Events published: {broker.stats()['published']}, "
          f"events stored in SQLite: {sum(len(db.get_events(r[0])) for r in rows)}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
