"""
judge/db.py - the SQLite layer: submissions, per-test results and events.

Tables
    submissions   one row per submission, its status and final verdict
    test_results  one row per finished test (written the moment the test ends)
    events        the lifecycle events of a submission, kept so that a browser
                  that connects late (or reconnects) can replay what it missed
    users         accounts (Phase 4b): username, scrypt password hash, admin flag
    auth_sessions logged-in browsers: only the SHA-256 of the login token is
                  stored, so a copy of the database cannot be used to log in
    contests      timed problem sets (start/end); contest_problems lists their
                  problems; a submission made inside a contest has contest_id

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

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL    NOT NULL,
    last_login_at REAL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash   TEXT    PRIMARY KEY,          -- sha256(token); the token itself lives only in the cookie
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   REAL    NOT NULL,
    expires_at   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS contests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    slug         TEXT    NOT NULL UNIQUE,
    title        TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    starts_at    REAL    NOT NULL,
    ends_at      REAL    NOT NULL,
    hidden       INTEGER NOT NULL DEFAULT 1,
    created_at   REAL    NOT NULL
);

-- Exam mode: every time a contestant leaves the exam page (tab switch, window
-- blur, leaving fullscreen) or pastes a large text, the browser reports it here.
CREATE TABLE IF NOT EXISTS contest_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contest_id   INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL,
    detail       TEXT    NOT NULL DEFAULT '',
    ts           REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contest_events ON contest_events(contest_id, user_id, id);

-- Exam locks: a student who left the exam too often is locked out (no more
-- submissions) until the teacher unlocks them. released_at = NULL: still locked.
CREATE TABLE IF NOT EXISTS contest_locks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contest_id   INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    locked_at    REAL    NOT NULL,
    released_at  REAL,
    released_by  TEXT
);
CREATE INDEX IF NOT EXISTS idx_contest_locks ON contest_locks(contest_id, user_id);

CREATE TABLE IF NOT EXISTS contest_problems (
    contest_id   INTEGER NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
    problem_slug TEXT    NOT NULL,
    position     INTEGER NOT NULL,
    PRIMARY KEY (contest_id, problem_slug)
);
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
        if "user_id" not in columns:
            self._conn.execute("ALTER TABLE submissions ADD COLUMN user_id INTEGER REFERENCES users(id)")
        if "contest_id" not in columns:
            self._conn.execute("ALTER TABLE submissions ADD COLUMN contest_id INTEGER REFERENCES contests(id)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submissions_contest ON submissions(contest_id, id)")
        contest_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(contests)")}
        if "exam_mode" not in contest_columns:
            self._conn.execute("ALTER TABLE contests ADD COLUMN exam_mode INTEGER NOT NULL DEFAULT 0")
        if "max_exits" not in contest_columns:
            # 0 = only record; N = lock the exam after N exits
            self._conn.execute("ALTER TABLE contests ADD COLUMN max_exits INTEGER NOT NULL DEFAULT 0")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submissions_session ON submissions(session_id, id)")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id, id)")

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
    def create_submission(self, problem_slug, language, source, session_id=None, user_id=None,
                          contest_id=None):
        """Insert a new submission in the Pending state and return its id."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO submissions (problem_slug, language, source, status, session_id, user_id, "
                "contest_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (problem_slug, language, source, config.PENDING, session_id, user_id, contest_id,
                 time.time()))
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

    def list_submissions(self, status=None, limit=100, session_id=None, user_id=None,
                         anonymous_only=False, with_username=False):
        """
        Newest first. Filters (all optional):
          status          only this status
          session_id      only this browser session
          user_id         only this account
          anonymous_only  only submissions that belong to no account
        """
        where, params = [], []
        if status is not None:
            where.append("s.status = ?")
            params.append(status)
        if session_id is not None:
            where.append("s.session_id = ?")
            params.append(session_id)
        if user_id is not None:
            where.append("s.user_id = ?")
            params.append(user_id)
        if anonymous_only:
            where.append("s.user_id IS NULL")
        sql = ("SELECT s.*, u.username AS username FROM submissions s "
               "LEFT JOIN users u ON u.id = s.user_id") if with_username else "SELECT s.* FROM submissions s"
        if where:
            sql += " WHERE " + " AND ".join(where)
        return self._all(sql + " ORDER BY s.id DESC LIMIT ?", (*params, limit))

    def get_owner(self, submission_id):
        """(session_id, user_id) of a submission, or None if it does not exist."""
        row = self._one("SELECT session_id, user_id FROM submissions WHERE id = ?", (submission_id,))
        return (row["session_id"], row["user_id"]) if row else None

    def get_session_of(self, submission_id):
        """The session id that owns a submission (None if unknown or no owner)."""
        owner = self.get_owner(submission_id)
        return owner[0] if owner else None

    def claim_submissions(self, session_id, user_id):
        """
        Give the anonymous submissions of a browser session to an account
        (called right after that browser logs in or signs up). Returns how many.
        """
        with self._tx() as c:
            cur = c.execute("UPDATE submissions SET user_id = ? WHERE session_id = ? AND user_id IS NULL",
                            (user_id, session_id))
            return cur.rowcount

    # --------------------------------------------------------------------- users
    def create_user(self, username, password_hash, is_admin=False):
        """Insert an account. Raises sqlite3.IntegrityError if the name is taken."""
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, 1 if is_admin else 0, time.time()))
            return cur.lastrowid

    def get_user(self, user_id):
        return self._one("SELECT * FROM users WHERE id = ?", (user_id,))

    def get_user_by_name(self, username):
        return self._one("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,))

    def list_users(self):
        return self._all("SELECT u.id, u.username, u.is_admin, u.created_at, u.last_login_at, "
                         "(SELECT COUNT(*) FROM submissions s WHERE s.user_id = u.id) AS submissions "
                         "FROM users u ORDER BY u.id")

    def set_admin(self, user_id, is_admin):
        with self._tx() as c:
            return c.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                             (1 if is_admin else 0, user_id)).rowcount == 1

    def set_password(self, user_id, password_hash):
        """Change a password and log that account out everywhere."""
        with self._tx() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            c.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))

    def touch_login(self, user_id):
        with self._tx() as c:
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (time.time(), user_id))

    def submissions_of_problem(self, problem_slug, contest_id=None):
        """Every finished submission of one problem made by an account (for the similarity check)."""
        sql = ("SELECT s.id, s.user_id, u.username, s.language, s.source, s.status, s.created_at "
               "FROM submissions s JOIN users u ON u.id = s.user_id "
               "WHERE s.problem_slug = ? AND s.status NOT IN (?, ?)")
        params = [problem_slug, config.PENDING, config.RUNNING]
        if contest_id is not None:
            sql += " AND s.contest_id = ?"
            params.append(contest_id)
        return self._all(sql + " ORDER BY s.id", params)

    # ------------------------------------------------------------------ contests
    def create_contest(self, slug, title, description, starts_at, ends_at):
        with self._tx() as c:
            return c.execute(
                "INSERT INTO contests (slug, title, description, starts_at, ends_at, hidden, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (slug, title, description, starts_at, ends_at, time.time())).lastrowid

    def update_contest(self, contest_id, title, description, starts_at, ends_at, exam_mode=False, max_exits=0):
        with self._tx() as c:
            c.execute("UPDATE contests SET title = ?, description = ?, starts_at = ?, ends_at = ?, exam_mode = ?, "
                      "max_exits = ? WHERE id = ?",
                      (title, description, starts_at, ends_at, 1 if exam_mode else 0, max_exits, contest_id))

    # --- exam locks
    EXIT_KINDS = ("tab_hidden", "window_blur", "fullscreen_exit")

    def exam_state(self, contest_id, user_id):
        """{"locked", "exits"}: exits counted since the last unlock."""
        lock = self._one("SELECT * FROM contest_locks WHERE contest_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
                         (contest_id, user_id))
        since = lock["released_at"] if lock and lock["released_at"] else 0
        row = self._one(f"SELECT COUNT(*) AS n FROM contest_events WHERE contest_id = ? AND user_id = ? AND ts > ? "
                        f"AND kind IN ({','.join('?' * len(self.EXIT_KINDS))})",
                        (contest_id, user_id, since, *self.EXIT_KINDS))
        return {"locked": bool(lock and lock["released_at"] is None), "exits": row["n"] if row else 0}

    def lock_exam(self, contest_id, user_id):
        with self._tx() as c:
            open_lock = c.execute("SELECT id FROM contest_locks WHERE contest_id = ? AND user_id = ? "
                                  "AND released_at IS NULL", (contest_id, user_id)).fetchone()
            if open_lock is None:
                c.execute("INSERT INTO contest_locks (contest_id, user_id, locked_at) VALUES (?, ?, ?)",
                          (contest_id, user_id, time.time()))

    def unlock_exam(self, contest_id, user_id, by):
        with self._tx() as c:
            return c.execute("UPDATE contest_locks SET released_at = ?, released_by = ? WHERE contest_id = ? "
                             "AND user_id = ? AND released_at IS NULL",
                             (time.time(), by, contest_id, user_id)).rowcount

    def add_contest_event(self, contest_id, user_id, kind, detail):
        with self._tx() as c:
            c.execute("INSERT INTO contest_events (contest_id, user_id, kind, detail, ts) VALUES (?, ?, ?, ?, ?)",
                      (contest_id, user_id, kind, detail, time.time()))

    def contest_event_counts(self, contest_id):
        """{user_id: {"total": n, kind: n, ...}} for the standings."""
        counts = {}
        for row in self._all("SELECT user_id, kind, COUNT(*) AS n FROM contest_events WHERE contest_id = ? "
                             "GROUP BY user_id, kind", (contest_id,)):
            entry = counts.setdefault(row["user_id"], {"total": 0})
            entry[row["kind"]] = row["n"]
            entry["total"] += row["n"]
        return counts

    def contest_events_of_user(self, user_id, contest_id=None):
        sql = ("SELECT e.kind, e.detail, e.ts, c.slug AS contest, c.title AS contest_title "
               "FROM contest_events e JOIN contests c ON c.id = e.contest_id WHERE e.user_id = ?")
        params = [user_id]
        if contest_id is not None:
            sql += " AND e.contest_id = ?"
            params.append(contest_id)
        return self._all(sql + " ORDER BY e.id DESC LIMIT 500", params)

    def count_recent_contest_events(self, contest_id, user_id, since):
        row = self._one("SELECT COUNT(*) AS n FROM contest_events WHERE contest_id = ? AND user_id = ? AND ts > ?",
                        (contest_id, user_id, since))
        return row["n"] if row else 0

    def set_contest_hidden(self, contest_id, hidden):
        with self._tx() as c:
            c.execute("UPDATE contests SET hidden = ? WHERE id = ?", (1 if hidden else 0, contest_id))

    def delete_contest(self, contest_id):
        """Delete a contest. Its submissions stay, they just no longer belong to a contest."""
        with self._tx() as c:
            c.execute("UPDATE submissions SET contest_id = NULL WHERE contest_id = ?", (contest_id,))
            c.execute("DELETE FROM contests WHERE id = ?", (contest_id,))

    def get_contest(self, slug):
        return self._one("SELECT * FROM contests WHERE slug = ?", (slug,))

    def get_contest_by_id(self, contest_id):
        return self._one("SELECT * FROM contests WHERE id = ?", (contest_id,))

    def list_contests(self, include_hidden=False):
        sql = "SELECT * FROM contests" + ("" if include_hidden else " WHERE hidden = 0")
        return self._all(sql + " ORDER BY starts_at DESC")

    def set_contest_problems(self, contest_id, slugs):
        with self._tx() as c:
            c.execute("DELETE FROM contest_problems WHERE contest_id = ?", (contest_id,))
            for position, slug in enumerate(slugs):
                c.execute("INSERT INTO contest_problems (contest_id, problem_slug, position) VALUES (?, ?, ?)",
                          (contest_id, slug, position))

    def contest_problems(self, contest_id):
        return [r["problem_slug"] for r in self._all(
            "SELECT problem_slug FROM contest_problems WHERE contest_id = ? ORDER BY position", (contest_id,))]

    def contests_with_problem(self, problem_slug):
        return self._all("SELECT c.* FROM contests c JOIN contest_problems p ON p.contest_id = c.id "
                         "WHERE p.problem_slug = ?", (problem_slug,))

    def contest_submissions(self, contest_id):
        """Submissions of a contest by non-admin accounts, oldest first (for the standings)."""
        return self._all(
            "SELECT s.id, s.user_id, u.username, s.problem_slug, s.status, s.created_at "
            "FROM submissions s JOIN users u ON u.id = s.user_id "
            "WHERE s.contest_id = ? AND u.is_admin = 0 ORDER BY s.id", (contest_id,))

    # ------------------------------------------------------------- login sessions
    def create_auth_session(self, token_hash, user_id, ttl_s):
        now = time.time()
        with self._tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))   # tidy up old ones
            c.execute("INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at) "
                      "VALUES (?, ?, ?, ?)", (token_hash, user_id, now, now + ttl_s))

    def get_auth_user(self, token_hash):
        """The account a login token belongs to, or None if unknown/expired."""
        return self._one(
            "SELECT u.id, u.username, u.is_admin FROM auth_sessions a JOIN users u ON u.id = a.user_id "
            "WHERE a.token_hash = ? AND a.expires_at > ?", (token_hash, time.time()))

    def delete_auth_session(self, token_hash):
        with self._tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

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
