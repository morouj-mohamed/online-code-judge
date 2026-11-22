"""
judge/sandbox.py - run untrusted code with real operating-system limits.

NOTHING here is simulated. For every compile and every test we:

    1. fork()           create a child process
    2. setsid()         give the child its own session / process group
    3. dup2()           connect stdin/stdout/stderr to files
    4. close fds        close every other inherited file descriptor
    5. setrlimit()      CPU, address space, processes, file size, open files
    6. chroot()         hide the real file system (root only)
    7. setresuid()      stop being root (root only)
    8. execve()         replace the child with the user's program

and the parent watches the child with a wall-clock watchdog and collects the
exit status plus resource usage with wait4().

We never use the `subprocess` module. `subprocess` is itself just a wrapper
around fork + exec; using the raw calls lets us place our own code (limits,
chroot, uid change) between fork() and exec().

Every step is reported as an "event" dict:
    {"ts": <unix time>, "pid": <process id>, "step": "<name>", "details": {...}}
A later phase will stream these events to the browser.
"""

import ctypes
import errno
import json
import os
import re
import resource
import select
import shutil
import signal
import sys
import sysconfig
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass

import config
from judge.compare import compare_outputs, difference_message, normalize_checker

# Python 3.12+ warns every time os.fork() is called while other threads exist,
# because a child forked from a multi-threaded process may inherit a lock that
# another thread was holding, and then wait for it forever. Our child is safe
# because it only runs a short, fixed list of system calls and then execve()s:
# it never touches locks that other threads use (no logging, no sqlite, no
# imports). So the warning would only be noise for every single test.
warnings.filterwarnings("ignore", category=DeprecationWarning,
                        message=r".*multi-threaded.*use of fork\(\) may lead to deadlocks.*")

# How long the parent waits for the child to reach execve() before giving up.
SETUP_TIMEOUT_S = 5.0
# How much of the program's stderr we keep for messages and heuristics.
STDERR_HEAD_BYTES = 2000
# How much of the program's stdout we keep (used only for the public sample diff).
STDOUT_HEAD_BYTES = 16 * 1024
# For the first moments after exec() we poll without sleeping so that even a
# program that lives for half a millisecond is sampled a few times.
SPIN_SECONDS = 0.02
POLL_SLEEP_S = 0.001


# ============================================================================
# Errors
# ============================================================================
class SandboxError(Exception):
    """The judge itself failed (not the user's fault): missing gcc, bad setup..."""


class ProblemError(Exception):
    """A problem folder is missing or malformed."""


# ============================================================================
# Events
# ============================================================================
class EventLog:
    """
    Collects lifecycle events and optionally hands each one to a callback
    (a later phase will use the callback to push events to the browser).
    """

    def __init__(self, callback=None):
        self.events = []
        self._callback = callback

    def emit(self, step, pid=None, ts=None, **details):
        event = {
            "ts": round(ts if ts is not None else time.time(), 6),
            "pid": pid if pid is not None else os.getpid(),
            "step": step,
            "details": details,
        }
        self.events.append(event)
        if self._callback is not None:
            try:
                self._callback(event)
            except Exception:
                # A broken listener (for example a closed browser tab) must
                # never break the judging itself.
                pass
        return event


# ============================================================================
# Isolation level: what protection can we really give right now?
# ============================================================================
@dataclass(frozen=True)
class Isolation:
    level: str            # "full", "setuid-only", "chroot-only" or "basic"
    chroot: bool          # will user code run inside a chroot jail?
    drop_privileges: bool  # will user code run as an unprivileged user?
    description: str      # human readable text for the CLI and the UI


_ISOLATION = None
_ISOLATION_LOCK = threading.Lock()


def get_isolation():
    """Return the (cached) isolation level of this machine (thread-safe)."""
    global _ISOLATION
    # Several workers may ask at the same moment. The lock makes sure the probe
    # child is forked only once.
    with _ISOLATION_LOCK:
        if _ISOLATION is None:
            _ISOLATION = _probe_isolation()
        return _ISOLATION


def _probe_isolation():
    """
    chroot() and setuid() are privileged system calls. Instead of guessing
    from the user id, we TRY both in a throw-away child process and see which
    ones the kernel allows (a container may be root yet lack CAP_SYS_CHROOT).
    """
    if os.geteuid() != 0:
        return Isolation(
            "basic", False, False,
            "basic (not root: rlimits + own process group + watchdog + private temp dir; "
            "no chroot, no uid change)",
        )

    probe_dir = tempfile.mkdtemp(prefix="judge-probe-", dir=config.TMP_ROOT)
    code = 0
    try:
        pid = os.fork()
        if pid == 0:
            # ---- child: runs only these two tests, then exits ----
            try:
                try:
                    os.chroot(probe_dir)
                    code |= 1
                except OSError:
                    pass
                try:
                    os.setgroups([])
                    os.setresgid(config.SANDBOX_GID, config.SANDBOX_GID, config.SANDBOX_GID)
                    os.setresuid(config.SANDBOX_UID, config.SANDBOX_UID, config.SANDBOX_UID)
                    code |= 2
                except OSError:
                    pass
            finally:
                # _exit() ends the child immediately. A normal exit() would run
                # the parent's cleanup code a second time.
                os._exit(code)
        _, status = os.waitpid(pid, 0)
        code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else 0
    finally:
        try:
            os.rmdir(probe_dir)
        except OSError:
            pass

    chroot_ok = bool(code & 1)
    setuid_ok = bool(code & 2)
    uid = config.SANDBOX_UID
    if chroot_ok and setuid_ok:
        return Isolation(
            "full", True, True,
            f"full (chroot + setuid {uid} + rlimits + own process group + watchdog)",
        )
    if setuid_ok:
        return Isolation(
            "setuid-only", False, True,
            f"setuid-only (setuid {uid} + rlimits + own process group + watchdog; chroot not permitted)",
        )
    if chroot_ok:
        return Isolation(
            "chroot-only", True, False,
            "chroot-only (chroot + rlimits + own process group + watchdog; cannot drop root)",
        )
    return Isolation(
        "basic", False, False,
        "basic (root but chroot/setuid not permitted: rlimits + own process group + watchdog)",
    )


# ============================================================================
# Small helpers that read /proc (the kernel's window into running processes)
# ============================================================================
_VM_RE = re.compile(rb"^(VmPeak|VmHWM):\s+(\d+) kB", re.M)


