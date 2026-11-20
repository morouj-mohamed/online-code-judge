#!/usr/bin/env python3
"""
tools/test_concurrency.py - prove that nothing is lost or duplicated under load.

It does not need the sandbox: the worker pool is given a fast FAKE judge, so
hundreds of submissions go through in seconds and the only thing under test is
the concurrency machinery (queue, workers, SQLite, events).

    python3 tools/test_concurrency.py

Exit status: 0 if every check passed, 1 otherwise.
"""

import collections
import contextlib
import io
import os
import random
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from judge import db as dbmod  # noqa: E402
from judge import events, worker  # noqa: E402
from judge import queue as jqueue  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"      [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    return ok


def run_threads(threads, timeout=60):
    """Start all threads and wait. Returns False if one is still alive (deadlock?)."""
    for t in threads:
        t.start()
    deadline = time.monotonic() + timeout
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    return not any(t.is_alive() for t in threads)


# ============================================================================
# 1. BoundedQueue behaviour
# ============================================================================
def test_queue_basics():
    print("[1/8] BoundedQueue: basic behaviour")
    q = jqueue.BoundedQueue(3)
    for i in range(3):
        q.put(i)
    try:
        q.put_nowait(99)
        full = False
    except jqueue.Full:
        full = True
    check("put on a full queue raises Full (non-blocking)", full)
    t0 = time.monotonic()
    try:
        q.put(99, timeout=0.2)
        timed = False
    except jqueue.Full:
        timed = 0.15 < time.monotonic() - t0 < 1.0
    check("put on a full queue times out after the timeout", timed)
    check("items come out in FIFO order", [q.get(), q.get(), q.get()] == [0, 1, 2])
    try:
        q.get_nowait()
        empty = False
    except jqueue.Empty:
        empty = True
    check("get on an empty queue raises Empty (non-blocking)", empty)

    # close() wakes a blocked consumer and a blocked producer.
    q = jqueue.BoundedQueue(1)
    got = []

    def blocked_consumer():
        try:
            q.get()
        except jqueue.Closed:
            got.append("consumer woken")

    c = threading.Thread(target=blocked_consumer)
    c.start()
    time.sleep(0.1)
    q.close()
    c.join(2)
    check("close() wakes a consumer blocked on an empty queue", got == ["consumer woken"])

    q = jqueue.BoundedQueue(1)
    q.put("x")

    def blocked_producer():
        try:
            q.put("y")
        except jqueue.Closed:
            got.append("producer woken")

    p = threading.Thread(target=blocked_producer)
    p.start()
    time.sleep(0.1)
    q.close()
    p.join(2)
    check("close() wakes a producer blocked on a full queue", "producer woken" in got)
    check("after close(): the queued item can still be drained, then Closed",
          q.get() == "x" and _raises(jqueue.Closed, q.get))
    check("after close(): put is refused", _raises(jqueue.Closed, q.put, "z"))


def _raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    return False


