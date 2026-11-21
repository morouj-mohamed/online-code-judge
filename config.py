"""
config.py - every tunable number of the judge lives here.

Keeping the limits in one file means a student can answer the question
"where does the 256 MB come from?" by opening a single file.
"""

import math
import os
import tempfile

# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROBLEMS_DIR = os.path.join(BASE_DIR, "problems")

# Every submission gets its own private temp directory under this folder.
TMP_ROOT = os.environ.get("JUDGE_TMP_ROOT", tempfile.gettempdir())

# ----------------------------------------------------------------------------
# Verdicts (the only strings the judge is allowed to return)
# ----------------------------------------------------------------------------
PENDING = "Pending"
RUNNING = "Running"
ACCEPTED = "Accepted"
WRONG_ANSWER = "Wrong Answer"
TIME_LIMIT_EXCEEDED = "Time Limit Exceeded"
MEMORY_LIMIT_EXCEEDED = "Memory Limit Exceeded"
RUNTIME_ERROR = "Runtime Error"
COMPILATION_ERROR = "Compilation Error"

# NOT a verdict: an internal terminal state used when the judge itself crashes
# (for example gcc is missing). Without it such a submission would stay in
# "Running" forever. The user never sees it as an answer about their code.
JUDGE_ERROR = "Judge Error"

# ----------------------------------------------------------------------------
# The unprivileged user that user code runs as (only used when we are root).
# A numeric id does not need an entry in /etc/passwd, so this also works
# inside the chroot where /etc does not exist.
# ----------------------------------------------------------------------------
SANDBOX_UID = int(os.environ.get("JUDGE_SANDBOX_UID", "20000"))
SANDBOX_GID = int(os.environ.get("JUDGE_SANDBOX_GID", "20000"))

# Worker number i runs user code as uid SANDBOX_UID + i (and gid SANDBOX_GID + i).
# RLIMIT_NPROC counts processes PER USER ID, so if every worker shared one uid, a
# fork bomb in one worker would use up the process quota of all the others and
# make their execve() fail with EAGAIN. One uid per worker isolates them.

# ----------------------------------------------------------------------------
# Default limits (a problem's meta.json can override the first two)
# ----------------------------------------------------------------------------
DEFAULT_TIME_LIMIT_MS = 1000       # CPU time budget for C / C++
DEFAULT_MEMORY_LIMIT_MB = 256      # memory budget for C / C++

# RLIMIT_NPROC: how many processes+threads the sandbox user may own at once.
# This is what makes a fork bomb harmless: fork() starts failing with EAGAIN.
NPROC_LIMIT = 32
# RLIMIT_NOFILE: how many files the program may keep open.
NOFILE_LIMIT = 64
# RLIMIT_FSIZE: the biggest file (including stdout) a program may write.
FSIZE_LIMIT_MB = 16

# The watchdog kills the process group when the *wall clock* passes
#     effective_time_limit * WATCHDOG_WALL_FACTOR + WATCHDOG_EXTRA_S
# RLIMIT_CPU only counts CPU time, so a program that sleeps forever
# would never trip it. The watchdog catches that case.
WATCHDOG_WALL_FACTOR = 2.0
WATCHDOG_EXTRA_S = 0.5

# ----------------------------------------------------------------------------
# Compiler limits (the compiler is a program too, and it also gets limits)
# ----------------------------------------------------------------------------
COMPILE_TIME_LIMIT_S = 10
COMPILE_MEMORY_MB = 2048
COMPILE_OUTPUT_FSIZE_MB = 64
COMPILE_NOFILE_LIMIT = 256
COMPILE_MESSAGE_MAX_CHARS = 4000   # how much compiler text we keep

# ----------------------------------------------------------------------------
# Memory-limit heuristics (see classify() in judge/sandbox.py)
# ----------------------------------------------------------------------------
# A program that died abnormally while its resident memory was at least this
# fraction of the address-space limit is reported as Memory Limit Exceeded.
MLE_RSS_RATIO = 0.85
# Same idea for the peak *virtual* size (VmPeak) that we sample from /proc.
MLE_VM_RATIO = 0.95
# Text that C++/Python print when an allocation fails.
MLE_STDERR_SIGNATURES = (
    b"std::bad_alloc",
    b"MemoryError",
    b"Cannot allocate memory",
    b"out of memory",
    b"Out of memory",
)

# ----------------------------------------------------------------------------
# Languages
#   time_mult / mem_mult : multipliers applied to the problem limits, so a
#                          correct Python solution is not judged like C.
#   compiler             : None means "interpreted, nothing to compile".
# ----------------------------------------------------------------------------
LANGUAGES = {
    "c": {
        "label": "C (gcc, -O2, static)",
        "source_name": "main.c",
        "compiler": ["gcc", "-O2", "-std=gnu17", "-static", "-o", "prog", "main.c", "-lm"],
        "time_mult": 1.0,
        "mem_mult": 1.0,
    },
    "cpp": {
        "label": "C++ (g++, -O2, static)",
        "source_name": "main.cpp",
        "compiler": ["g++", "-O2", "-std=gnu++17", "-static", "-o", "prog", "main.cpp"],
        "time_mult": 1.0,
        "mem_mult": 1.0,
    },
    "python": {
        "label": "Python 3",
        "source_name": "main.py",
        "compiler": None,
        "time_mult": 3.0,
        "mem_mult": 2.0,
    },
}