def _read_vm(pid):
    """
    Read the peak virtual size (VmPeak) and peak resident size (VmHWM) of a
    LIVE process from /proc/<pid>/status. Returns None if the process is gone
    or already a zombie (a zombie has no memory any more).
    """
    try:
        with open(f"/proc/{pid}/status", "rb") as f:
            data = f.read(4096)
    except OSError:
        return None
    found = {name: int(value) for name, value in _VM_RE.findall(data)}
    if not found:
        return None
    return {"peak_vm_kb": found.get(b"VmPeak", 0), "peak_hwm_kb": found.get(b"VmHWM", 0)}


_CLK_TCK = os.sysconf("SC_CLK_TCK")
GROUP_CPU_CHECK_S = 0.1      # how often the watchdog adds up the CPU time of a whole process group


def _group_cpu_s(pgid):
    """
    CPU seconds used so far by EVERY process of a group: user + system time of
    each member, plus the time of children they already waited for (cutime,
    cstime). Read from /proc/<pid>/stat fields 14-17 (in clock ticks).

    Why: RLIMIT_CPU is per PROCESS. A fork bomb of 32 processes could burn 32 x
    the limit and starve other students' programs on the same machine (they
    would then fail the wall-clock limit through no fault of their own). The
    watchdog uses this sum to stop the whole group once it has had its share.
    """
    ticks = 0
    try:
        names = os.listdir("/proc")
    except OSError:
        return 0.0
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                data = f.read()
        except OSError:
            continue
        rest = data[data.rindex(b")") + 2:].split()
        if len(rest) > 14 and int(rest[2]) == pgid:
            ticks += int(rest[11]) + int(rest[12]) + int(rest[13]) + int(rest[14])
    return ticks / _CLK_TCK


def group_members(pgid):
    """
    List (pid, state) of every process whose process-group id is `pgid`.
    State 'Z' means zombie (already dead, waiting to be reaped).
    """
    members = []
    try:
        names = os.listdir("/proc")
    except OSError:
        return members
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                data = f.read()
        except OSError:
            continue
        # The command name sits in parentheses and may contain spaces, so we
        # split only after the LAST ')'. Then: state, ppid, pgrp, session...
        rest = data[data.rindex(b")") + 2:].split()
        if len(rest) > 2 and int(rest[2]) == pgid:
            members.append((int(name), rest[0].decode()))
    return members


def process_group_alive(pgid):
    """Pids in the group that are still really running (zombies do not count)."""
    return [pid for pid, state in group_members(pgid) if state != "Z"]


def unreaped_children():
    """
    True if this process still has a child that nobody has waited for.
    Used by the tests to prove that the judge leaves no zombies behind.
    """
    try:
        os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        return False   # "no child processes": everything was reaped
    return True