# ============================================================================
# 2. 100 producers, several consumers: nothing lost, nothing duplicated
# ============================================================================
def test_queue_many_producers(producers=100, per_producer=50, capacity=8, consumers=4):
    print(f"[2/8] BoundedQueue: {producers} producers x {per_producer} items, capacity {capacity}, "
          f"{consumers} consumers")
    q = jqueue.BoundedQueue(capacity)
    start = threading.Barrier(producers)
    consumed = []
    consumed_lock = threading.Lock()
    max_seen = [0]
    stop_sampling = threading.Event()

    def producer(pid):
        start.wait()                      # all 100 producers start at the same instant
        for n in range(per_producer):
            q.put((pid, n))

    def consumer():
        mine = []
        while True:
            try:
                mine.append(q.get())
            except jqueue.Closed:
                break
        with consumed_lock:
            consumed.extend(mine)

    def sampler():
        while not stop_sampling.is_set():
            max_seen[0] = max(max_seen[0], q.qsize())
            time.sleep(0.0002)            # do not hog the GIL (the interpreter lock)

    sampler_thread = threading.Thread(target=sampler)
    sampler_thread.start()
    cons = [threading.Thread(target=consumer) for _ in range(consumers)]
    for c in cons:
        c.start()
    prods = [threading.Thread(target=producer, args=(i,)) for i in range(producers)]
    finished = run_threads(prods)
    q.close()                             # consumers drain what is left, then stop
    for c in cons:
        c.join(30)
    stop_sampling.set()
    sampler_thread.join()

    expected = {(p, n) for p in range(producers) for n in range(per_producer)}
    counts = collections.Counter(consumed)
    duplicated = sum(1 for v in counts.values() if v > 1)
    lost = len(expected - set(counts))
    check("no deadlock: every producer and consumer finished",
          finished and not any(c.is_alive() for c in cons))
    check(f"produced {len(expected)}, consumed {len(consumed)}: lost {lost}, duplicated {duplicated}",
          lost == 0 and duplicated == 0 and len(consumed) == len(expected))
    check(f"the queue never held more than its capacity (max seen {max_seen[0]}, capacity {capacity})",
          max_seen[0] <= capacity)

    # FIFO per producer: with ONE consumer, each producer's items must come out in order.
    q = jqueue.BoundedQueue(capacity)
    start = threading.Barrier(producers)
    order = []

    def single_consumer():
        while True:
            try:
                order.append(q.get())
            except jqueue.Closed:
                return

    sc = threading.Thread(target=single_consumer)
    sc.start()
    prods = [threading.Thread(target=producer, args=(i,)) for i in range(producers)]
    run_threads(prods)
    q.close()
    sc.join(30)
    last = {}
    in_order = True
    for pid, n in order:
        if n != last.get(pid, -1) + 1:
            in_order = False
        last[pid] = n
    check("items of the same producer come out in the order they were put (FIFO)",
          in_order and len(order) == producers * per_producer)


# ============================================================================
# 3. Event broker
# ============================================================================
def test_broker():
    print("[3/8] EventBroker: ordering, slow subscribers, replay")
    broker = events.EventBroker(history=100000)
    fast = broker.subscribe(maxlen=100000)
    slow = broker.subscribe(maxlen=10)           # a "browser tab" that never reads
    only_even = broker.subscribe(submission_id=2)
    publishers, per = 20, 200
    start = threading.Barrier(publishers)

    def publisher(i):
        start.wait()
        for n in range(per):
            broker.publish("judge", {"p": i, "n": n}, submission_id=n % 3)

    t0 = time.monotonic()
    done = run_threads([threading.Thread(target=publisher, args=(i,)) for i in range(publishers)])
    elapsed = time.monotonic() - t0
    got = fast.drain()
    seqs = [e["seq"] for e in got]
    check(f"publishers never blocked, even with a stuck subscriber ({publishers * per} events in {elapsed:.2f}s)",
          done and elapsed < 10)
    check("a fast subscriber saw every event exactly once, in sequence order",
          seqs == list(range(1, publishers * per + 1)))
    check(f"the slow subscriber kept only its newest 10 and counted {slow.dropped} dropped",
          len(slow.drain()) == 10 and slow.dropped == publishers * per - 10)
    check("a subscriber filtered on one submission only got that submission's events",
          all(e["submission_id"] == 2 for e in only_even.drain()))
    late = broker.subscribe(since_seq=3900)
    replayed = [e["seq"] for e in late.drain()]
    check("subscribe(since_seq=...) replays the missed events with no gap",
          replayed == list(range(3901, publishers * per + 1)))
    broker.close()


# ============================================================================
# 4. Database compare-and-swap
# ============================================================================
def test_db_claim(tmp):
    print("[4/8] SQLite: claim() has exactly one winner")
    db = dbmod.Database(os.path.join(tmp, "claim.sqlite3"))
    sid = db.create_submission("sum-two", "cpp", "x")
    wins = []
    start = threading.Barrier(50)

    def contender(i):
        start.wait()
        if db.claim(sid, i):
            wins.append(i)

    run_threads([threading.Thread(target=contender, args=(i,)) for i in range(50)])
    check("50 threads claim the same Pending submission: exactly one wins", len(wins) == 1)
    check("finish() works once and refuses to overwrite a finished submission",
          db.finish(sid, config.ACCEPTED) and not db.finish(sid, config.WRONG_ANSWER)
          and db.get_submission(sid)["status"] == config.ACCEPTED)
    check("a Pending submission cannot jump straight to a verdict",
          not db.finish(db.create_submission("a", "c", "y"), config.ACCEPTED))
    db.close()