def effective_limits(language, time_limit_ms, memory_limit_mb):
    """
    Turn a problem's base limits into the limits for one language.

    Returns a dict with:
      time_ms   - CPU time limit after the language multiplier
      memory_kb - memory limit after the language multiplier
      cpu_s     - integer seconds for RLIMIT_CPU (it only accepts whole seconds)
      wall_s    - wall-clock limit used by the watchdog
    """
    lang = LANGUAGES[language]
    time_ms = int(time_limit_ms * lang["time_mult"])
    memory_kb = int(memory_limit_mb * 1024 * lang["mem_mult"])
    return {
        "time_ms": time_ms,
        "memory_kb": memory_kb,
        # RLIMIT_CPU works in whole seconds, so we round up. The exact
        # millisecond check is done later from the measured CPU time.
        "cpu_s": max(1, math.ceil(time_ms / 1000.0)),
        "wall_s": time_ms / 1000.0 * WATCHDOG_WALL_FACTOR + WATCHDOG_EXTRA_S,
    }


# ----------------------------------------------------------------------------
# Phase 2: database, queue and workers
# ----------------------------------------------------------------------------
DB_PATH = os.environ.get("JUDGE_DB", os.path.join(BASE_DIR, "judge.sqlite3"))
WORKERS = int(os.environ.get("JUDGE_WORKERS", "3"))          # number of worker threads
QUEUE_CAPACITY = int(os.environ.get("JUDGE_QUEUE_CAPACITY", "100"))  # max waiting submissions
EVENT_HISTORY = 5000          # how many recent events the broker remembers for late subscribers
SUBSCRIBER_BUFFER = 2000      # per-subscriber buffer; the oldest events are dropped when it is full

# ----------------------------------------------------------------------------
# Phase 3: the web API (app.py)
# ----------------------------------------------------------------------------
# Inside Docker the server must listen on 0.0.0.0 to be reachable from the
# host; on a laptop 127.0.0.1 (this machine only) is the safe default.
HOST = os.environ.get("JUDGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("JUDGE_PORT", "5000"))

# Request validation
MAX_CODE_BYTES = 64 * 1024                 # source code limit (UTF-8 bytes)
MAX_REQUEST_BYTES = MAX_CODE_BYTES + 16 * 1024  # whole JSON body (code + JSON overhead)

# Anonymous session: a random id in a cookie, no login. It decides which
# submissions "belong" to a browser, and it is the key for the rate limit.
SESSION_COOKIE = "ocj_session"
SESSION_COOKIE_MAX_AGE_S = 365 * 24 * 3600
# Send the cookie only over HTTPS. Off by default because the dev server is
# plain HTTP; turn it on behind an HTTPS proxy.
SESSION_COOKIE_SECURE = os.environ.get("JUDGE_COOKIE_SECURE", "0") == "1"

# Rate limits: (max requests, window in seconds), counted per session and,
# because a client can simply throw its cookie away, also per IP address
# with a larger budget.
RATE_LIMITS = {
    "submit":     {"session": (10, 60), "ip": (40, 60)},
    "run_sample": {"session": (20, 60), "ip": (60, 60)},
    "stress":     {"session": (1, 60),  "ip": (3, 60)},
    # Login and sign-up: slows down password guessing (10 tries per 5 minutes).
    "auth":       {"session": (10, 300), "ip": (30, 300)},
    # Admin "validate with a reference solution" runs every hidden test.
    "validate":   {"session": (10, 60), "ip": (20, 60)},
}

# How long POST /api/submit waits for a free place in a full queue before it
# answers 503 "queue full" (a short back-pressure wait, then give up).
SUBMIT_QUEUE_WAIT_S = 2.0

# "Run sample" is synchronous and does not use the queue. It gets its own
# sandbox uids (slots WORKERS .. WORKERS + SAMPLE_RUNNERS - 1) so it never
# shares a process quota with a queue worker. At most this many run at once.
SAMPLE_RUNNERS = int(os.environ.get("JUDGE_SAMPLE_RUNNERS", "2"))
SAMPLE_WAIT_S = 10.0                       # wait this long for a free runner, then 503

# Server-Sent Events: a comment line is sent this often so that proxies keep
# the connection open and a closed browser tab is noticed (the write fails).
SSE_HEARTBEAT_S = 15.0
SSE_RETRY_MS = 2000                        # how long the browser waits before reconnecting

SUBMISSIONS_PAGE_MAX = 100                 # GET /api/submissions?limit=... upper bound
STRESS_SUBMISSIONS = 20                    # POST /api/inspector/stress fires this many

# Accounts. The login cookie holds a random token; the database stores only
# its SHA-256. A login lasts 30 days (or until "Log out").
AUTH_COOKIE = "ocj_auth"
AUTH_TTL_S = 30 * 24 * 3600
# Anyone may create an account (set JUDGE_ALLOW_SIGNUP=0 to close sign-up).
# Admins are made with:  python3 tools/manage_users.py create-admin <name>
ALLOW_SIGNUP = os.environ.get("JUDGE_ALLOW_SIGNUP", "1") == "1"

# Folder for the self-signed TLS certificate (created on first start), so the
# server answers https:// as well as http:// on the same port.
TLS_DIR = os.environ.get("JUDGE_TLS_DIR", os.path.join(os.path.dirname(DB_PATH), "tls"))

# Graceful shutdown: how long running submissions may take to finish.
SHUTDOWN_TIMEOUT_S = 30.0
