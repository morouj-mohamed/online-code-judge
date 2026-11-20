"""
judge/db.py - the SQLite layer: submissions, per-test results and events.

Three tables
    submissions   one row per submission, its status and final verdict
    test_results  one row per finished test (written the moment the test ends)
    events        the lifecycle events of a submission, kept so that a browser
                  that connects late (or reconnects) can replay what it missed

Status model (the only legal moves):

        Pending --claim()--> Running --finish()--> <final verdict>

    final verdict = Accepted | Wrong Answer | Time Limit Exceeded |
                    Memory Limit Exceeded | Runtime Error | Compilation Error
                    (or "Judge Error" if the judge itself crashed)

    The failing test number is stored in submissions.failed_test.
    recover_interrupted() is the only way back: Running -> Pending, used after a
    crash so that unfinished work is not lost.

Concurrency
    One sqlite3 connection is shared by all threads, and EVERY access (reads
    and writes) goes through one lock. A connection must not be used by two
    threads at the same moment, and with a single writer there is no
    "database is locked" error to handle. Multi-statement changes run inside
    one transaction (BEGIN IMMEDIATE ... COMMIT), so they happen all or nothing.
"""

import contextlib
import json
import sqlite3
import threading
import time

import config

FINAL_STATUSES = frozenset({
    config.ACCEPTED, config.WRONG_ANSWER, config.TIME_LIMIT_EXCEEDED,
    config.MEMORY_LIMIT_EXCEEDED, config.RUNTIME_ERROR, config.COMPILATION_ERROR,
    config.JUDGE_ERROR,
})