# ============================================================================
# 5. Full pipeline with a fake judge: 100 producers
# ============================================================================
class FakeJudge:
    """Stands in for the sandbox. The 'source' says what to do: '<n>:ok', '<n>:wa' or '<n>:boom'."""

    def __init__(self):
        self.calls = collections.Counter()
        self.lock = threading.Lock()
        self.threads = set()

    def __call__(self, slug, language, source, on_event=None, on_test=None, uid_slot=0):
        with self.lock:
            self.calls[source] += 1
            self.threads.add(threading.current_thread().name)
        kind = source.split(":")[1]
        time.sleep(random.random() * 0.004)
        on_event({"ts": time.time(), "pid": 1000 + uid_slot, "step": "fork", "details": {}})
        if kind == "boom":
            raise RuntimeError("the judge exploded")
        tests = 3 if kind == "ok" else 2
        for i in range(1, tests + 1):
            verdict = config.ACCEPTED if (kind == "ok" or i < 2) else config.WRONG_ANSWER
            on_test({"test": i, "verdict": verdict, "time_ms": 1, "wall_ms": 1, "memory_kb": 100,
                     "memory_source": "fake", "message": "", "exit_code": 0, "signal": None})
        on_event({"ts": time.time(), "pid": 1000 + uid_slot, "step": "reaped", "details": {}})
        ok = kind == "ok"
        return {"verdict": config.ACCEPTED if ok else config.WRONG_ANSWER, "failed_test": None if ok else 2,
                "message": "fake", "total_tests": 3, "time_ms": 1, "memory_kb": 100}


