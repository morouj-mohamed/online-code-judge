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
