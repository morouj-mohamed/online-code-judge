"""
judge/concepts.py - the "OS concept" explanations shown by the Inspector page.

Each concept lists the exact functions that implement it. The file and line
numbers are NOT typed by hand: inspect.getsourcelines() reads them from the
running code, so they stay correct when the code changes.
"""

import inspect
import os

import config
from judge import contests, events, queue, sandbox, similarity, worker

ROOT = config.BASE_DIR

# (concept id, title, one-paragraph explanation, [(object, why), ...])
CONCEPTS = [
    ("process_management", "Process management",
     "Every compile and every test is a new process. The judge calls fork() to clone itself, the child "
     "turns into the user's program with execve(), and the parent waits for it with wait4(), which also "
     "returns how much CPU time and memory the child used. A finished child that nobody waits for would "
     "stay a 'zombie'; the judge reaps every one.",
     [(sandbox._spawn, "fork(), the watchdog loop, wait4() and the rusage numbers"),
      (sandbox._child_main, "everything the child does between fork() and execve()"),
      (sandbox._reap_group, "reaps every process of the group, so no zombie is left"),
      (sandbox._become_subreaper, "PR_SET_CHILD_SUBREAPER: orphans of the user program come back to the judge")]),
    ("signals", "Signals",
     "Signals are how the kernel (or another process) interrupts a process. RLIMIT_CPU makes the kernel "
     "send SIGXCPU; a bad memory access gives SIGSEGV; division by zero SIGFPE; writing past RLIMIT_FSIZE "
     "SIGXFSZ. The watchdog uses killpg(pgid, SIGKILL), which cannot be caught or ignored: when the wall "
     "clock runs out, and when the WHOLE process group has used its CPU share (a fork bomb of many "
     "processes, each under its own RLIMIT_CPU). The parent decodes the wait status with WIFEXITED / "
     "WIFSIGNALED / WTERMSIG.",
     [(sandbox._kill_group, "killpg(): SIGKILL to the whole process group"),
      (sandbox._reset_signals, "the child resets signal handlers so the program starts clean"),
      (sandbox._group_cpu_s, "adds up the CPU time of every process in the group, from /proc"),
      (sandbox.classify_run, "turns 'killed by signal N' into a verdict")]),
    ("resource_limits", "Resource limits",
     "setrlimit() puts hard caps that the KERNEL enforces on a process and its children: CPU seconds, "
     "address space, number of processes, file size, open files, core dump size. The program cannot "
     "raise them back. Languages get multipliers so a correct Python solution is not judged like C.",
     [(sandbox._child_main, "the setrlimit() calls, RLIMIT_AS applied last"),
      (config.effective_limits, "per-language time and memory multipliers")]),
    ("ipc", "Inter-process communication",
     "The child talks to the parent through a pipe created with the close-on-exec flag: it writes one "
     "line per setup step, and when execve() succeeds the kernel closes the pipe, so the parent reads "
     "end-of-file and knows the program really started. Inside the judge, an event broker moves events "
     "from worker threads to every open browser stream (Server-Sent Events).",
     [(sandbox._child_main, "say(): JSON lines written into the close-on-exec pipe"),
      (sandbox._spawn, "the parent reads the pipe until end-of-file"),
      (events.EventBroker.publish, "one event delivered to every subscriber"),
      (events.EventBroker.subscribe, "a browser stream subscribes, with replay from a sequence number")]),
    ("concurrency", "Concurrency",
     "A pool of worker threads judges several submissions at the same time. Each worker blocks in "
     "wait4() while its child runs, which releases Python's global lock, so the threads really run in "
     "parallel. Each worker uses its own sandbox uid, so their process limits do not mix.",
     [(worker.WorkerPool._worker_loop, "the loop of one worker thread"),
      (worker.WorkerPool._process, "claims a submission, judges it, stores every result")]),
    ("synchronization", "Synchronization",
     "The queue between the web server (producers) and the workers (consumers) is the classic bounded "
     "buffer: a mutex protects the list, a semaphore counts empty slots and another counts full slots. "
     "Taking the semaphore BEFORE the mutex, and never holding two locks, rules out deadlock. A database "
     "compare-and-swap (UPDATE ... WHERE status = 'Pending') makes sure exactly one worker claims each "
     "submission.",
     [(queue.BoundedQueue.put, "empty_slots.acquire() -> mutex -> append -> full_slots.release()"),
      (queue.BoundedQueue.get, "full_slots.acquire() -> mutex -> popleft -> empty_slots.release()"),
      (queue.BoundedQueue.close, "wakes every blocked thread on shutdown")]),
    ("memory", "Memory",
     "RLIMIT_AS limits the virtual address space: allocations beyond it fail. The real usage is the "
     "resident set size (RSS). The kernel's ru_maxrss also counts the copy of the judge the child was "
     "before execve(), so the judge measures that 'floor' and samples /proc/<pid>/status (VmHWM) while "
     "the program runs, to report the program's own peak.",
     [(sandbox._measured_memory, "chooses ru_maxrss or the sampled VmHWM"),
      (sandbox._memory_exhausted, "recognizes a crash caused by running out of memory"),
      (sandbox._read_vm, "reads VmPeak / VmHWM from /proc")]),
    ("file_system", "File system",
     "Each submission gets a private temporary directory. The program's stdin/stdout/stderr are files "
     "connected with dup2(); every other inherited file descriptor is closed. The jail for Python is "
     "built with hard links (a second name for the same file), which costs no disk space.",
     [(sandbox.Sandbox._create_dirs, "private build / io / root folders with tight permissions"),
      (sandbox._close_inherited_fds, "closes every descriptor except 0, 1, 2 and the pipe"),
      (sandbox._link_tree, "builds the jail with hard links")]),
    ("isolation", "Isolation",
     "chroot() changes what '/' means for the program, so the real file system is invisible. Then the "
     "child drops root with setgroups / setresgid / setresuid to an unprivileged uid (one per worker). "
     "The judge probes at start-up which of these the kernel allows and reports the level honestly.",
     [(sandbox._probe_isolation, "tries chroot and setuid in a throw-away child"),
      (sandbox._child_main, "chroot(), then giving up root"),
      (similarity.normalize, "(bonus) the same idea for code: strip names to compare structure"),
      (contests.phase, "(bonus) time windows for contests")]),
]

# Which concepts each Inspector panel demonstrates.
PANELS = {
    "workers": ["concurrency", "process_management"],
    "queue": ["synchronization", "concurrency"],
    "timeline": ["process_management", "signals", "ipc", "file_system"],
    "limits": ["resource_limits", "memory"],
    "isolation": ["isolation", "file_system"],
    "log": ["ipc", "concurrency"],
    "stress": ["concurrency", "synchronization"],
}


def _location(obj):
    """Repository-relative file, first line and qualified name of a function."""
    target = inspect.unwrap(obj)
    path = inspect.getsourcefile(target)
    _lines, start = inspect.getsourcelines(target)
    return {"file": os.path.relpath(path, ROOT).replace(os.sep, "/"), "line": start,
            "function": f"{target.__qualname__}()"}


def catalog():
    """All concepts with their source locations, plus the panel -> concepts map."""
    items = []
    for cid, title, text, refs in CONCEPTS:
        sources = []
        for obj, why in refs:
            loc = _location(obj)
            if config.REPOSITORY_URL:
                loc["url"] = f"{config.REPOSITORY_URL}/blob/main/{loc['file']}#L{loc['line']}"
            loc["why"] = why
            sources.append(loc)
        items.append({"id": cid, "title": title, "text": text, "sources": sources})
    return {"concepts": items, "panels": PANELS}
