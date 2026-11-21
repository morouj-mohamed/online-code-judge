"""
app.py - the web API of the Online Code Judge (Flask + Server-Sent Events).

    python3 app.py            (inside Docker: docker compose up)

ONE process does everything:

    browser --HTTP--> Flask request threads --submit()--> BoundedQueue --> worker threads
       ^                                                                     |
       |                                                                fork/exec/wait4
       +--- text/event-stream <--- EventBroker <--- publish() <-------------+

The worker pool lives INSIDE this process, so the server must run as a single
process (no `flask run --reload`, no multi-process gunicorn): a second process
would start a second pool on the same database.

Endpoints (all JSON; errors are {"error": {"code": ..., "message": ...}})

    GET  /api/problems                 list of problems (no tests)
    GET  /api/problems/<slug>          statement + the public sample only
    POST /api/submit                   {problem, language, code} -> 202 {id, status: "Pending", seq}
    POST /api/run-sample               {problem, language, code} -> runs the sample now, returns the result
    GET  /api/submissions              this session's recent submissions (?limit=)
    GET  /api/submissions/<id>         one of this session's submissions (+ per-test results, ?events=1)
    GET  /api/stream                   Server-Sent Events (?submission=<id>&since=<seq>&types=a,b)
    GET  /api/inspector/state          workers, queue, isolation level, last rlimits
    POST /api/inspector/stress         fires 20 submissions at the pool
    POST /api/submissions/<id>/sample-run   re-run one of YOUR submissions on the
                                       public sample and get its real output (for a diff)
    POST /api/auth/signup | login | logout, GET /api/auth/me      accounts
    /api/admin/...                     problem management (admins only)

Who owns a submission: the account it was made with, or, for a visitor who
is not logged in, the anonymous browser session. Logging in moves the
session's anonymous submissions to the account. Admins may open any one.

Pages (Phase 4, HTML rendered from templates/, behaviour in static/js/):

    /                       problem list        /problems/<slug>   statement + editor
    /submissions            your submissions    /submissions/<id>  live result
    /login  /signup         accounts            /admin             problem management

Hidden tests are never sent to a client: problem endpoints only expose the
sample, and every verdict message is rewritten by public_message() so that it
cannot carry hidden input or expected output (see that function).
"""

import builtins
import collections
import importlib
import json
import os
import re
import secrets
import signal
import sqlite3
import sys
import threading
import time

from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, stream_with_context
from werkzeug.exceptions import HTTPException

import config
from judge import auth
from judge import db as dbmod
from judge import events as evmod
from judge import problemstore
from judge import queue as jqueue
from judge import sandbox
from judge import worker as wmod

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{20,64}$")

FINAL_STATUSES = dbmod.FINAL_STATUSES

# Names of Python's built-in exception classes ("ZeroDivisionError", ...).
# Only these may appear in a public Runtime Error message (see public_message).
BUILTIN_EXCEPTIONS = frozenset(
    name for name, obj in vars(builtins).items()
    if isinstance(obj, type) and issubclass(obj, BaseException))

# Event detail keys that could carry program output. The sandbox does not put
# such keys in its events today; dropping them anyway keeps the stream safe if
# a later change adds one.
LEAKY_KEYS = frozenset({"stdout", "stderr", "output", "expected", "actual", "input", "message"})


class ApiError(Exception):
    """An error that becomes a JSON response: {"error": {"code", "message"}}."""

    def __init__(self, status, code, message, headers=None, **extra):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers or {}
        self.extra = extra


# ============================================================================
# Keeping hidden tests hidden
# ============================================================================
_RE_REASON = re.compile(
    r"^(terminated by signal [A-Z0-9]+(?: \([^)]*\))?"
    r"|wrote more than the allowed file size \(SIGXFSZ\)"
    r"|exited with code -?\d+)"
    r"(?:: (.*))?$", re.S)


def public_message(status, message, failed_test=None):
    """
    The judge's internal messages are detailed: a Wrong Answer says
    "line 1: expected '3000000000', got '-1294967296'" and a Runtime Error ends
    with the first line the program wrote to stderr. Both would leak hidden
    tests: the expected output directly, the input because a program can copy
    its stdin to stderr and then crash. So a client only gets this rewritten
    message. It keeps what is useful (which test, which line, which signal,
    which built-in exception) and drops anything the program or the test
    files could control. The full message stays in the database for teachers.
    """
    message = message or ""
    if status in (config.PENDING, config.RUNNING) or status not in FINAL_STATUSES:
        return ""
    if status == config.ACCEPTED:
        return "all tests passed"
    if status == config.COMPILATION_ERROR:
        # Compiler output only talks about the user's own source code; the
        # compiler never sees a test file.
        return message
    if status == config.JUDGE_ERROR:
        return "internal judge error (not caused by your code)"

    where = f"test {failed_test}" if failed_test is not None else "a test"
    body = re.sub(r"^test \d+: ", "", message)
    if status == config.WRONG_ANSWER:
        line = re.match(r"line (\d+):", body)
        detail = f" (first difference on line {line.group(1)})" if line else ""
        return f"{where}: output differs from the expected answer{detail}"
    if status in (config.TIME_LIMIT_EXCEEDED, config.MEMORY_LIMIT_EXCEEDED):
        # These messages are built only from numbers the kernel measured.
        return f"{where}: {body}"
    if status == config.RUNTIME_ERROR:
        match = _RE_REASON.match(body)
        text = match.group(1) if match else "the program crashed"
        extra = (match.group(2) or "") if match else ""
        exception = extra.split(":", 1)[0].strip()
        if exception in BUILTIN_EXCEPTIONS:
            text += f" ({exception})"
        return f"{where}: {text}"
    return f"{where}: {status}"