def _kill_group(pgid):
    """Send SIGKILL to every process in a process group."""
    try:
        os.killpg(pgid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return False


_SUBREAPER_SET = False


def _become_subreaper():
    """
    Ask the kernel to make THIS process the adopter of orphaned descendants.

    Normally, when a process dies, its children are adopted by PID 1. If PID 1
    never calls wait(), the dead ones stay as zombies forever. With
    PR_SET_CHILD_SUBREAPER the judge adopts them itself and can reap them.
    (The flag is not inherited by children, so user code does not get it.)
    """
    global _SUBREAPER_SET
    if _SUBREAPER_SET:
        return True
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36
        _SUBREAPER_SET = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        _SUBREAPER_SET = False
    return _SUBREAPER_SET


def _reap_group(pgid, leader):
    """
    Reap the dead members of a process group (everything except the leader).

    The group id stays reserved while the leader is still an un-reaped zombie,
    so we can safely look for the group's members by that number. We wait for
    each member BY PID, never with wait(-1), so we can never steal the exit
    status of a child that belongs to a different submission.
    """
    reaped = 0
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        members = [p for p, _state in group_members(pgid) if p != leader]
        if not members:
            break
        for member in members:
            try:
                done, _status = os.waitpid(member, os.WNOHANG)
                if done == member:
                    reaped += 1
            except ChildProcessError:
                pass   # not our child (yet): its parent is still being killed
        time.sleep(0.001)
    return reaped


def _capture(argv, timeout=30.0):
    """
    Run a TRUSTED helper program (ldd) and return its stdout.
    This is the same fork/dup2/exec pattern, just without limits, because it is
    our own tool and not user code. Used only to prepare the Python runtime.
    """
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            devnull = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull, 0)
            os.dup2(write_end, 1)
            os.dup2(devnull, 2)
            os.close(read_end)
            os.close(write_end)
            os.execv(argv[0], argv)
        finally:
            os._exit(127)
    os.close(write_end)
    chunks = []
    deadline = time.monotonic() + timeout
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                os.kill(pid, signal.SIGKILL)
                break
            ready, _, _ = select.select([read_end], [], [], left)
            if not ready:
                continue
            chunk = os.read(read_end, 65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(read_end)
        os.waitpid(pid, 0)
    return b"".join(chunks).decode("utf-8", errors="replace")


# ============================================================================
# Python runtime for the chroot jail
# ============================================================================
# Inside a chroot the program can only see files under the new root. A static
# C binary needs nothing else. The Python interpreter needs its libraries and
# its standard library, so we build a read-only copy of them once, and give each
# submission its own root made of HARD LINKS to that copy (a hard link is a
# second name for the same file: instant, and it costs no disk space).
_PY_SKIP_DIRS = {
    "test", "tests", "idlelib", "tkinter", "turtledemo", "lib2to3",
    "ensurepip", "site-packages", "dist-packages", "distutils",
}


def _python_interpreter():
    """Absolute, symlink-free path of the real Python interpreter binary."""
    return os.path.realpath(getattr(sys, "_base_executable", sys.executable))


def _walk_files(top):
    """Yield every regular file under `top`, skipping folders we do not need."""
    for dirpath, dirnames, filenames in os.walk(top):
        dirnames[:] = [d for d in dirnames if d not in _PY_SKIP_DIRS and not d.startswith("config-")]
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.isfile(path):   # follows symlinks, skips broken ones
                yield path


def _copy_into(root, absolute_path, source=None):
    """Copy a file so that it appears at `absolute_path` inside the tree `root`."""
    dest = os.path.join(root, absolute_path.lstrip("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(source or absolute_path, dest)


_RUNTIME_LOCK = threading.Lock()


def _python_runtime_cache():
    """Build (once) and return the Python runtime copy. Thread-safe."""
    # Without the lock, two workers judging their first Python submission at the
    # same time would both spend seconds building the same 50 MB tree.
    with _RUNTIME_LOCK:
        return _build_python_runtime_cache()


def _build_python_runtime_cache():
    """Build (once) and return the directory holding the Python runtime copy."""
    interp = _python_interpreter()
    stamp = f"{interp}\n{os.stat(interp).st_mtime_ns}\n"
    name = f"judge-pyruntime-{sys.version_info.major}.{sys.version_info.minor}-{os.getuid()}"
    final = os.path.join(config.TMP_ROOT, name)
    marker = os.path.join(final, ".complete")
    try:
        with open(marker) as f:
            if f.read() == stamp:
                return final
    except OSError:
        pass

    building = tempfile.mkdtemp(prefix=name + ".build-", dir=config.TMP_ROOT)
    try:
        stdlib = sysconfig.get_path("stdlib")
        platstdlib = sysconfig.get_path("platstdlib")
        prefix = os.path.dirname(os.path.dirname(interp))
        # Python finds its standard library relative to the interpreter file:
        #   <prefix>/bin/pythonX.Y  ->  <prefix>/lib/pythonX.Y/os.py
        jail_stdlib = os.path.join(prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}")

        _copy_into(building, interp)
        for path in _walk_files(stdlib):
            rel = os.path.relpath(path, stdlib)
            _copy_into(building, os.path.join(jail_stdlib, rel), source=path)
        if os.path.realpath(platstdlib) != os.path.realpath(stdlib):
            for path in _walk_files(platstdlib):
                rel = os.path.relpath(path, platstdlib)
                _copy_into(building, os.path.join(platstdlib, rel), source=path)

        # Shared libraries: ask the dynamic linker's own tool (ldd) which ones
        # the interpreter and every extension module needs.
        ldd = shutil.which("ldd")
        if ldd is None:
            raise SandboxError("ldd not found: cannot build the Python chroot runtime")
        dynload = os.path.join(jail_stdlib, "lib-dynload")
        targets = [interp] + sorted(
            p for p in _walk_files(os.path.join(building, dynload.lstrip("/"))) if p.endswith(".so")
        )
        libs = set()
        for line in _capture([ldd] + targets).splitlines():
            m = re.search(r"(/\S+)\s+\(0x", line)
            if m and os.path.exists(m.group(1)):
                libs.add(m.group(1))
        for lib in libs:
            _copy_into(building, lib)

        with open(os.path.join(building, ".complete"), "w") as f:
            f.write(stamp)
        try:
            shutil.rmtree(final)
        except OSError:
            pass
        os.rename(building, final)
        return final
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise


def _link_tree(cache, root):
    """Recreate the runtime tree inside `root` using hard links (copy as fallback)."""
    for dirpath, dirnames, filenames in os.walk(cache):
        rel = os.path.relpath(dirpath, cache)
        target_dir = root if rel == "." else os.path.join(root, rel)
        os.makedirs(target_dir, mode=0o755, exist_ok=True)
        for name in filenames:
            if name == ".complete":
                continue
            src = os.path.join(dirpath, name)
            dst = os.path.join(target_dir, name)
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)   # different file system, for example


# ============================================================================
# The child side of fork()
# ============================================================================
def _close_inherited_fds(keep):
    """
    fork() copies EVERY open file descriptor into the child. If we left them
    open, the user's program could read or write our files and pipes.
    We close everything except the descriptors listed in `keep`.
    """
    try:
        fds = [int(name) for name in os.listdir("/proc/self/fd")]
    except OSError:
        fds = range(3, min(os.sysconf("SC_OPEN_MAX"), 65536))
    for fd in fds:
        if fd not in keep:
            try:
                os.close(fd)
            except OSError:
                pass   # includes the fd that listdir() itself used


def _reset_signals():
    """
    execve() keeps signals that are IGNORED and keeps the signal mask.
    Python ignores SIGPIPE and SIGXFSZ at start-up, so without this reset the
    user's program would inherit that: a file-size limit would then fail with an
    error code instead of killing the program. We restore the default action
    for every signal and unblock them all.
    """
    for number in signal.valid_signals():
        try:
            signal.signal(number, signal.SIG_DFL)
        except (OSError, ValueError, RuntimeError, OverflowError):
            pass   # SIGKILL and SIGSTOP can never be changed
    signal.pthread_sigmask(signal.SIG_SETMASK, set())


def _child_main(err_w, argv, env, stdio_fds, limits, chroot_dir, cwd, creds):
    """
    Runs ONLY inside the forked child. It never returns: it either becomes the
    user's program (execve) or exits with _exit().

    The pipe `err_w` is the child's way to talk to the parent. It was created
    with the close-on-exec flag, so the kernel closes it by itself the moment
    execve() succeeds. The parent sees "end of file" and knows exec worked.
    If something fails before that, we write a message into the pipe instead.
    """
    step = "start"

    def say(name, **details):
        message = {"ts": time.time(), "pid": os.getpid(), "step": name, "details": details}
        os.write(err_w, (json.dumps(message) + "\n").encode())

    try:
        # --- 1. own session and process group -------------------------------
        # setsid() makes this process the leader of a NEW session and a NEW
        # process group (its group id equals its pid). Now the parent can kill
        # the program AND everything it forks with a single killpg().
        step = "setsid"
        os.setsid()
        say("setsid", pgid=os.getpgrp(), sid=os.getsid(0))

        # --- 2. redirect stdin / stdout / stderr -----------------------------
        # dup2(old, new) makes file descriptor `new` point to the same open file
        # as `old`. Descriptors 0, 1, 2 are what every program reads/writes.
        step = "dup2"
        os.dup2(stdio_fds[0], 0)
        os.dup2(stdio_fds[1], 1)
        os.dup2(stdio_fds[2], 2)

        # --- 3. close everything else we inherited ---------------------------
        step = "close_fds"
        _close_inherited_fds(keep={0, 1, 2, err_w})
        step = "reset_signals"
        _reset_signals()
        say("stdio_ready", note="fds 0,1,2 redirected, other inherited fds closed, signals reset")

        # --- 4. resource limits (setrlimit) ----------------------------------
        # Limits are inherited through execve(), so they apply to the program.
        # soft == hard for most of them, so the program cannot raise them again.
        step = "setrlimit"
        applied = {}
        if limits.get("cpu_s"):
            # CPU time: at the soft limit the kernel sends SIGXCPU, one second
            # later (hard limit) SIGKILL.
            resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_s"], limits["cpu_s"] + 1))
            applied["RLIMIT_CPU_s"] = limits["cpu_s"]
        if limits.get("fsize_bytes") is not None:
            # Biggest file the program may write (stdout included). Writing
            # past it raises SIGXFSZ.
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits["fsize_bytes"], limits["fsize_bytes"]))
            applied["RLIMIT_FSIZE_mb"] = limits["fsize_bytes"] // (1024 * 1024)
        if limits.get("nofile") is not None:
            # Number of open files.
            resource.setrlimit(resource.RLIMIT_NOFILE, (limits["nofile"], limits["nofile"]))
            applied["RLIMIT_NOFILE"] = limits["nofile"]
        if limits.get("nproc") is not None:
            # Number of processes+threads of this user. fork() fails with EAGAIN
            # once the limit is reached, which defuses fork bombs.
            resource.setrlimit(resource.RLIMIT_NPROC, (limits["nproc"], limits["nproc"]))
            applied["RLIMIT_NPROC"] = limits["nproc"]
        # Core dump size 0: a crashing program must not write a huge core file.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        applied["RLIMIT_CORE"] = 0
        say("limits_applied", **applied)

        # --- 5. chroot: change what "/" means --------------------------------
        if chroot_dir:
            step = "chroot"
            os.chroot(chroot_dir)
            # chdir() right after chroot() is mandatory: otherwise the old
            # working directory would still point outside the jail.
            os.chdir("/")
        step = "chdir"
        os.chdir(cwd)
        if chroot_dir:
            say("chroot_done", new_root=os.path.basename(chroot_dir), cwd=os.getcwd())

        # --- 6. stop being root ----------------------------------------------
        if creds:
            step = "setuid"
            uid, gid = creds
            os.setgroups([])                 # forget all supplementary groups
            os.setresgid(gid, gid, gid)      # real, effective and saved gid
            os.setresuid(uid, uid, uid)      # real, effective and saved uid
            try:
                os.setresuid(0, 0, 0)        # must FAIL: root cannot come back
            except PermissionError:
                pass
            else:
                raise RuntimeError("privilege drop failed: process could become root again")
            say("privileges_dropped", uid=os.getuid(), euid=os.geteuid(), gid=os.getgid())

        # --- 7. address-space limit, applied LAST ----------------------------
        # RLIMIT_AS caps the total virtual memory. The child is still a copy of
        # the Python judge, which already maps a lot of memory. If we lowered
        # the limit early, Python itself could fail to allocate the few bytes it
        # needs for the steps above. So we build the message first, set the
        # limit, and only then write it and call execve().
        step = "setrlimit_as"
        as_bytes = limits.get("as_bytes")
        as_message = None
        if as_bytes:
            as_message = (json.dumps({
                "ts": time.time(), "pid": os.getpid(), "step": "address_space_limit_applied",
                "details": {"RLIMIT_AS_mb": as_bytes // (1024 * 1024)},
            }) + "\n").encode()
            resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
            os.write(err_w, as_message)

        # --- 8. become the user's program ------------------------------------
        # execve() replaces this process image. On success it never returns, the
        # close-on-exec pipe closes, and the parent knows we are running.
        step = "execve"
        try:
            os.execve(argv[0], argv, env)
        except OSError as exc:
            os.write(err_w, b"EXECFAIL %d\n" % exc.errno)
            os._exit(126)
    except BaseException as exc:   # noqa: B902 - the child must never fall back into parent code
        try:
            text = f"{step}: {exc!r}".encode("utf-8", "replace")[:500]
            os.write(err_w, b"SETUPFAIL " + text + b"\n")
        except BaseException:
            pass
        os._exit(125)


# ============================================================================
# The parent side of fork(): start the child, watch it, collect the result
# ============================================================================
def _spawn(argv, *, events, label, stdin_path, stdout_path, stderr_path,
           limits, wall_limit_s, cwd, chroot_dir=None, creds=None, env=None):
    """
    Start `argv` in a sandboxed child, wait for it, and return a result dict.

    Always cleans up: the child's process group is killed and the child is
    reaped (wait4) even if an exception such as Ctrl-C happens in the middle.
    """
    env = env or {"PATH": "/usr/bin:/bin"}
    # Convert to bytes now: less work (and less memory use) in the child.
    argv_b = [os.fsencode(a) for a in argv]
    env_b = {os.fsencode(k): os.fsencode(v) for k, v in env.items()}

    stdin_fd = stdout_fd = stderr_fd = err_r = err_w = None
    pid = None
    reaped = False
    try:
        # These files are opened by the PARENT (which may be root) before fork,
        # so the unprivileged child needs no access to the directory they are in.
        stdin_fd = os.open(stdin_path, os.O_RDONLY)
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        # A pipe for the child to report setup problems. Python creates pipes
        # with the close-on-exec flag already set.
        err_r, err_w = os.pipe()

        # ---------------------------------------------------------------- fork
        # fork() creates a copy of this process. It returns 0 in the child and
        # the child's pid in the parent.
        pid = os.fork()
        if pid == 0:
            try:
                _child_main(err_w, argv_b, env_b, (stdin_fd, stdout_fd, stderr_fd),
                            limits, chroot_dir, cwd, creds)
            finally:
                os._exit(127)   # unreachable in practice; never return into the parent's code

        # ------------------------------------------------------------- parent
        events.emit("fork", pid, label=label, child_pid=pid, argv=[os.path.basename(argv[0])] + list(argv[1:]))
        # The child owns its own copies of these descriptors now.
        for fd in (stdin_fd, stdout_fd, stderr_fd, err_w):
            os.close(fd)
        stdin_fd = stdout_fd = stderr_fd = err_w = None

        # Read what the child reports until the pipe closes (= execve worked).
        buffer = b""
        deadline = time.monotonic() + SETUP_TIMEOUT_S
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SandboxError("child did not reach execve() in time")
            ready, _, _ = select.select([err_r], [], [], left)
            if not ready:
                continue
            chunk = os.read(err_r, 4096)
            if not chunk:
                break
            buffer += chunk

        exec_errno = None
        setup_error = None
        for line in buffer.split(b"\n"):
            if not line:
                continue
            if line.startswith(b"{"):
                msg = json.loads(line)
                details = dict(msg["details"])
                details["label"] = label
                events.emit(msg["step"], msg["pid"], msg["ts"], **details)
            elif line.startswith(b"EXECFAIL "):
                exec_errno = int(line.split()[1])
            elif line.startswith(b"SETUPFAIL "):
                setup_error = line[len(b"SETUPFAIL "):].decode("utf-8", "replace")

        if setup_error is not None or exec_errno is not None:
            # The child is exiting on its own; reap it so it does not become a zombie.
            os.waitpid(pid, 0)
            reaped = True
            if setup_error is not None:
                raise SandboxError(f"sandbox setup failed in child ({label}): {setup_error}")
            events.emit("exec_failed", pid, label=label, errno=exec_errno, error=os.strerror(exec_errno))
            return {
                "pid": pid, "pgid": pid, "wall_s": 0.0, "cpu_s": 0.0, "user_s": 0.0, "sys_s": 0.0,
                "maxrss_kb": 0, "peak_hwm_kb": None, "peak_vm_kb": None, "exited": False,
                "exit_code": None, "signal": None, "core_dumped": False, "watchdog_fired": False,
                "group_cpu_exceeded": False, "exec_errno": exec_errno, "stray_killed": 0, "orphans_reaped": 0,
            }

        events.emit("exec", pid, label=label, note="execve succeeded (close-on-exec pipe reached end of file)")

        # ----------------------------------------------------------- watchdog
        # Wait for the child WITHOUT blocking forever. waitid() with WNOWAIT
        # tells us that the child has finished but leaves it as a zombie, so
        # its pid cannot be reused yet while we clean up its process group.
        t_exec = time.monotonic()
        deadline = t_exec + wall_limit_s
        spin_until = t_exec + SPIN_SECONDS
        peak_vm = peak_hwm = 0
        fired = False
        group_cpu_exceeded = False
        # The whole group may use the CPU limit plus a small grace (the leader's
        # own RLIMIT_CPU normally ends a single-process program first, with SIGXCPU).
        cpu_budget = limits.get("cpu_s")
        next_group_check = t_exec + GROUP_CPU_CHECK_S
        while True:
            if os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None:
                break
            sample = _read_vm(pid)
            if sample:
                peak_vm = max(peak_vm, sample["peak_vm_kb"])
                peak_hwm = max(peak_hwm, sample["peak_hwm_kb"])
            now = time.monotonic()
            if cpu_budget and not fired and now >= next_group_check:
                next_group_check = now + GROUP_CPU_CHECK_S
                used = _group_cpu_s(pid)
                if used > cpu_budget + 0.5:
                    fired = group_cpu_exceeded = True
                    _kill_group(pid)
                    events.emit("group_cpu_limit", pid, label=label, cpu_s=round(used, 2), budget_s=cpu_budget,
                                action="SIGKILL sent to the whole process group")
                    continue
            if not fired and now >= deadline:
                # The program used too much wall-clock time. SIGKILL cannot be
                # caught or ignored. Sending it to the group also kills children.
                fired = True
                _kill_group(pid)
                events.emit("watchdog_fired", pid, label=label, wall_limit_s=round(wall_limit_s, 3),
                            action="SIGKILL sent to the whole process group")
            elif fired and now > deadline + 5.0:
                raise SandboxError("child could not be killed (stuck in the kernel?)")
            if now >= spin_until:
                time.sleep(POLL_SLEEP_S)
        wall_s = time.monotonic() - t_exec

        # The leader has exited but is still a zombie, so its process group id
        # is still reserved. Anything else left in the group is a stray.
        members = [(p, state) for p, state in group_members(pid) if p != pid]
        strays = [p for p, state in members if state != "Z"]
        orphans = 0
        if strays:
            _kill_group(pid)
            events.emit("stray_processes_killed", pid, label=label, count=len(strays), pids=strays[:10])
        if members:
            # Dead descendants were adopted by us (subreaper). Reap them now.
            orphans = _reap_group(pid, leader=pid)
            events.emit("orphans_reaped", pid, label=label, count=orphans)

        # wait4() reaps the zombie (the kernel frees its process-table slot) and
        # returns the exit status plus the resource usage of the child.
        _, status, usage = os.wait4(pid, 0)
        reaped = True

        exited = os.WIFEXITED(status)
        signaled = os.WIFSIGNALED(status)
        exit_code = os.WEXITSTATUS(status) if exited else None
        term_signal = os.WTERMSIG(status) if signaled else None
        core = os.WCOREDUMP(status) if signaled else False
        if exited:
            events.emit("exited", pid, label=label, exit_code=exit_code)
        else:
            events.emit("killed_by_signal", pid, label=label, signal=signal.Signals(term_signal).name,
                        number=term_signal, core_dumped=core)
        cpu_s = usage.ru_utime + usage.ru_stime
        events.emit("reaped", pid, label=label, cpu_user_ms=round(usage.ru_utime * 1000, 2),
                    cpu_sys_ms=round(usage.ru_stime * 1000, 2), wall_ms=round(wall_s * 1000, 2),
                    maxrss_kb=usage.ru_maxrss)

        return {
            "pid": pid, "pgid": pid, "wall_s": wall_s, "cpu_s": cpu_s,
            "user_s": usage.ru_utime, "sys_s": usage.ru_stime, "maxrss_kb": usage.ru_maxrss,
            "peak_hwm_kb": peak_hwm or None, "peak_vm_kb": peak_vm or None,
            "exited": exited, "exit_code": exit_code, "signal": term_signal, "core_dumped": core,
            "watchdog_fired": fired and not group_cpu_exceeded, "group_cpu_exceeded": group_cpu_exceeded,
            "exec_errno": None, "stray_killed": len(strays), "orphans_reaped": orphans,
        }
    finally:
        # ---- guaranteed cleanup, also when an exception is in flight --------
        if pid is not None and not reaped:
            _kill_group(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)   # reap: no zombie, whatever happened
            except ChildProcessError:
                pass
        for fd in (stdin_fd, stdout_fd, stderr_fd, err_r, err_w):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


# ============================================================================
# Verdict logic
# ============================================================================
def _signal_text(number):
    return f"{signal.Signals(number).name} ({signal.strsignal(number)})"


def _measured_memory(result, floor_kb):
    """
    Peak memory of the PROGRAM, in KB, and where the number came from.

    Gotcha: after fork()+exec() the kernel's ru_maxrss never goes below the
    size of the forked copy of the judge (here: `floor_kb`). So a tiny program
    would look as big as the judge. We measured that floor with a do-nothing
    program. If ru_maxrss is above it, it is the real peak. If not, we use the
    peak that we sampled from /proc while the program ran.
    """
    raw = result["maxrss_kb"]
    if raw > floor_kb * 1.05 + 256:
        return raw, "ru_maxrss"
    if result["peak_hwm_kb"]:
        return result["peak_hwm_kb"], "sampled /proc VmHWM"
    return None, "below measurement floor"


def _memory_exhausted(result, mem_kb, eff, as_limit_kb, stderr_head):
    """
    Did the program die because it ran out of memory?

    When RLIMIT_AS is hit, malloc() returns NULL or mmap() fails, and the
    program usually crashes with a signal or prints an error. The kernel does
    not say "out of memory", so we look for the clues it leaves behind.
    """
    if mem_kb is not None and mem_kb > eff["memory_kb"]:
        return True, f"peak memory {mem_kb // 1024} MB is over the {eff['memory_kb'] // 1024} MB limit"
    if mem_kb is not None and mem_kb >= config.MLE_RSS_RATIO * as_limit_kb:
        return True, f"died while using {mem_kb // 1024} MB of a {as_limit_kb // 1024} MB limit"
    if result["peak_vm_kb"] and result["peak_vm_kb"] >= config.MLE_VM_RATIO * as_limit_kb:
        return True, "address space limit reached (virtual size hit RLIMIT_AS)"
    if any(sig in stderr_head for sig in config.MLE_STDERR_SIGNATURES):
        return True, "allocation failed (out-of-memory message on stderr)"
    return False, ""


def classify_run(result, eff, as_limit_kb, mem_kb, stderr_head):
    """
    Turn the raw facts about a finished run into (verdict, message).
    Returns (None, "") when the program ran fine and only its output remains
    to be compared.
    """
    time_ms = result["cpu_s"] * 1000.0

    # 0. execve itself failed. ENOMEM means the program's static memory did not
    #    even fit under RLIMIT_AS, for example `static char a[1 << 30];`.
    if result["exec_errno"] is not None:
        if result["exec_errno"] == errno.ENOMEM:
            return config.MEMORY_LIMIT_EXCEEDED, "program could not be loaded: its static memory exceeds the limit"
        raise SandboxError(f"execve failed: {os.strerror(result['exec_errno'])}")

    # 1. The watchdog had to kill it: the whole group used too much CPU
    #    (e.g. a fork bomb: many processes, each under its own RLIMIT_CPU) ...
    if result.get("group_cpu_exceeded"):
        return (config.TIME_LIMIT_EXCEEDED,
                f"the program and the processes it started used more than {eff['time_ms']} ms of CPU time together")
    #    ... or too much wall-clock time (sleeping, blocked, or starved).
    if result["watchdog_fired"]:
        return (config.TIME_LIMIT_EXCEEDED,
                f"killed by the watchdog after {result['wall_s']:.2f} s of wall-clock time "
                f"({time_ms:.0f} ms of CPU)")

    abnormal = False
    reason = ""
    if result["signal"] is not None:
        sig = result["signal"]
        abnormal = True
        if sig == signal.SIGXCPU:
            return config.TIME_LIMIT_EXCEEDED, f"CPU time limit reached (SIGXCPU), limit {eff['time_ms']} ms"
        if sig == signal.SIGKILL and time_ms >= eff["time_ms"]:
            return config.TIME_LIMIT_EXCEEDED, "killed after using all its CPU time"
        reason = f"terminated by signal {_signal_text(sig)}"
        if sig == signal.SIGXFSZ:
            reason = "wrote more than the allowed file size (SIGXFSZ)"
    elif result["exit_code"] != 0:
        abnormal = True
        reason = f"exited with code {result['exit_code']}"

    # 2. Memory: a program that died abnormally may have run out of memory.
    if abnormal:
        exhausted, why = _memory_exhausted(result, mem_kb, eff, as_limit_kb, stderr_head)
        if exhausted:
            return config.MEMORY_LIMIT_EXCEEDED, why
    elif mem_kb is not None and mem_kb > eff["memory_kb"]:
        return config.MEMORY_LIMIT_EXCEEDED, f"peak memory {mem_kb // 1024} MB is over the limit"

    # 3. CPU time measured by the kernel, compared with the exact millisecond limit.
    if time_ms > eff["time_ms"]:
        return config.TIME_LIMIT_EXCEEDED, f"used {time_ms:.0f} ms of CPU, limit {eff['time_ms']} ms"

    # 4. Plain crashes.
    if abnormal:
        lines = stderr_head.decode("utf-8", "replace").strip().splitlines()
        extra = ""
        if lines:
            # A Python traceback ends with the useful line ("ZeroDivisionError: ...").
            # For other programs the first line is the most useful one.
            line = lines[-1] if lines[0].startswith("Traceback") else lines[0]
            extra = f": {line[:120]}"
        return config.RUNTIME_ERROR, reason + extra

    return None, ""


# ============================================================================
# The Sandbox: one private working area for one submission
# ============================================================================
class Sandbox:
    """
    Layout of the private temp directory (T = Sandbox.tmp):

        T/build/   source file + compiler output   (owned by the sandbox user)
        T/io/      stdout / stderr of every run    (root only, opened by the parent)
        T/root/    the chroot jail for user code
            prog | main.py (+ runtime libs for Python)   read-only for the user
            work/  the only place the program may write; wiped before each test

    Use it as a context manager so cleanup() always runs:
        with Sandbox("cpp", 1000, 256, events) as sb: ...
    """

    def __init__(self, language, time_limit_ms, memory_limit_mb, events=None, uid_slot=0):
        if language not in config.LANGUAGES:
            raise SandboxError(f"unsupported language: {language}")
        self.language = language
        self.events = events or EventLog()
        self.iso = get_isolation()
        self.subreaper = _become_subreaper()
        self.eff = config.effective_limits(language, time_limit_ms, memory_limit_mb)
        self.tmp = None
        # Each worker passes its own slot number, so user code of different
        # workers runs under different uids and their process quotas stay apart.
        self.uid_slot = uid_slot
        self._creds = ((config.SANDBOX_UID + uid_slot, config.SANDBOX_GID + uid_slot)
                       if self.iso.drop_privileges else None)

    # ------------------------------------------------------------- lifecycle
    def __enter__(self):
        try:
            self._create_dirs()
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        return False

    def _chown(self, path):
        """Give a path to the sandbox user (only meaningful when we are root)."""
        if self._creds:
            os.chown(path, self._creds[0], self._creds[1])

    def _create_dirs(self):
        # mkdtemp creates a directory only we can enter (mode 0700) and picks a
        # unique random name, so two submissions never share a folder.
        self.tmp = tempfile.mkdtemp(prefix="judge-", dir=config.TMP_ROOT)
        os.chmod(self.tmp, 0o755)   # the sandbox user must be able to walk through it
        self.build_dir = os.path.join(self.tmp, "build")
        self.io_dir = os.path.join(self.tmp, "io")
        self.root_dir = os.path.join(self.tmp, "root")
        self.work_dir = os.path.join(self.root_dir, "work")
        os.mkdir(self.build_dir, 0o700)
        self._chown(self.build_dir)
        os.mkdir(self.io_dir, 0o700)
        os.mkdir(self.root_dir, 0o755)
        os.mkdir(self.work_dir, 0o700)
        self._chown(self.work_dir)
        self.events.emit("sandbox_created", directory=self.tmp, isolation=self.iso.level,
                         language=self.language, subreaper=self.subreaper,
                         uid=self._creds[0] if self._creds else None, time_limit_ms=self.eff["time_ms"],
                         memory_limit_mb=self.eff["memory_kb"] // 1024)

    def cleanup(self):
        """Delete the temp directory. Safe to call twice; never raises."""
        if self.tmp is None:
            return
        path, self.tmp = self.tmp, None
        # Directories that user code created may have strange permissions;
        # make them enterable first, then delete the whole tree.
        for dirpath, _dirs, _files in os.walk(path):
            try:
                os.chmod(dirpath, 0o700)
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=True)
        self.events.emit("cleanup", directory=path, removed=not os.path.exists(path))

    # --------------------------------------------------------------- compile
    def compile(self, source_text):
        """
        Compile the source. Returns {"ok", "message", "time_ms", "skipped"}.
        C and C++ are compiled by running gcc/g++ in the same kind of sandboxed
        child as user programs (limits, own process group, unprivileged user):
        a compiler reads untrusted text, so it must not run with extra rights.
        """
        lang = config.LANGUAGES[self.language]
        source_path = os.path.join(self.build_dir, lang["source_name"])
        with open(source_path, "w", encoding="utf-8", newline="") as f:
            f.write(source_text)
        self._chown(source_path)

        if lang["compiler"] is None:
            # Interpreted language: just put the script into the jail.
            script = os.path.join(self.root_dir, "main.py")
            shutil.copyfile(source_path, script)
            os.chmod(script, 0o644)
            if self.iso.chroot:
                _link_tree(_python_runtime_cache(), self.root_dir)
            self.events.emit("compile_skipped", language=self.language, note="interpreted language")
            return {"ok": True, "message": "", "time_ms": 0, "skipped": True}

        argv = list(lang["compiler"])
        compiler = shutil.which(argv[0])
        if compiler is None:
            raise SandboxError(f"compiler not found: {argv[0]}")
        argv[0] = compiler
        out_path = os.path.join(self.io_dir, "compile.out")
        err_path = os.path.join(self.io_dir, "compile.err")
        self.events.emit("compile_start", command=" ".join([os.path.basename(argv[0])] + argv[1:]))
        result = _spawn(
            argv, events=self.events, label="compile",
            stdin_path=os.devnull, stdout_path=out_path, stderr_path=err_path,
            limits={
                "cpu_s": config.COMPILE_TIME_LIMIT_S + 1,
                "as_bytes": config.COMPILE_MEMORY_MB * 1024 * 1024,
                "fsize_bytes": config.COMPILE_OUTPUT_FSIZE_MB * 1024 * 1024,
                "nofile": config.COMPILE_NOFILE_LIMIT,
                # No NPROC limit here: gcc starts cc1, as and ld as child
                # processes, and on a normal desktop the user already owns more
                # processes than any small limit would allow.
            },
            wall_limit_s=config.COMPILE_TIME_LIMIT_S,
            cwd=self.build_dir, chroot_dir=None, creds=self._creds,
            env={"PATH": "/usr/bin:/bin", "TMPDIR": self.build_dir, "HOME": self.build_dir, "LANG": "C"},
        )
        time_ms = round(result["wall_s"] * 1000)

        def read_text(path):
            with open(path, "rb") as f:
                data = f.read(config.COMPILE_MESSAGE_MAX_CHARS * 4)
            text = data.decode("utf-8", errors="replace").replace(self.build_dir + "/", "")
            return text[:config.COMPILE_MESSAGE_MAX_CHARS]

        if result["exec_errno"] is not None:
            raise SandboxError(f"could not start the compiler: {os.strerror(result['exec_errno'])}")
        if result["watchdog_fired"]:
            return {"ok": False, "time_ms": time_ms, "skipped": False,
                    "message": f"Compilation timed out after {config.COMPILE_TIME_LIMIT_S} s"}
        if result["signal"] is not None:
            return {"ok": False, "time_ms": time_ms, "skipped": False,
                    "message": f"The compiler was killed by {_signal_text(result['signal'])}\n"
                               + read_text(err_path)}
        if result["exit_code"] != 0:
            return {"ok": False, "time_ms": time_ms, "skipped": False,
                    "message": read_text(err_path) or "The compiler failed without a message"}

        built = os.path.join(self.build_dir, "prog")
        if not os.path.isfile(built):
            return {"ok": False, "time_ms": time_ms, "skipped": False,
                    "message": "The compiler produced no program"}
        # Copy the binary into the jail. The copy is made by the parent, so it is
        # owned by root (or by us): the program cannot modify its own file.
        target = os.path.join(self.root_dir, "prog")
        shutil.copyfile(built, target)
        os.chmod(target, 0o755)
        # Warnings (exit code 0) are kept: they are useful for the student.
        return {"ok": True, "message": read_text(err_path), "time_ms": time_ms, "skipped": False}

    # ------------------------------------------------------------------ run
    def _reset_work_dir(self):
        """Give every test a fresh, empty working directory."""
        for dirpath, _dirs, _files in os.walk(self.work_dir):
            try:
                os.chmod(dirpath, 0o700)
            except OSError:
                pass
        shutil.rmtree(self.work_dir, ignore_errors=True)
        os.mkdir(self.work_dir, 0o700)
        self._chown(self.work_dir)

    def _run_command(self):
        """The command line of the user's program as seen from INSIDE the jail."""
        if self.iso.chroot:
            base, cwd = "", "/work"
        else:
            base, cwd = self.root_dir, self.work_dir
        if self.language == "python":
            # -I isolated mode (ignore PYTHON* environment and user site dir)
            # -S do not import the site module (faster start, nothing extra)
            # -B do not try to write .pyc files
            return [_python_interpreter(), "-I", "-S", "-B", f"{base}/main.py"], cwd
        return [f"{base}/prog"], cwd

    def _maxrss_floor_kb(self):
        """
        Measure the ru_maxrss floor (see _measured_memory) by running a program
        that does nothing through the very same fork()+exec() path.
        """
        true_path = shutil.which("true")
        if true_path is None:
            return 0
        out = os.path.join(self.io_dir, "floor.out")
        result = _spawn([true_path], events=EventLog(), label="calibration",
                        stdin_path=os.devnull, stdout_path=out, stderr_path=out,
                        limits={}, wall_limit_s=5.0, cwd=self.io_dir, env={"PATH": "/usr/bin:/bin"})
        return result["maxrss_kb"]

    def run_test(self, index, input_path, expected_path, checker=None):
        """
        Run the program on one test and return a result dict (see below).
        checker: how outputs are compared ("exact", "tokens" or "float", see compare.py).
        """
        self._reset_work_dir()
        floor_kb = self._maxrss_floor_kb()
        stdout_path = os.path.join(self.io_dir, "stdout.txt")
        stderr_path = os.path.join(self.io_dir, "stderr.txt")
        argv, cwd = self._run_command()
        as_limit_kb = self.eff["memory_kb"]

        result = _spawn(
            argv, events=self.events, label=f"test {index}",
            stdin_path=input_path, stdout_path=stdout_path, stderr_path=stderr_path,
            limits={
                "cpu_s": self.eff["cpu_s"],
                "as_bytes": as_limit_kb * 1024,
                "fsize_bytes": config.FSIZE_LIMIT_MB * 1024 * 1024,
                "nofile": config.NOFILE_LIMIT,
                "nproc": config.NPROC_LIMIT,
            },
            wall_limit_s=self.eff["wall_s"], cwd=cwd,
            chroot_dir=self.root_dir if self.iso.chroot else None,
            creds=self._creds,
            env={"PATH": "/usr/bin:/bin", "HOME": cwd, "LANG": "C.UTF-8", "PYTHONIOENCODING": "utf-8"},
        )

        with open(stderr_path, "rb") as f:
            stderr_head = f.read(STDERR_HEAD_BYTES)
        # The first bytes of the program's output. Only the web API's "run on the
        # PUBLIC sample" feature shows it (to draw a diff); for hidden tests it is
        # never stored or sent anywhere.
        with open(stdout_path, "rb") as f:
            stdout_head = f.read(STDOUT_HEAD_BYTES)
        mem_kb, mem_source = _measured_memory(result, floor_kb)
        verdict, message = classify_run(result, self.eff, as_limit_kb, mem_kb, stderr_head)
        if verdict is None:
            comparison = compare_outputs(expected_path, stdout_path, checker)
            if comparison["equal"]:
                verdict, message = config.ACCEPTED, "output matches"
            else:
                verdict = config.WRONG_ANSWER
                message = difference_message(comparison)

        return {
            "test": index,
            "verdict": verdict,
            "message": message,
            "time_ms": round(result["cpu_s"] * 1000),
            "wall_ms": round(result["wall_s"] * 1000),
            "memory_kb": mem_kb,
            "memory_source": mem_source,
            "exit_code": result["exit_code"],
            "signal": signal.Signals(result["signal"]).name if result["signal"] else None,
            "stderr": stderr_head.decode("utf-8", "replace")[:500],
            "stdout": stdout_head.decode("utf-8", "replace"),
            "pgid": result["pgid"],
            "stray_processes_killed": result["stray_killed"],
            "orphans_reaped": result["orphans_reaped"],
        }


# ============================================================================
# Problems and the judging loop
# ============================================================================
def load_problem(slug_or_path):
    """Load problems/<slug>/ (or any folder path) into a dict."""
    path = slug_or_path if os.path.isdir(slug_or_path) else os.path.join(config.PROBLEMS_DIR, slug_or_path)
    meta_path = os.path.join(path, "meta.json")
    if not os.path.isfile(meta_path):
        raise ProblemError(f"problem not found: {slug_or_path} (no meta.json in {path})")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    tests_dir = os.path.join(path, "tests")
    numbers = sorted(int(m.group(1)) for m in
                     (re.fullmatch(r"(\d+)\.in", n) for n in os.listdir(tests_dir)) if m)
    if not numbers:
        raise ProblemError(f"problem {slug_or_path} has no tests")
    tests = []
    for n in numbers:
        in_path = os.path.join(tests_dir, f"{n}.in")
        out_path = os.path.join(tests_dir, f"{n}.out")
        if not os.path.isfile(out_path):
            raise ProblemError(f"missing {out_path}")
        tests.append({"index": n, "input": in_path, "expected": out_path})
    return {
        "slug": meta.get("slug", os.path.basename(path)),
        "title": meta.get("title", os.path.basename(path)),
        "time_limit_ms": int(meta.get("time_limit_ms", config.DEFAULT_TIME_LIMIT_MS)),
        "memory_limit_mb": int(meta.get("memory_limit_mb", config.DEFAULT_MEMORY_LIMIT_MB)),
        "checker": normalize_checker(meta.get("checker")),
        "tests": tests,
    }


def judge_submission(problem, language, source_text, on_event=None, on_test=None, uid_slot=0):
    """
    Judge one submission completely and return a result dict:

      verdict, message, problem, language, isolation, isolation_detail,
      compile {ok, message, time_ms}, tests [per-test results],
      total_tests, tests_run, time_ms (max), memory_kb (max),
      failed_test (number or None), events [lifecycle events]

    Stops at the first test that is not Accepted.

    on_event(event)   is called for every lifecycle event as it happens.
    on_test(outcome)  is called right after each test finishes (the workers use
                      it to save every test result to the database immediately).
    uid_slot          which sandbox uid to use (worker number, see config.py).
    """
    if isinstance(problem, str):
        problem = load_problem(problem)
    events = EventLog(on_event)
    iso = get_isolation()
    result = {
        "verdict": config.PENDING, "message": "", "problem": problem["slug"], "language": language,
        "isolation": iso.level, "isolation_detail": iso.description,
        "compile": None, "tests": [], "total_tests": len(problem["tests"]), "tests_run": 0,
        "time_ms": 0, "memory_kb": None, "failed_test": None, "events": events.events,
    }
    events.emit("judging_started", problem=problem["slug"], language=language, verdict=config.RUNNING)

    with Sandbox(language, problem["time_limit_ms"], problem["memory_limit_mb"], events,
                 uid_slot=uid_slot) as sandbox:
        compiled = sandbox.compile(source_text)
        result["compile"] = compiled
        if not compiled["ok"]:
            result["verdict"] = config.COMPILATION_ERROR
            result["message"] = compiled["message"]
        else:
            result["verdict"] = config.ACCEPTED
            result["message"] = "all tests passed"
            for test in problem["tests"]:
                events.emit("test_start", test=test["index"])
                outcome = sandbox.run_test(test["index"], test["input"], test["expected"],
                                           problem.get("checker"))
                events.emit("test_result", test=test["index"], verdict=outcome["verdict"],
                            time_ms=outcome["time_ms"], memory_kb=outcome["memory_kb"])
                result["tests"].append(outcome)
                if on_test is not None:
                    on_test(outcome)
                result["tests_run"] += 1
                result["time_ms"] = max(result["time_ms"], outcome["time_ms"])
                if outcome["memory_kb"] is not None:
                    result["memory_kb"] = max(result["memory_kb"] or 0, outcome["memory_kb"])
                if outcome["verdict"] != config.ACCEPTED:
                    result["verdict"] = outcome["verdict"]
                    result["message"] = f"test {test['index']}: {outcome['message']}"
                    result["failed_test"] = test["index"]
                    break
    events.emit("final_verdict", verdict=result["verdict"], failed_test=result["failed_test"])
    return result
