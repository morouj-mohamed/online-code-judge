"""
judge/worker.py - a pool of worker threads that judge submissions in parallel.

                      submit()                    get()
    producers  ---->  [ BoundedQueue of ids ]  ---->  worker 1 ... worker N
    (anyone)             (FIFO, bounded)               |   claim() in SQLite
                                                       |   judge test by test
                                                       |   save each result at once
                                                       v   publish events

Lifecycle of one submission
    submit()  : INSERT row as Pending, publish the status, put its id in the queue.
    worker    : get id -> db.claim() (Pending -> Running, exactly one winner)
                -> judge_submission(...) -> db.finish(final verdict + failing test)

Threads or processes? Judging itself is done by CHILD PROCESSES (fork/exec); a
worker thread just waits for its child in wait4()/waitid(), which does not hold
the Python GIL. So plain threads give real parallelism here.
"""

import threading
import time
import traceback

import config
from judge import queue as jqueue
from judge import sandbox

IDLE, BUSY, STOPPED = "idle", "busy", "stopped"


class PoolClosed(Exception):
    """submit() was called after shutdown() started."""


class WorkerPool:
    def __init__(self, db, broker, n_workers=None, capacity=None, judge_fn=None):
        """
        db        a judge.db.Database
        broker    a judge.events.EventBroker
        judge_fn  the function that judges one submission. Defaults to the real
                  sandbox; the tests pass a fast fake one. Signature:
                  judge_fn(problem_slug, language, source, on_event=, on_test=, uid_slot=) -> result dict
        """
        self.db = db
        self.broker = broker
        self.n_workers = n_workers if n_workers is not None else config.WORKERS
        self.queue = jqueue.BoundedQueue(capacity if capacity is not None else config.QUEUE_CAPACITY)
        self.judge_fn = judge_fn or sandbox.judge_submission

        self._state_lock = threading.Lock()        # protects the records and counters below
        self._workers = {}                         # worker id -> record dict
        self._threads = []
        self._stats = {"submitted": 0, "started": 0, "finished": 0,
                       "duplicates_skipped": 0, "judge_errors": 0}
        self._outstanding = 0                      # submitted but not yet finished
        self._outstanding_cv = threading.Condition(self._state_lock)
        self._closing = False
        self._started_at = time.time()

    # ------------------------------------------------------------------ start
    def start(self, recover=False):
        """
        Start the worker threads. With recover=True, first re-queue work that a
        previous run left unfinished (Running rows are reset to Pending).
        """
        for worker_id in range(1, self.n_workers + 1):
            self._workers[worker_id] = {"id": worker_id, "state": IDLE, "submission_id": None,
                                        "child_pid": None, "uid_slot": worker_id - 1,
                                        "jobs_done": 0, "since": time.time()}
            thread = threading.Thread(target=self._worker_loop, args=(worker_id,),
                                      name=f"judge-worker-{worker_id}", daemon=True)
            self._threads.append(thread)
        recovered = self.db.recover_interrupted() if recover else []
        for thread in self._threads:
            thread.start()
        for sid in recovered:
            self.enqueue_existing(sid)
        return len(recovered)

    # ----------------------------------------------------------------- producers
    def submit(self, problem_slug, language, source, block=True, timeout=None):
        """
        Create a submission and queue it. Returns its id.

        Blocks while the queue is full (back-pressure: producers are slowed down
        instead of memory growing). With block=False it raises jqueue.Full
        immediately, which a web server can turn into "try again later".
        """
        if self._closing:
            raise PoolClosed("the pool is shutting down")
        sid = self.db.create_submission(problem_slug, language, source)
        self._publish_status(sid, config.PENDING)       # before queueing: the worker
        self._enqueue(sid, block, timeout)               # may start at once
        return sid

    def enqueue_existing(self, sid, block=True, timeout=None):
        """Queue a submission that already exists in the database (recovery, tests)."""
        self._enqueue(sid, block, timeout)

    def _enqueue(self, sid, block, timeout):
        with self._state_lock:
            self._outstanding += 1
            self._stats["submitted"] += 1
        try:
            self.queue.put(sid, block=block, timeout=timeout)
        except (jqueue.Full, jqueue.Closed) as exc:
            with self._state_lock:
                self._outstanding -= 1
                self._stats["submitted"] -= 1
                self._outstanding_cv.notify_all()
            if isinstance(exc, jqueue.Closed):
                raise PoolClosed("the pool is shutting down") from exc
            raise     # the row stays Pending in the database
        self._publish_queue()

    # ------------------------------------------------------------------- workers
    def _worker_loop(self, worker_id):
        self._set_worker(worker_id, IDLE)
        while True:
            try:
                sid = self.queue.get()              # sleeps here while the queue is empty
            except jqueue.Closed:
                break                               # closed and fully drained: leave
            self._publish_queue()
            try:
                self._process(worker_id, sid)
            except BaseException:                   # the worker thread must never die
                traceback.print_exc()
            finally:
                with self._state_lock:
                    self._outstanding -= 1
                    self._outstanding_cv.notify_all()
        self._set_worker(worker_id, STOPPED)

    def _process(self, worker_id, sid):
        # Exactly one worker can win this compare-and-swap, even if the same id
        # was queued twice. The loser simply skips it.
        if not self.db.claim(sid, worker_id):
            with self._state_lock:
                self._stats["duplicates_skipped"] += 1
            self.broker.publish("submission", {"status": "skipped", "worker_id": worker_id,
                                               "reason": "already claimed or finished"}, sid)
            return
        with self._state_lock:
            self._stats["started"] += 1
        submission = self.db.get_submission(sid)
        self._set_worker(worker_id, BUSY, submission_id=sid, child_pid=None)
        self._publish_status(sid, config.RUNNING, worker_id=worker_id)

        def on_event(event):
            # Track which child process this worker is waiting for right now.
            if event["step"] == "fork":
                self._set_worker(worker_id, BUSY, child_pid=event["pid"])
            elif event["step"] in ("reaped", "exec_failed"):
                self._set_worker(worker_id, BUSY, child_pid=None)
            self.db.add_event(sid, worker_id, "judge", event["step"], event["ts"], event["pid"],
                              {**event["details"]})
            self.broker.publish("judge", {"worker_id": worker_id, **event}, sid, ts=event["ts"])

        def on_test(outcome):
            self.db.add_test_result(sid, outcome)   # saved the moment the test ends

        try:
            result = self.judge_fn(submission["problem_slug"], submission["language"],
                                   submission["source"], on_event=on_event, on_test=on_test,
                                   uid_slot=worker_id - 1)
            ok = self.db.finish(sid, result["verdict"], failed_test=result["failed_test"],
                                message=result["message"], tests_total=result["total_tests"],
                                time_ms=result["time_ms"], memory_kb=result["memory_kb"])
            final = result["verdict"]
            failed_test = result["failed_test"]
            message = result["message"]
        except Exception as exc:
            # The judge itself crashed. Record it: the submission must not be
            # left "Running" forever.
            traceback.print_exc()
            with self._state_lock:
                self._stats["judge_errors"] += 1
            message = f"{type(exc).__name__}: {exc}"
            ok = self.db.finish(sid, config.JUDGE_ERROR, message=message)
            final, failed_test = config.JUDGE_ERROR, None
        finally:
            self._set_worker(worker_id, IDLE, submission_id=None, child_pid=None, job_done=True)

        if ok:
            with self._state_lock:
                self._stats["finished"] += 1
            self._publish_status(sid, final, worker_id=worker_id, failed_test=failed_test, message=message)

    # --------------------------------------------------------- state and events
    def _set_worker(self, worker_id, state, **changes):
        with self._state_lock:
            rec = self._workers[worker_id]
            changed = rec["state"] != state or any(rec.get(k) != v for k, v in changes.items()
                                                   if k != "job_done")
            if rec["state"] != state:
                rec["since"] = time.time()
            rec["state"] = state
            if changes.pop("job_done", False):
                rec["jobs_done"] += 1
            rec.update(changes)
            snapshot = dict(rec)
        # Publish AFTER releasing our lock: never call into another lock while holding one.
        if changed:
            self.broker.publish("worker", snapshot, snapshot["submission_id"])

    def _publish_status(self, sid, status, **extra):
        self.broker.publish("submission", {"status": status, **extra}, sid)
        # Stored too, so a browser that connects late can replay the whole history.
        self.db.add_event(sid, extra.get("worker_id"), "submission", "status_changed",
                          time.time(), None, {"status": status, **extra})

    def _publish_queue(self):
        self.broker.publish("queue", {"length": self.queue.qsize(), "capacity": self.queue.capacity})

    def snapshot(self):
        """A consistent picture of the pool, safe to call from any thread."""
        with self._state_lock:
            workers = [dict(self._workers[i]) for i in sorted(self._workers)]
            stats = dict(self._stats)
            outstanding = self._outstanding
        return {"time": time.time(), "closing": self._closing,
                "queue": {"length": self.queue.qsize(), "capacity": self.queue.capacity},
                "workers": workers, "stats": stats, "outstanding": outstanding}

    # ------------------------------------------------------------------ shutdown
    def wait_until_done(self, timeout=None):
        """Block until every submitted submission has finished. False on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._outstanding_cv:
            while self._outstanding > 0:
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    return False
                self._outstanding_cv.wait(left)
        return True

    def shutdown(self, drain=True, timeout=None):
        """
        Stop the pool gracefully.
          drain=True   (default) refuse new submissions, finish everything that is
                       queued or running, then stop the workers.
          drain=False  refuse new submissions, finish only what is RUNNING; submissions
                       still waiting stay Pending in the database, and the next start(recover=True)
                       picks them up again.
        Returns True if every worker thread has stopped.
        """
        self._closing = True
        if not drain:
            while True:
                try:
                    self.queue.get_nowait()
                except (jqueue.Empty, jqueue.Closed):
                    break
                with self._state_lock:
                    self._outstanding -= 1
                    self._stats["submitted"] -= 1
                    self._outstanding_cv.notify_all()
        self.queue.close()           # also wakes workers that sleep in get()
        deadline = None if timeout is None else time.monotonic() + timeout
        for thread in self._threads:
            thread.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
        alive = [t for t in self._threads if t.is_alive()]
        self.broker.publish("queue", {"length": self.queue.qsize(), "capacity": self.queue.capacity,
                                      "closed": True})
        return not alive