def public_test(row):
    """One row of test_results, safe to show (no hidden data)."""
    verdict = row["verdict"]
    message = ("output matches" if verdict == config.ACCEPTED
               else public_message(verdict, row["message"], row["test_index"]))
    return {"test": row["test_index"], "verdict": verdict, "time_ms": row["time_ms"],
            "wall_ms": row["wall_ms"], "memory_kb": row["memory_kb"],
            "memory_source": row["memory_source"], "exit_code": row["exit_code"],
            "signal": row["signal"], "message": message}


def public_submission(row, detail=False):
    data = {
        "id": row["id"], "problem": row["problem_slug"], "language": row["language"],
        "status": row["status"], "failed_test": row["failed_test"],
        "message": public_message(row["status"], row["message"], row["failed_test"]),
        "tests_run": row["tests_run"], "tests_total": row["tests_total"],
        "time_ms": row["time_ms"], "memory_kb": row["memory_kb"], "worker_id": row["worker_id"],
        "created_at": row["created_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }
    if detail:
        data["source"] = row["source"]
    return data


def safe_details(details):
    return {k: v for k, v in (details or {}).items() if k not in LEAKY_KEYS}


# ============================================================================
# Rate limiting: a sliding window of request times per key
# ============================================================================
class RateLimiter:
    """
    For every key ("submit", "session:abc") we remember the times of the recent
    requests in a deque. A request is allowed if fewer than `limit` of them are
    younger than `window` seconds. One lock protects the dict, because Flask
    serves every request in its own thread.
    """

    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()
        self._last_prune = time.monotonic()

    def check(self, bucket, keys):
        """
        keys: [(key, (limit, window)), ...]. Records the request only if EVERY
        key allows it. Returns 0 when allowed, else the seconds to wait.
        """
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            wait = 0.0
            for key, (limit, window) in keys:
                hits = self._hits.setdefault((bucket, key), collections.deque())
                while hits and now - hits[0] >= window:
                    hits.popleft()
                if len(hits) >= limit:
                    wait = max(wait, window - (now - hits[0]))
            if wait > 0:
                return wait
            for key, _ in keys:
                self._hits[(bucket, key)].append(now)
            return 0

    def _prune(self, now):
        """Forget keys that have been quiet for a while, so memory stays small."""
        if now - self._last_prune < 60:
            return
        self._last_prune = now
        for key in [k for k, d in self._hits.items() if not d or now - d[-1] > 3600]:
            del self._hits[key]


# ============================================================================
# Problems (only public parts)
# ============================================================================
def _problem_dir(slug):
    return os.path.join(config.PROBLEMS_DIR, slug)


def load_public_problem(slug, detail=False, include_hidden=False):
    """
    Problem info for clients. Raises ApiError(404) for an unknown slug, and
    for a hidden (unpublished) problem unless include_hidden (admins).
    """
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise ApiError(404, "problem_not_found", f"unknown problem: {slug!r}")
    if not include_hidden and problemstore.is_hidden(slug):
        raise ApiError(404, "problem_not_found", f"unknown problem: {slug!r}")
    try:
        problem = sandbox.load_problem(slug)
    except (sandbox.ProblemError, OSError, ValueError):
        raise ApiError(404, "problem_not_found", f"unknown problem: {slug!r}") from None
    limits = {}
    for language in config.LANGUAGES:
        eff = config.effective_limits(language, problem["time_limit_ms"], problem["memory_limit_mb"])
        limits[language] = {"time_ms": eff["time_ms"], "memory_mb": eff["memory_kb"] // 1024}
    with open(os.path.join(_problem_dir(slug), "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    tags = meta.get("tags", [])
    data = {"slug": slug, "index": str(meta.get("index", "")), "title": problem["title"],
            "difficulty": meta.get("difficulty", "Easy"),
            "tags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
            "time_limit_ms": problem["time_limit_ms"],
            "memory_limit_mb": problem["memory_limit_mb"], "tests_count": len(problem["tests"]),
            "limits_by_language": limits}
    if include_hidden:
        data["hidden"] = bool(meta.get("hidden", False))
    if detail:
        folder = _problem_dir(slug)
        with open(os.path.join(folder, "statement.md"), encoding="utf-8") as f:
            data["statement"] = f.read()
        with open(os.path.join(folder, "sample.in"), encoding="utf-8") as f:
            data["sample_input"] = f.read()
        with open(os.path.join(folder, "sample.out"), encoding="utf-8") as f:
            data["sample_output"] = f.read()
    return data


def list_problem_slugs(include_hidden=False):
    return [slug for slug in problemstore.list_slugs()
            if include_hidden or not problemstore.is_hidden(slug)]


# ============================================================================
# Who is asking: the anonymous session and, if logged in, the account
# ============================================================================
class Viewer:
    """The browser making a request: its anonymous session id and maybe a user."""

    def __init__(self, session_id, user=None):
        self.session_id = session_id
        self.user = user                      # {"id", "username", "is_admin"} or None

    @property
    def user_id(self):
        return self.user["id"] if self.user else None

    @property
    def is_admin(self):
        return bool(self.user and self.user["is_admin"])

    def owns(self, owner, allow_admin=False):
        """owner = (session_id, user_id) of a submission."""
        if owner is None:
            return False
        if allow_admin and self.is_admin:
            return True
        owner_session, owner_user = owner
        if owner_user is not None:
            return self.user_id == owner_user
        # Anonymous submission: only the same browser session, and only while
        # it is not logged in to an account (after login it was moved over).
        return self.user is None and owner_session == self.session_id


# ============================================================================
# The judge service: database + event broker + worker pool, started once
# ============================================================================
class JudgeService:
    def __init__(self, db_path=None, workers=None, capacity=None):
        self.db = dbmod.Database(db_path)
        self.broker = evmod.EventBroker()
        self.pool = wmod.WorkerPool(self.db, self.broker, n_workers=workers, capacity=capacity)
        self.limiter = RateLimiter()
        # "Run sample" uids: one slot per concurrent runner, after the workers'
        # slots. Our own BoundedQueue doubles as a pool of free slots: get() takes
        # one (waiting if all are busy), put() gives it back.
        self.sample_slots = jqueue.BoundedQueue(config.SAMPLE_RUNNERS)
        for i in range(config.SAMPLE_RUNNERS):
            self.sample_slots.put(self.pool.n_workers + i)
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._last_limits = None
        self._owners = collections.OrderedDict()   # submission id -> session id (cache)
        self._tracker = None
        self._tracker_sub = None
        self._stopped = False

    # ------------------------------------------------------------ lifecycle
    def start(self):
        """Start the workers (re-queuing work a previous run left unfinished)."""
        recovered = self.pool.start(recover=True)
        # Follow the judge events to remember the last limits applied to a user
        # program, for the inspector.
        self._tracker_sub = self.broker.subscribe(types={"judge"})
        self._tracker = threading.Thread(target=self._track_limits, name="limits-tracker",
                                         daemon=True)
        self._tracker.start()
        return recovered

    def stop(self):
        """
        Graceful shutdown, safe to call twice. Running submissions finish;
        waiting ones stay Pending in SQLite and are re-queued at the next start.
        Closing the broker ends every open SSE stream.
        """
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        print("Shutting down: waiting for running submissions...", flush=True)
        stopped = self.pool.shutdown(drain=False, timeout=config.SHUTDOWN_TIMEOUT_S)
        self.broker.close()
        if self._tracker is not None:
            self._tracker.join(timeout=2)
        print(f"Worker pool stopped cleanly: {stopped}", flush=True)

    def _track_limits(self):
        for event in self._tracker_sub:
            self.observe_judge_event(event["data"], event["submission_id"])

    def observe_judge_event(self, data, submission_id):
        """Remember rlimits applied to a user program (not to the compiler)."""
        details = data.get("details", {})
        label = str(details.get("label", ""))
        if data.get("step") not in ("limits_applied", "address_space_limit_applied"):
            return
        if not label.startswith(("test", "sample")):
            return
        values = {k: v for k, v in details.items() if k.startswith("RLIMIT_")}
        with self._lock:
            current = self._last_limits
            same_run = (current is not None and current["pid"] == data.get("pid"))
            merged = dict(current["values"]) if same_run else {}
            merged.update(values)
            self._last_limits = {"pid": data.get("pid"), "label": label, "ts": data.get("ts"),
                                 "submission_id": submission_id, "values": merged}

    # ------------------------------------------------------------ ownership
    def owner_of(self, submission_id):
        """(session_id, user_id) of a submission. Cached: the stream asks for every event."""
        with self._lock:
            if submission_id in self._owners:
                self._owners.move_to_end(submission_id)
                return self._owners[submission_id]
        owner = self.db.get_owner(submission_id)
        with self._lock:
            self._owners[submission_id] = owner
            while len(self._owners) > 20000:
                self._owners.popitem(last=False)
        return owner

    def forget_owners(self):
        """Ownership changed (a login claimed anonymous submissions): drop the cache."""
        with self._lock:
            self._owners.clear()

    # ------------------------------------------------------------ actions
    def submit(self, viewer, problem, language, code):
        seq = self.broker.stats()["published"]
        try:
            sid = self.pool.submit(problem, language, code, block=True,
                                   timeout=config.SUBMIT_QUEUE_WAIT_S,
                                   session_id=viewer.session_id, user_id=viewer.user_id)
        except jqueue.Full:
            raise ApiError(503, "queue_full", "the judge queue is full, try again in a moment",
                           headers={"Retry-After": "5"}) from None
        except wmod.PoolClosed:
            raise ApiError(503, "shutting_down", "the judge is shutting down") from None
        with self._lock:
            self._owners[sid] = (viewer.session_id, viewer.user_id)
        return sid, seq

    def _take_slot(self):
        try:
            return self.sample_slots.get(timeout=config.SAMPLE_WAIT_S)
        except (jqueue.Empty, jqueue.Closed):
            raise ApiError(503, "sample_runners_busy", "all sample runners are busy, try again",
                           headers={"Retry-After": "2"}) from None

    def validate_solution(self, problem_slug, language, code):
        """
        Admin check: run a reference solution on ALL hidden tests (stops at the
        first failure). Admins see the full, unredacted messages.
        """
        slot = self._take_slot()
        try:
            result = sandbox.judge_submission(problem_slug, language, code,
                                              on_event=lambda e: None, uid_slot=slot)
        finally:
            self.sample_slots.put(slot)
        return {
            "verdict": result["verdict"], "message": result["message"],
            "failed_test": result["failed_test"], "total_tests": result["total_tests"],
            "time_ms": result["time_ms"], "memory_kb": result["memory_kb"],
            "compile_output": (result["compile"] or {}).get("message", ""),
            "tests": [{"test": t["test"], "verdict": t["verdict"], "time_ms": t["time_ms"],
                       "memory_kb": t["memory_kb"], "message": t["message"]} for t in result["tests"]],
        }

    def run_sample(self, problem_slug, language, code):
        """Compile and run on the public sample, synchronously, outside the queue."""
        problem = sandbox.load_problem(problem_slug)
        folder = _problem_dir(problem_slug)
        problem = dict(problem)
        problem["tests"] = [{"index": 1, "input": os.path.join(folder, "sample.in"),
                             "expected": os.path.join(folder, "sample.out")}]
        slot = self._take_slot()
        try:
            def on_event(event):
                details = dict(event["details"])
                if str(details.get("label", "")).startswith("test"):
                    details["label"] = "sample"
                self.observe_judge_event({**event, "details": details}, None)

            result = sandbox.judge_submission(problem, language, code,
                                              on_event=on_event, uid_slot=slot)
        finally:
            self.sample_slots.put(slot)

        test = result["tests"][0] if result["tests"] else None
        with open(os.path.join(folder, "sample.in"), encoding="utf-8") as f:
            sample_input = f.read()
        with open(os.path.join(folder, "sample.out"), encoding="utf-8") as f:
            sample_output = f.read()
        # The sample is public, so the full message (expected vs got) and the
        # program's real output are fine here. This is the ONLY place where
        # program output leaves the server, and only for the public sample.
        message = re.sub(r"^test 1: ", "", result["message"])
        if result["verdict"] == config.ACCEPTED:
            message = "sample passed"
        return {
            "verdict": result["verdict"],
            "message": message,
            "compile_output": (result["compile"] or {}).get("message", "")
                              if result["verdict"] == config.COMPILATION_ERROR else "",
            "time_ms": result["time_ms"],
            "memory_kb": result["memory_kb"],
            "test": None if test is None else {
                "verdict": test["verdict"], "time_ms": test["time_ms"], "wall_ms": test["wall_ms"],
                "memory_kb": test["memory_kb"], "memory_source": test["memory_source"],
                "exit_code": test["exit_code"], "signal": test["signal"], "stderr": test["stderr"],
            },
            "input": sample_input,
            "expected_output": sample_output,
            "stdout": test["stdout"] if test else "",
            "isolation": result["isolation"],
        }

    def stress(self, viewer):
        """Queue the 20 mixed programs of tools/stress.py without waiting."""
        stress_mod = importlib.import_module("tools.stress")
        ids, rejected = [], 0
        for label, problem, language, path, expected, failed in \
                stress_mod.SUBMISSIONS[:config.STRESS_SUBMISSIONS]:
            with open(path, encoding="utf-8") as f:
                code = f.read()
            try:
                sid = self.pool.submit(problem, language, code, block=False,
                                       session_id=viewer.session_id, user_id=viewer.user_id)
            except (jqueue.Full, wmod.PoolClosed):
                rejected += 1
                continue
            with self._lock:
                self._owners[sid] = (viewer.session_id, viewer.user_id)
            ids.append({"id": sid, "program": label, "problem": problem, "language": language,
                        "expected": expected, "expected_failed_test": failed})
        return {"submitted": len(ids), "rejected": rejected, "submissions": ids}

    def inspector_state(self):
        snap = self.pool.snapshot()
        iso = sandbox.get_isolation()
        workers = []
        for w in snap["workers"]:
            uid = (config.SANDBOX_UID + w["uid_slot"]) if iso.drop_privileges else os.getuid()
            workers.append({**w, "sandbox_uid": uid})
        with self._lock:
            last = None if self._last_limits is None else dict(self._last_limits)
        busy = config.SAMPLE_RUNNERS - self.sample_slots.qsize()
        return {
            "server": {"pid": os.getpid(), "uptime_s": round(time.time() - self.started_at, 1),
                       "threads": threading.active_count(), "python": sys.version.split()[0]},
            "isolation": {"level": iso.level, "chroot": iso.chroot,
                          "drop_privileges": iso.drop_privileges, "description": iso.description},
            "queue": snap["queue"],
            "workers": workers,
            "stats": snap["stats"],
            "outstanding": snap["outstanding"],
            "sample_runners": {"total": config.SAMPLE_RUNNERS, "busy": busy},
            "last_limits": last,
            "events": self.broker.stats(),
            "submissions_by_status": self.db.count_by_status(),
        }


# ============================================================================
# Server-Sent Events
# ============================================================================
def sse_format(event_name, data, event_id=None):
    """
    One SSE message. The format is plain text:
        id: 42            the browser sends it back as Last-Event-ID on reconnect
        event: test       which addEventListener() handler receives it
        data: {...}       the payload (one line of JSON)
        <empty line>      ends the message
    """
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append("data: " + json.dumps(data, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


def translate_event(service, event, viewer, allow_admin=False):
    """
    Turn a broker event into (sse_event_name, data), or None if this viewer
    must not see it. Submission and judge events go only to the owner of the
    submission (or an admin watching that one submission); worker and queue
    events are about the machine and go to everybody (no code, no test data).
    """
    kind = event["type"]
    sid = event["submission_id"]
    data = event["data"]
    if kind in ("worker", "queue"):
        return kind, data
    if sid is None or not viewer.owns(service.owner_of(sid), allow_admin=allow_admin):
        return None
    if kind == "submission":
        status = data.get("status")
        out = {"submission_id": sid, "status": status, "worker_id": data.get("worker_id"),
               "failed_test": data.get("failed_test")}
        if status in FINAL_STATUSES:
            row = service.db.get_submission(sid)
            if row is not None:
                out.update(public_submission(row))
                out["submission_id"] = sid
        return "submission", out
    if kind == "judge":
        details = data.get("details", {})
        if data.get("step") == "test_result":
            return "test", {"submission_id": sid, "test": details.get("test"),
                            "verdict": details.get("verdict"), "time_ms": details.get("time_ms"),
                            "memory_kb": details.get("memory_kb")}
        return "judge", {"submission_id": sid, "worker_id": data.get("worker_id"),
                         "step": data.get("step"), "pid": data.get("pid"), "ts": data.get("ts"),
                         "details": safe_details(details)}
    return None


# ============================================================================
# The Flask application
# ============================================================================
def create_app(service=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_REQUEST_BYTES
    app.json.sort_keys = False
    service = service or JudgeService()
    app.extensions["judge"] = service

    # ---------------------------------------------------------------- session
    @app.before_request
    def assign_session():
        sid = request.cookies.get(config.SESSION_COOKIE, "")
        g.new_session = not SESSION_RE.match(sid)
        # 18 random bytes from the OS (secrets uses os.urandom) -> 24 URL-safe characters.
        g.session_id = secrets.token_urlsafe(18) if g.new_session else sid
        # Logged in? The cookie holds a random token; the database knows its hash.
        token = request.cookies.get(config.AUTH_COOKIE, "")
        user = service.db.get_auth_user(auth.token_hash(token)) if 20 <= len(token) <= 100 else None
        g.viewer = Viewer(g.session_id, user)
        g.clear_auth_cookie = bool(token) and user is None

    @app.before_request
    def check_origin():
        """
        Cross-site request forgery (CSRF) protection. A page on another site
        could make the browser send a POST here with our cookies attached.
        Browsers add an Origin header to such requests, so any state-changing
        request whose Origin is not this very server is refused.
        """
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            raise ApiError(403, "bad_origin", "cross-site requests are not allowed")
        return None

    @app.before_request
    def upload_limit():
        # Test ZIPs are bigger than any other request: raise the limit for that one URL.
        if request.path.startswith("/api/admin/") and request.path.endswith("/tests"):
            request.max_content_length = problemstore.MAX_ZIP_BYTES + 1024 * 1024

    @app.context_processor
    def template_globals():
        return {"viewer": g.get("viewer"), "allow_signup": config.ALLOW_SIGNUP}

    @app.after_request
    def finish_response(response):
        if g.get("new_session"):
            response.set_cookie(config.SESSION_COOKIE, g.session_id,
                                max_age=config.SESSION_COOKIE_MAX_AGE_S, httponly=True,
                                samesite="Lax", secure=config.SESSION_COOKIE_SECURE)
        if g.get("set_auth_token"):
            response.set_cookie(config.AUTH_COOKIE, g.set_auth_token, max_age=config.AUTH_TTL_S,
                                httponly=True, samesite="Lax", secure=config.SESSION_COOKIE_SECURE)
        elif g.get("clear_auth_cookie"):
            response.delete_cookie(config.AUTH_COOKIE, samesite="Lax")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if response.mimetype == "text/html":
            # Content Security Policy: the pages may only load scripts, fonts and
            # data from this server. Even if an attacker managed to inject HTML,
            # the browser would refuse to run any script that is not our own file.
            # ('unsafe-inline' for styles only: the code editor sets styles at runtime.)
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if request.path.startswith("/api/") and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    # ---------------------------------------------------------------- errors
    @app.errorhandler(ApiError)
    def api_error(err):
        body = {"error": {"code": err.code, "message": err.message, **err.extra}}
        return jsonify(body), err.status, err.headers

    @app.errorhandler(HTTPException)
    def http_error(err):
        if err.code == 404 and not request.path.startswith("/api") \
                and not request.path.startswith("/static"):
            return render_template("not_found.html", page="not-found"), 404
        codes = {400: "bad_request", 404: "not_found", 405: "method_not_allowed",
                 413: "request_too_large", 415: "unsupported_media_type"}
        message = err.description
        if err.code == 413:
            message = f"request body is larger than {config.MAX_REQUEST_BYTES} bytes"
        return jsonify({"error": {"code": codes.get(err.code, "http_error"),
                                  "message": message}}), err.code

    @app.errorhandler(Exception)
    def internal_error(err):
        app.logger.exception("unhandled error")
        return jsonify({"error": {"code": "internal_error",
                                  "message": "internal server error"}}), 500

    # ---------------------------------------------------------------- helpers
    def rate_limit(bucket):
        limits = config.RATE_LIMITS[bucket]
        who = f"user:{g.viewer.user_id}" if g.viewer.user else f"session:{g.session_id}"
        wait = service.limiter.check(bucket, [
            (who, limits["session"]),
            (f"ip:{request.remote_addr}", limits["ip"]),
        ])
        if wait:
            seconds = max(1, int(wait + 0.999))
            raise ApiError(429, "rate_limited", f"too many requests, retry in {seconds} s",
                           headers={"Retry-After": str(seconds)}, retry_after=seconds)

    def read_code_request():
        """Validate {problem, language, code}. Returns the three values."""
        if not request.is_json:
            raise ApiError(415, "unsupported_media_type", "send JSON with Content-Type: application/json")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "the body must be a JSON object")
        problem, language, code = body.get("problem"), body.get("language"), body.get("code")
        if not isinstance(problem, str) or not isinstance(language, str) or not isinstance(code, str):
            raise ApiError(400, "missing_fields",
                           "fields 'problem', 'language' and 'code' are required strings")
        if language not in config.LANGUAGES:
            raise ApiError(400, "unsupported_language",
                           f"language must be one of {sorted(config.LANGUAGES)}")
        size = len(code.encode("utf-8"))
        if size > config.MAX_CODE_BYTES:
            raise ApiError(413, "code_too_large",
                           f"code is {size} bytes, the limit is {config.MAX_CODE_BYTES}")
        if not code.strip():
            raise ApiError(400, "empty_code", "the code is empty")
        if "\x00" in code:
            raise ApiError(400, "invalid_code", "the code contains a NUL byte")
        try:
            load_public_problem(problem, include_hidden=g.viewer.is_admin)
        except ApiError:
            raise ApiError(400, "unknown_problem", f"unknown problem: {problem!r}") from None
        return problem, language, code

    def read_json():
        if not request.is_json:
            raise ApiError(415, "unsupported_media_type", "send JSON with Content-Type: application/json")
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "the body must be a JSON object")
        return body

    def require_admin():
        if not g.viewer.user:
            raise ApiError(401, "login_required", "log in first")
        if not g.viewer.is_admin:
            raise ApiError(403, "admin_only", "only admins can do this")

    def log_in(user_id):
        """Start a login session for this browser and give it its anonymous work."""
        token, token_hash = auth.new_token()
        service.db.create_auth_session(token_hash, user_id, config.AUTH_TTL_S)
        service.db.touch_login(user_id)
        moved = service.db.claim_submissions(g.session_id, user_id)
        service.forget_owners()
        g.set_auth_token = token
        return moved

    def public_user(user):
        return None if not user else {"username": user["username"], "is_admin": bool(user["is_admin"])}

    def own_submission(submission_id, allow_admin=True):
        row = service.db.get_submission(submission_id)
        # Somebody else's submission answers exactly like a missing one, so ids
        # of other sessions cannot even be probed.
        owner = (row["session_id"], row["user_id"]) if row else None
        if not g.viewer.owns(owner, allow_admin=allow_admin):
            raise ApiError(404, "submission_not_found", f"no submission {submission_id}")
        return row

    # ---------------------------------------------------------------- routes
    # ---------------------------------------------------------------- pages
    # The HTML is a thin shell; each page's ES module (static/js/page-*.js)
    # fetches the data from the JSON API below and keeps it live with SSE.
    @app.get("/")
    def page_problems():
        return render_template("problems.html", page="problems")

    @app.get("/problems/<slug>")
    def page_problem(slug):
        try:
            problem = load_public_problem(slug, include_hidden=g.viewer.is_admin)
        except ApiError:
            abort(404)
        return render_template("problem.html", page="problem", problem=problem)

    @app.get("/login")
    def page_login():
        if g.viewer.user:
            return redirect("/")
        return render_template("auth.html", page="login", mode="login")

    @app.get("/signup")
    def page_signup():
        if g.viewer.user:
            return redirect("/")
        if not config.ALLOW_SIGNUP:
            abort(404)
        return render_template("auth.html", page="signup", mode="signup")

    @app.get("/admin")
    def page_admin():
        if not g.viewer.user:
            return redirect("/login?next=/admin")
        if not g.viewer.is_admin:
            abort(404)
        return render_template("admin.html", page="admin")

    @app.get("/admin/problems/new")
    @app.get("/admin/problems/<slug>")
    def page_admin_problem(slug=None):
        if not g.viewer.user:
            return redirect(f"/login?next={request.path}")
        if not g.viewer.is_admin or (slug and not problemstore.exists(slug)):
            abort(404)
        return render_template("admin_problem.html", page="admin", slug=slug or "")

    @app.get("/submissions")
    def page_submissions():
        return render_template("submissions.html", page="submissions")

    @app.get("/submissions/<int:submission_id>")
    def page_result(submission_id):
        return render_template("result.html", page="result", submission_id=submission_id)

    @app.get("/api")
    def api_index():
        return jsonify({"name": "Online Code Judge API", "endpoints": [
            "GET /api/problems", "GET /api/problems/<slug>", "POST /api/submit",
            "POST /api/run-sample", "GET /api/submissions", "GET /api/submissions/<id>",
            "POST /api/submissions/<id>/sample-run", "GET /api/stream",
            "GET /api/inspector/state", "POST /api/inspector/stress"]})

    @app.get("/api/problems")
    def problems():
        admin = g.viewer.is_admin
        return jsonify({"problems": [load_public_problem(s, include_hidden=admin)
                                     for s in list_problem_slugs(include_hidden=admin)],
                        "languages": {k: v["label"] for k, v in config.LANGUAGES.items()}})

    @app.get("/api/problems/<slug>")
    def problem(slug):
        return jsonify(load_public_problem(slug, detail=True, include_hidden=g.viewer.is_admin))

    @app.post("/api/submit")
    def submit():
        problem_slug, language, code = read_code_request()
        rate_limit("submit")
        sid, seq = service.submit(g.viewer, problem_slug, language, code)
        return jsonify({"id": sid, "status": config.PENDING, "seq": seq,
                        "stream": f"/api/stream?submission={sid}&since={seq}"}), 202

    @app.post("/api/run-sample")
    def run_sample():
        problem_slug, language, code = read_code_request()
        rate_limit("run_sample")
        try:
            return jsonify(service.run_sample(problem_slug, language, code))
        except sandbox.SandboxError as exc:
            raise ApiError(500, "judge_error", f"the judge failed: {exc}") from None

    @app.get("/api/submissions")
    def submissions():
        try:
            limit = int(request.args.get("limit", 20))
        except ValueError:
            raise ApiError(400, "invalid_limit", "limit must be an integer") from None
        limit = max(1, min(limit, config.SUBMISSIONS_PAGE_MAX))
        scope = request.args.get("scope", "mine")
        if scope == "all" and g.viewer.is_admin:
            rows = service.db.list_submissions(limit=limit, with_username=True)
        elif g.viewer.user:
            rows = service.db.list_submissions(limit=limit, user_id=g.viewer.user_id, with_username=True)
        else:
            rows = service.db.list_submissions(limit=limit, session_id=g.session_id, anonymous_only=True)
        out = []
        for r in rows:
            item = public_submission(r)
            if "username" in r:
                item["username"] = r["username"]
            out.append(item)
        return jsonify({"submissions": out, "scope": "all" if scope == "all" and g.viewer.is_admin else "mine",
                        "account": public_user(g.viewer.user)})

    @app.get("/api/submissions/<int:submission_id>")
    def submission(submission_id):
        row = own_submission(submission_id)
        data = public_submission(row, detail=True)
        data["tests"] = [public_test(t) for t in service.db.get_test_results(submission_id)]
        if request.args.get("events") == "1":
            events = []
            for e in service.db.get_events(submission_id):
                details = e["details"]
                if e["source"] == "submission":
                    details = {**details, "message": public_message(
                        details.get("status"), details.get("message"), details.get("failed_test"))}
                else:
                    details = safe_details(details)
                events.append({"id": e["id"], "source": e["source"], "step": e["step"],
                               "ts": e["ts"], "pid": e["pid"], "worker_id": e["worker_id"],
                               "details": details})
            data["events"] = events
        return jsonify(data)

    @app.post("/api/submissions/<int:submission_id>/sample-run")
    def submission_sample_run(submission_id):
        """
        Run one of this session's submissions on the PUBLIC sample only, so the
        Result page can show a real side-by-side diff after a Wrong Answer.
        The hidden tests are not involved at all.
        """
        row = own_submission(submission_id, allow_admin=False)
        rate_limit("run_sample")
        try:
            return jsonify(service.run_sample(row["problem_slug"], row["language"], row["source"]))
        except sandbox.SandboxError as exc:
            raise ApiError(500, "judge_error", f"the judge failed: {exc}") from None

    @app.get("/api/stream")
    def stream():
        viewer = g.viewer
        only = request.args.get("submission")
        submission_id = None
        if only is not None:
            if not only.isdigit():
                raise ApiError(400, "invalid_submission", "submission must be an id")
            submission_id = int(only)
            own_submission(submission_id)
        since = request.headers.get("Last-Event-ID") or request.args.get("since")
        if since is not None:
            if not str(since).isdigit():
                raise ApiError(400, "invalid_since", "since / Last-Event-ID must be a number")
            since = int(since)
        types = request.args.get("types")
        wanted = set(t.strip() for t in types.split(",") if t.strip()) if types else None

        # Subscribe BEFORE reading the current state, so nothing that happens
        # in between can be missed.
        sub = service.broker.subscribe(submission_id=submission_id, since_seq=since)
        state = service.db.get_submission(submission_id) if submission_id is not None else None

        def generate():
            try:
                yield f"retry: {config.SSE_RETRY_MS}\n\n"
                yield sse_format("hello", {"session": "anonymous",
                                           "seq": service.broker.stats()["published"],
                                           "submission_id": submission_id,
                                           "history_gap": sub.history_gap})
                # Without `since` there is no replay, so tell the client where
                # the submission stands right now. (With `since`, the replayed
                # events already tell the whole story in order.)
                if state is not None and since is None:
                    yield sse_format("snapshot", public_submission(state))

                def emit(event):
                    out = translate_event(service, event, viewer, allow_admin=submission_id is not None)
                    if out is None:
                        return None, False
                    name, data = out
                    done = (submission_id is not None and name == "submission"
                            and data.get("status") in FINAL_STATUSES)
                    if wanted is not None and name not in wanted and not done:
                        return None, False
                    return sse_format(name, data, event["seq"]), done

                # 1) events replayed from history (only when `since` was given)
                for event in sub.drain():
                    text, done = emit(event)
                    if text:
                        yield text
                    if done:
                        yield sse_format("end", {"submission_id": submission_id})
                        return
                # 2) the submission may already be finished (stream opened late)
                if submission_id is not None:
                    row = service.db.get_submission(submission_id)
                    if row is not None and row["status"] in FINAL_STATUSES:
                        yield sse_format("submission", {**public_submission(row),
                                                        "submission_id": submission_id})
                        yield sse_format("end", {"submission_id": submission_id})
                        return
                # 3) live events
                while True:
                    event = sub.get(timeout=config.SSE_HEARTBEAT_S)
                    if event is None:
                        if sub.closed:          # the server is shutting down
                            yield sse_format("end", {"reason": "server shutting down"})
                            return
                        # A comment line: ignored by EventSource, keeps the
                        # connection alive, and fails if the client has gone.
                        yield ": ping\n\n"
                        continue
                    text, done = emit(event)
                    if text:
                        yield text
                    if done:
                        yield sse_format("end", {"submission_id": submission_id})
                        return
            finally:
                # Runs when the stream ends AND when the browser disconnects
                # (the next write fails and Python closes the generator).
                sub.close()

        return Response(stream_with_context(generate()), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/inspector/state")
    def inspector_state():
        return jsonify(service.inspector_state())

    @app.post("/api/inspector/stress")
    def inspector_stress():
        rate_limit("stress")
        return jsonify(service.stress(g.viewer)), 202

    # ---------------------------------------------------------------- accounts
    @app.get("/api/auth/me")
    def auth_me():
        return jsonify({"user": public_user(g.viewer.user), "allow_signup": config.ALLOW_SIGNUP})

    @app.post("/api/auth/signup")
    def auth_signup():
        if not config.ALLOW_SIGNUP:
            raise ApiError(403, "signup_closed", "new accounts are disabled on this server")
        body = read_json()
        rate_limit("auth")
        try:
            username = auth.validate_username(body.get("username"))
            password = auth.validate_password(body.get("password"), username)
        except auth.ValidationError as exc:
            raise ApiError(400, "invalid_account", str(exc)) from None
        try:
            user_id = service.db.create_user(username, auth.hash_password(password))
        except sqlite3.IntegrityError:
            raise ApiError(409, "username_taken", "this username is already taken") from None
        moved = log_in(user_id)
        return jsonify({"user": {"username": username, "is_admin": False},
                        "moved_submissions": moved}), 201

    @app.post("/api/auth/login")
    def auth_login():
        body = read_json()
        rate_limit("auth")
        username, password = body.get("username"), body.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(password) > 1024:
            raise ApiError(400, "missing_fields", "username and password are required")
        user = service.db.get_user_by_name(username)
        if user is None:
            auth.burn_time(password)           # same delay as a real check
            ok = False
        else:
            ok = auth.verify_password(password, user["password_hash"])
        if not ok:
            # One message for "no such user" and "wrong password": the answer
            # must not reveal which usernames exist.
            raise ApiError(401, "invalid_credentials", "wrong username or password")
        moved = log_in(user["id"])
        return jsonify({"user": public_user(user), "moved_submissions": moved})

    @app.post("/api/auth/logout")
    def auth_logout():
        token = request.cookies.get(config.AUTH_COOKIE, "")
        if token:
            service.db.delete_auth_session(auth.token_hash(token))
        g.clear_auth_cookie = True
        return jsonify({"user": None})

    # ---------------------------------------------------------------- admin
    def admin_problem_payload(slug):
        meta = problemstore.read_meta(slug)
        folder = _problem_dir(slug)
        with open(os.path.join(folder, "statement.md"), encoding="utf-8") as f:
            statement = f.read()
        with open(os.path.join(folder, "sample.in"), encoding="utf-8") as f:
            sample_in = f.read()
        with open(os.path.join(folder, "sample.out"), encoding="utf-8") as f:
            sample_out = f.read()
        return {"slug": slug, "index": meta.get("index", ""), "title": meta.get("title", slug),
                "difficulty": meta.get("difficulty", "Easy"), "tags": meta.get("tags", []),
                "time_limit_ms": meta.get("time_limit_ms", config.DEFAULT_TIME_LIMIT_MS),
                "memory_limit_mb": meta.get("memory_limit_mb", config.DEFAULT_MEMORY_LIMIT_MB),
                "hidden": bool(meta.get("hidden", False)), "validation": meta.get("validation"),
                "updated_at": meta.get("updated_at"), "statement": statement,
                "sample_input": sample_in, "sample_output": sample_out,
                "tests": problemstore.tests_info(slug)}

    def admin_slug(slug):
        if not problemstore.exists(slug):
            raise ApiError(404, "problem_not_found", f"unknown problem: {slug!r}")
        return slug

    @app.get("/api/admin/problems")
    def admin_problems():
        require_admin()
        counts = {}
        for row in service.db.list_submissions(limit=100000):
            c = counts.setdefault(row["problem_slug"], {"submissions": 0, "accepted": 0})
            c["submissions"] += 1
            c["accepted"] += row["status"] == config.ACCEPTED
        items = []
        for slug in problemstore.list_slugs():
            meta = problemstore.read_meta(slug)
            items.append({"slug": slug, "index": meta.get("index", ""), "title": meta.get("title", slug),
                          "difficulty": meta.get("difficulty", "Easy"), "tags": meta.get("tags", []),
                          "hidden": bool(meta.get("hidden", False)), "validation": meta.get("validation"),
                          "tests": problemstore.tests_info(slug), **counts.get(slug, {"submissions": 0, "accepted": 0})})
        return jsonify({"problems": items, "next_index": problemstore.next_index(),
                        "users": service.db.list_users()})

    @app.get("/api/admin/problems/<slug>")
    def admin_problem(slug):
        require_admin()
        return jsonify(admin_problem_payload(admin_slug(slug)))

    @app.post("/api/admin/problems")
    def admin_create_problem():
        require_admin()
        try:
            slug = problemstore.save_problem(read_json(), creating=True)
        except problemstore.ProblemStoreError as exc:
            raise ApiError(400, "invalid_problem", str(exc)) from None
        return jsonify(admin_problem_payload(slug)), 201

    @app.put("/api/admin/problems/<slug>")
    def admin_update_problem(slug):
        require_admin()
        admin_slug(slug)
        try:
            problemstore.save_problem(read_json(), slug=slug)
        except problemstore.ProblemStoreError as exc:
            raise ApiError(400, "invalid_problem", str(exc)) from None
        return jsonify(admin_problem_payload(slug))

    @app.post("/api/admin/problems/<slug>/visibility")
    def admin_visibility(slug):
        require_admin()
        admin_slug(slug)
        hidden = bool(read_json().get("hidden"))
        if not hidden and problemstore.tests_info(slug)["count"] == 0:
            raise ApiError(400, "no_tests", "upload tests before publishing")
        problemstore.set_hidden(slug, hidden)
        return jsonify(admin_problem_payload(slug))

    @app.post("/api/admin/problems/<slug>/tests")
    def admin_upload_tests(slug):
        require_admin()
        admin_slug(slug)
        upload = request.files.get("file")
        if upload is None:
            raise ApiError(400, "missing_file", "send the ZIP as multipart form field 'file'")
        try:
            info = problemstore.replace_tests(slug, upload.read(problemstore.MAX_ZIP_BYTES + 1))
        except problemstore.ProblemStoreError as exc:
            raise ApiError(400, "invalid_tests", str(exc)) from None
        return jsonify({**admin_problem_payload(slug), "uploaded": info})

    @app.post("/api/admin/problems/<slug>/validate")
    def admin_validate(slug):
        require_admin()
        admin_slug(slug)
        body = read_json()
        language, code = body.get("language"), body.get("code")
        if language not in config.LANGUAGES or not isinstance(code, str) or not code.strip():
            raise ApiError(400, "missing_fields", "language and code are required")
        if len(code.encode("utf-8")) > config.MAX_CODE_BYTES:
            raise ApiError(413, "code_too_large", "the code is too large")
        if problemstore.tests_info(slug)["count"] == 0:
            raise ApiError(400, "no_tests", "upload tests first")
        rate_limit("validate")
        try:
            result = service.validate_solution(slug, language, code)
        except sandbox.SandboxError as exc:
            raise ApiError(500, "judge_error", f"the judge failed: {exc}") from None
        problemstore.set_validation(slug, {"verdict": result["verdict"], "language": language,
                                           "at": time.time(), "time_ms": result["time_ms"],
                                           "tests": result["total_tests"],
                                           "by": g.viewer.user["username"]})
        return jsonify(result)

    return app


# ============================================================================
# Entry point
# ============================================================================
def _exit_on_sigterm(signum, frame):
    # `docker stop` sends SIGTERM. Python's default would kill us without
    # running `finally` blocks; turning it into SystemExit lets the server loop
    # end normally so service.stop() below can shut the workers down cleanly.
    raise SystemExit(0)


def main():
    service = JudgeService()
    app = create_app(service)
    recovered = service.start()
    iso = sandbox.get_isolation()
    print(f"Online Code Judge API on http://{config.HOST}:{config.PORT}", flush=True)
    print(f"  isolation : {iso.description}", flush=True)
    print(f"  workers   : {service.pool.n_workers}, queue capacity {service.pool.queue.capacity}, "
          f"re-queued after restart: {recovered}", flush=True)
    print(f"  database  : {service.db.path}", flush=True)
    signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        # threaded=True: every request (and every open SSE stream) gets its own
        # thread. use_reloader=False: the reloader would start a SECOND process
        # with a second worker pool.
        app.run(host=config.HOST, port=config.PORT, threaded=True, use_reloader=False, debug=False)
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()


if __name__ == "__main__":
    main()