def test_pipeline(tmp, producers=100, per_producer=5, workers=3, capacity=20):
    total = producers * per_producer
    print(f"[5/8] Pipeline: {producers} producers x {per_producer} submissions = {total}, "
          f"{workers} workers, queue capacity {capacity}")
    db = dbmod.Database(os.path.join(tmp, "pipeline.sqlite3"))
    broker = events.EventBroker()
    judge = FakeJudge()
    pool = worker.WorkerPool(db, broker, n_workers=workers, capacity=capacity, judge_fn=judge)
    feed = broker.subscribe(types={"submission"}, maxlen=100000)
    max_len = [0]
    stop = threading.Event()

    def sampler():
        while not stop.is_set():
            max_len[0] = max(max_len[0], pool.queue.qsize())
            time.sleep(0.0002)            # do not hog the GIL (the interpreter lock)

    sampler_thread = threading.Thread(target=sampler)
    sampler_thread.start()
    pool.start()

    ids, ids_lock = [], threading.Lock()
    expected_status = {}
    start = threading.Barrier(producers)

    def producer(p):
        start.wait()
        for n in range(per_producer):
            kind = ("ok", "wa", "ok", "boom", "ok")[(p + n) % 5]
            source = f"{p}-{n}:{kind}"
            sid = pool.submit("sum-two", "cpp", source)
            with ids_lock:
                ids.append(sid)
                expected_status[sid] = {"ok": config.ACCEPTED, "wa": config.WRONG_ANSWER,
                                        "boom": config.JUDGE_ERROR}[kind]

    # The fake judge crashes on purpose for 1 in 5 submissions; the pool prints a
    # traceback for each crash, so hide stderr here to keep the output readable.
    with contextlib.redirect_stderr(io.StringIO()):
        finished_producing = run_threads([threading.Thread(target=producer, args=(i,))
                                          for i in range(producers)])
        all_done = pool.wait_until_done(timeout=120)
    stop.set()
    sampler_thread.join()
    stopped = pool.shutdown(drain=True, timeout=10)

    rows = db.list_submissions(limit=total + 10)
    by_id = {r["id"]: r for r in rows}
    check("no deadlock: all producers finished, all work finished, pool stopped",
          finished_producing and all_done and stopped)
    check(f"ids returned to producers are unique and contiguous (1..{total})",
          len(ids) == total and sorted(ids) == list(range(1, total + 1)))
    check(f"every submission is in the database exactly once: {len(rows)} rows, none lost",
          len(rows) == total and set(by_id) == set(ids))
    check("every submission reached a final status (none stuck in Pending or Running)",
          all(r["status"] in dbmod.FINAL_STATUSES for r in rows),
          str(db.count_by_status()))
    check("every submission got the right verdict and failing test",
          all(by_id[s]["status"] == expected_status[s]
              and by_id[s]["failed_test"] == (2 if expected_status[s] == config.WRONG_ANSWER else None)
              for s in ids))
    twice = [s for s, n in judge.calls.items() if n != 1]
    check(f"every submission was judged exactly once ({len(judge.calls)} different sources, "
          f"{sum(judge.calls.values())} judge calls, {len(twice)} judged more or less than once)",
          not twice and len(judge.calls) == total)
    stats = pool.snapshot()["stats"]
    expected_tests = sum(0 if v == config.JUDGE_ERROR else (3 if v == config.ACCEPTED else 2)
                         for v in expected_status.values())
    stored_tests = sum(len(db.get_test_results(s)) for s in ids)
    check(f"per-test results were stored as they finished ({stored_tests} rows, expected {expected_tests})",
          stored_tests == expected_tests)
    check(f"a judge that crashes is recorded as {config.JUDGE_ERROR} and the worker survives "
          f"({stats['judge_errors']} crashes)",
          stats["judge_errors"] == sum(1 for v in expected_status.values() if v == config.JUDGE_ERROR))
    final_events = collections.Counter(e["submission_id"] for e in feed.drain()
                                       if e["data"]["status"] in dbmod.FINAL_STATUSES)
    check("exactly one final-status event was published per submission",
          set(final_events.values()) == {1} and len(final_events) == total)
    check(f"back-pressure: the queue never exceeded its capacity (max {max_len[0]}, capacity {capacity})",
          max_len[0] <= capacity)
    check(f"all {workers} worker threads took part ({len(judge.threads)} used)", len(judge.threads) == workers)
    db.close()


# ============================================================================
# 6. The same id queued twice
# ============================================================================
def test_duplicates(tmp):
    print("[6/8] Idempotence: the same submission id queued 10 times")
    db = dbmod.Database(os.path.join(tmp, "dup.sqlite3"))
    judge = FakeJudge()
    pool = worker.WorkerPool(db, events.EventBroker(), n_workers=3, judge_fn=judge)
    pool.start()
    sid = db.create_submission("sum-two", "cpp", "dup:ok")
    for _ in range(10):
        pool.enqueue_existing(sid)
    pool.wait_until_done(30)
    stats = pool.snapshot()["stats"]
    pool.shutdown()
    check(f"judged once, {stats['duplicates_skipped']} duplicates skipped",
          judge.calls["dup:ok"] == 1 and stats["duplicates_skipped"] == 9
          and db.get_submission(sid)["status"] == config.ACCEPTED)
    db.close()


# ============================================================================
# 7. Graceful shutdown
# ============================================================================
class SlowJudge(FakeJudge):
    def __call__(self, *args, **kwargs):
        time.sleep(0.03)
        return super().__call__(*args, **kwargs)