# status -> the statuses it may move to
TRANSITIONS = {
    config.PENDING: frozenset({config.RUNNING}),
    config.RUNNING: FINAL_STATUSES,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_slug TEXT    NOT NULL,
    language     TEXT    NOT NULL,
    source       TEXT    NOT NULL,
    status       TEXT    NOT NULL,           -- Pending, Running or a final verdict
    failed_test  INTEGER,                    -- number of the first failing test
    message      TEXT    NOT NULL DEFAULT '',
    tests_total  INTEGER,
    tests_run    INTEGER NOT NULL DEFAULT 0, -- grows as each test finishes
    time_ms      INTEGER,                    -- slowest test
    memory_kb    INTEGER,                    -- biggest test
    worker_id    INTEGER,
    session_id   TEXT,                       -- anonymous browser session that submitted it
    created_at   REAL    NOT NULL,
    started_at   REAL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_submissions_status ON submissions(status);

CREATE TABLE IF NOT EXISTS test_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    test_index    INTEGER NOT NULL,
    verdict       TEXT    NOT NULL,
    time_ms       INTEGER,
    wall_ms       INTEGER,
    memory_kb     INTEGER,
    memory_source TEXT,
    message       TEXT,
    exit_code     INTEGER,
    signal        TEXT,
    UNIQUE (submission_id, test_index)       -- a test can be recorded only once
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id INTEGER NOT NULL REFERENCES submissions(id) ON DELETE CASCADE,
    worker_id     INTEGER,
    source        TEXT    NOT NULL,          -- "judge" (sandbox) or "submission" (status change)
    ts            REAL    NOT NULL,
    pid           INTEGER,
    step          TEXT    NOT NULL,
    details       TEXT    NOT NULL           -- JSON
);
CREATE INDEX IF NOT EXISTS idx_events_submission ON events(submission_id, id);
"""


class Database:
    def __init__(self, path=None):
        self.path = path or config.DB_PATH
        self._lock = threading.RLock()
        # check_same_thread=False: we promise to serialize access ourselves.
        # isolation_level=None: autocommit, so transactions are explicit (see _tx).
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            # WAL = write-ahead log: readers in OTHER processes (the web server in a
            # later phase) are not blocked by our writes.
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")
            self._conn.execute("PRAGMA busy_timeout = 5000")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self):
        """
        Bring a database created by an older phase up to date. SQLite cannot
        add a column with "IF NOT EXISTS", so we look at the table first.
        """
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(submissions)")}
        if "session_id" not in columns:
            self._conn.execute("ALTER TABLE submissions ADD COLUMN session_id TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submissions_session ON submissions(session_id, id)")

    # ------------------------------------------------------------------ plumbing
    @contextlib.contextmanager
    def _tx(self):
        """Hold the lock and run everything inside it as one transaction."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _one(self, sql, params=()):
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql, params=()):
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- submissions
    def create_submission(self, problem_slug, language, source, session_id=None):
        """Insert a new submission in the Pending state and return its id."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO submissions (problem_slug, language, source, status, session_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (problem_slug, language, source, config.PENDING, session_id, time.time()))
            return cur.lastrowid

    def discard_pending(self, submission_id):
        """
        Delete a submission that never got into the queue (the queue was full).
        Only a Pending row can be discarded; its events go too (ON DELETE CASCADE).
        Returns True if a row was deleted.
        """
        with self._tx() as c:
            cur = c.execute("DELETE FROM submissions WHERE id = ? AND status = ?",
                            (submission_id, config.PENDING))
            return cur.rowcount == 1

    def claim(self, submission_id, worker_id):
        """
        Pending -> Running, for exactly one caller.

        This is a compare-and-swap: the UPDATE only matches a row that is STILL
        Pending. If two workers try at the same moment (or the same id was
        queued twice by mistake) the database lets exactly one of them win.
        Returns True for the winner and False for everybody else.
        """
        with self._tx() as c:
            cur = c.execute(
                "UPDATE submissions SET status = ?, worker_id = ?, started_at = ? "
                "WHERE id = ? AND status = ?",
                (config.RUNNING, worker_id, time.time(), submission_id, config.PENDING))
            return cur.rowcount == 1

    def add_test_result(self, submission_id, outcome):
        """Save one finished test and bump the submission's progress counter."""
        with self._tx() as c:
            c.execute(
                "INSERT INTO test_results (submission_id, test_index, verdict, time_ms, wall_ms, "
                "memory_kb, memory_source, message, exit_code, signal) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (submission_id, outcome["test"], outcome["verdict"], outcome["time_ms"],
                 outcome.get("wall_ms"), outcome["memory_kb"], outcome.get("memory_source"),
                 outcome.get("message"), outcome.get("exit_code"), outcome.get("signal")))
            c.execute("UPDATE submissions SET tests_run = tests_run + 1 WHERE id = ?", (submission_id,))

    def finish(self, submission_id, status, failed_test=None, message="", tests_total=None,
               time_ms=None, memory_kb=None):
        """
        Running -> final verdict. Returns False if the submission was not Running
        (so a finished submission can never be overwritten).
        """
        if status not in TRANSITIONS[config.RUNNING]:
            raise ValueError(f"{status!r} is not a final status")
        with self._tx() as c:
            cur = c.execute(
                "UPDATE submissions SET status = ?, failed_test = ?, message = ?, tests_total = ?, "
                "time_ms = ?, memory_kb = ?, finished_at = ? WHERE id = ? AND status = ?",
                (status, failed_test, message, tests_total, time_ms, memory_kb, time.time(),
                 submission_id, config.RUNNING))
            return cur.rowcount == 1

    def get_submission(self, submission_id):
        return self._one("SELECT * FROM submissions WHERE id = ?", (submission_id,))

    def list_submissions(self, status=None, limit=100, session_id=None):
        """Newest first, optionally only one status and/or one browser session."""
        where, params = [], []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if session_id is not None:
            where.append("session_id = ?")
            params.append(session_id)
        sql = "SELECT * FROM submissions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self._all(sql + " ORDER BY id DESC LIMIT ?", (*params, limit))

    def get_session_of(self, submission_id):
        """The session id that owns a submission (None if unknown or no owner)."""
        row = self._one("SELECT session_id FROM submissions WHERE id = ?", (submission_id,))
        return row["session_id"] if row else None

    def count_by_status(self):
        rows = self._all("SELECT status, COUNT(*) AS n FROM submissions GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def recover_interrupted(self):
        """
        After a crash: put every Running submission back to Pending (its worker
        died with the process) and return the ids of all Pending submissions, oldest
        first, so the caller can queue them again. Their old test results and
        events are deleted so the re-run starts clean.
        """
        with self._tx() as c:
            running = [r["id"] for r in c.execute(
                "SELECT id FROM submissions WHERE status = ?", (config.RUNNING,)).fetchall()]
            for sid in running:
                c.execute("DELETE FROM test_results WHERE submission_id = ?", (sid,))
                c.execute("DELETE FROM events WHERE submission_id = ?", (sid,))
                c.execute("UPDATE submissions SET status = ?, worker_id = NULL, started_at = NULL, "
                          "tests_run = 0 WHERE id = ?", (config.PENDING, sid))
            return [r["id"] for r in c.execute(
                "SELECT id FROM submissions WHERE status = ? ORDER BY id", (config.PENDING,)).fetchall()]

    # -------------------------------------------------------------- tests, events
    def get_test_results(self, submission_id):
        return self._all("SELECT * FROM test_results WHERE submission_id = ? ORDER BY test_index",
                         (submission_id,))

    def add_event(self, submission_id, worker_id, source, step, ts, pid, details):
        with self._tx() as c:
            c.execute(
                "INSERT INTO events (submission_id, worker_id, source, ts, pid, step, details) "
                "VALUES (?,?,?,?,?,?,?)",
                (submission_id, worker_id, source, ts, pid, step, json.dumps(details)))

    def get_events(self, submission_id, after_id=0):
        """Events of one submission with id > after_id (id is the replay cursor)."""
        rows = self._all("SELECT * FROM events WHERE submission_id = ? AND id > ? ORDER BY id",
                         (submission_id, after_id))
        for row in rows:
            row["details"] = json.loads(row["details"])
        return rows