def test_shutdown(tmp):
    print("[7/8] Graceful shutdown")
    db = dbmod.Database(os.path.join(tmp, "shutdown.sqlite3"))
    pool = worker.WorkerPool(db, events.EventBroker(), n_workers=3, judge_fn=SlowJudge())
    pool.start()
    ids = [pool.submit("sum-two", "cpp", f"{i}:ok") for i in range(60)]
    stopped = pool.shutdown(drain=True, timeout=60)           # called while ~57 are still waiting
    check("drain=True: all 60 queued submissions were finished before the workers stopped",
          stopped and all(db.get_submission(i)["status"] == config.ACCEPTED for i in ids))
    check("after shutdown, submit() is refused", _raises(worker.PoolClosed, pool.submit, "sum-two", "cpp", "x:ok"))

    pool = worker.WorkerPool(db, events.EventBroker(), n_workers=2, judge_fn=SlowJudge())
    pool.start()
    ids = [pool.submit("sum-two", "cpp", f"b{i}:ok") for i in range(40)]
    pool.shutdown(drain=False, timeout=60)                    # finish only what is running
    counts = db.count_by_status()
    left = [i for i in ids if db.get_submission(i)["status"] == config.PENDING]
    check(f"drain=False: running work finished, waiting work stayed Pending ({counts})",
          0 < len(left) < 40 and counts.get(config.RUNNING, 0) == 0)

    pool = worker.WorkerPool(db, events.EventBroker(), n_workers=3, judge_fn=FakeJudge())
    requeued = pool.start(recover=True)                       # like a restart after the shutdown
    pool.wait_until_done(60)
    pool.shutdown()
    check(f"restart with recover=True re-queued the {requeued} waiting submissions and finished them all",
          requeued == len(left) and all(db.get_submission(i)["status"] == config.ACCEPTED for i in ids))

    # A crash leaves a row in Running: recovery must put it back.
    sid = db.create_submission("sum-two", "cpp", "crash:ok")
    db.claim(sid, 1)
    recovered = db.recover_interrupted()
    check("a submission left Running by a crash goes back to Pending on recovery",
          sid in recovered and db.get_submission(sid)["status"] == config.PENDING)
    db.close()


# ============================================================================
# 8. Worker snapshot
# ============================================================================
def test_snapshot(tmp):
    print("[8/8] Worker snapshot")
    db = dbmod.Database(os.path.join(tmp, "snap.sqlite3"))
    gate = threading.Event()

    def blocking_judge(slug, language, source, on_event=None, on_test=None, uid_slot=0):
        on_event({"ts": time.time(), "pid": 4242, "step": "fork", "details": {}})
        gate.wait(10)
        on_event({"ts": time.time(), "pid": 4242, "step": "reaped", "details": {}})
        return {"verdict": config.ACCEPTED, "failed_test": None, "message": "", "total_tests": 1,
                "time_ms": 1, "memory_kb": 1}

    pool = worker.WorkerPool(db, events.EventBroker(), n_workers=2, capacity=10, judge_fn=blocking_judge)
    pool.start()
    ids = [pool.submit("sum-two", "cpp", f"s{i}:ok") for i in range(5)]
    time.sleep(0.3)
    snap = pool.snapshot()
    busy = [w for w in snap["workers"] if w["state"] == worker.BUSY]
    check("snapshot: 2 workers busy, each with its submission id and child pid, 3 submissions waiting",
          len(busy) == 2 and all(w["submission_id"] in ids and w["child_pid"] == 4242 for w in busy)
          and snap["queue"]["length"] == 3)
    check("snapshot: every worker has its own uid slot",
          sorted(w["uid_slot"] for w in snap["workers"]) == [0, 1])
    gate.set()
    pool.wait_until_done(30)
    snap = pool.snapshot()
    check("snapshot after the work: all idle, child pid cleared, 5 jobs done in total",
          all(w["state"] == worker.IDLE and w["child_pid"] is None for w in snap["workers"])
          and sum(w["jobs_done"] for w in snap["workers"]) == 5)
    pool.shutdown()
    db.close()


def main():
    tmp = tempfile.mkdtemp(prefix="concurrency-test-")
    t0 = time.time()
    test_queue_basics()
    test_queue_many_producers()
    test_broker()
    test_db_claim(tmp)
    test_pipeline(tmp)
    test_duplicates(tmp)
    test_shutdown(tmp)
    test_snapshot(tmp)
    passed = sum(results)
    print(f"\n{passed}/{len(results)} checks passed in {time.time() - t0:.1f} s")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
