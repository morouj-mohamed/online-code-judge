#!/usr/bin/env python3
"""
tools/run_verdict_tests.py - prove that every verdict can really be produced.

Each program in tests/ is judged against the problem "sum-two" and its verdict is
compared with the expected one. After every run we also check the cleanup rules:

  * no process of the program's process group is still alive
  * this process has no un-reaped child (no zombie)
  * the temporary directory was deleted

Usage (from the project root):
    python3 tools/run_verdict_tests.py              # the verdict programs
    python3 tools/run_verdict_tests.py --reference  # also the reference solutions
                                                    # of all 6 problems, in every language
Exit status: 0 if every row passed, 1 otherwise.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from judge import sandbox  # noqa: E402

TESTS_DIR = os.path.join(config.BASE_DIR, "tests")
REFERENCE_DIR = os.path.join(config.BASE_DIR, "tools", "reference")

A, WA, TLE, MLE, RE, CE = (config.ACCEPTED, config.WRONG_ANSWER, config.TIME_LIMIT_EXCEEDED,
                           config.MEMORY_LIMIT_EXCEEDED, config.RUNTIME_ERROR,
                           config.COMPILATION_ERROR)

# (file in tests/, language, expected verdict, expected failing test or None,
#  text that must appear in the final message or "")
VERDICT_CASES = [
    ("correct.cpp",         "cpp",    A,   None, ""),
    ("slow_but_correct.cpp", "cpp",   A,   None, ""),
    ("wrong_answer.cpp",    "cpp",    WA,  5,    ""),
    ("infinite_loop.c",     "c",      TLE, 1,    ""),
    ("memory_hog.cpp",      "cpp",    MLE, 1,    ""),
    ("segfault.c",          "c",      RE,  1,    "SIGSEGV"),
    ("divide_by_zero.c",    "c",      RE,  1,    "SIGFPE"),
    ("fork_bomb.c",         "c",      TLE, 1,    ""),
    ("file_write_bomb.c",   "c",      RE,  1,    "SIGXFSZ"),
    ("compile_error.cpp",   "cpp",    CE,  None, "undefined_function"),
    ("correct.py",          "python", A,   None, ""),
    ("infinite_loop.py",    "python", TLE, 1,    ""),
    ("memory_hog.py",       "python", MLE, 1,    ""),
    ("runtime_error.py",    "python", RE,  1,    "ZeroDivisionError"),
]

EXTENSIONS = {"cpp": "cpp", "python": "py"}
REFERENCE_PROBLEMS = ["sum-two", "max-array", "reverse-string", "prime-check", "fib-mod", "average"]


def temp_dirs():
    """Names of judge temp directories that exist right now (not the Python runtime cache)."""
    return {n for n in os.listdir(config.TMP_ROOT)
            if n.startswith("judge-") and not n.startswith("judge-pyruntime") and not n.startswith("judge-probe")}


def run_case(path, language, problem):
    """Judge one file, then check the cleanup rules. Returns (result, cleanup_problems)."""
    with open(path, encoding="utf-8") as f:
        source = f.read()
    before = temp_dirs()
    result = sandbox.judge_submission(problem, language, source)
    problems = []
    for test in result["tests"]:
        alive = sandbox.process_group_alive(test["pgid"])
        if alive:
            problems.append(f"{len(alive)} process(es) still alive in group {test['pgid']}")
    if sandbox.unreaped_children():
        problems.append("zombie child found")
    leftover = temp_dirs() - before
    if leftover:
        problems.append(f"temp dir not deleted: {sorted(leftover)[0]}")
    return result, problems


def fmt_mem(kb):
    return "n/a" if kb is None else f"{kb / 1024:.1f} MB"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", action="store_true",
                        help="also judge the reference solutions of all 6 problems")
    args = parser.parse_args()

    iso = sandbox.get_isolation()
    print(f"Isolation level: {iso.description}\n")

    rows = []   # each row: list of strings, last one is "PASS"/"FAIL"
    failures = []

    def record(name, language, problem, path, expected, expected_test, needle):
        result, cleanup = run_case(path, language, problem)
        failed_test = result["failed_test"]
        good = (result["verdict"] == expected
                and failed_test == expected_test
                and needle in result["message"]
                and not cleanup)
        rows.append([name, language, expected, result["verdict"],
                     "-" if failed_test is None else str(failed_test),
                     f"{result['time_ms']} ms", fmt_mem(result["memory_kb"]),
                     "ok" if not cleanup else "LEAK", "PASS" if good else "FAIL"])
        if not good:
            failures.append((name, language, expected, result, cleanup))

    for filename, language, expected, expected_test, needle in VERDICT_CASES:
        record(filename, language, "sum-two", os.path.join(TESTS_DIR, filename),
               expected, expected_test, needle)

    if args.reference:
        for slug in REFERENCE_PROBLEMS:
            for language in ("cpp", "python"):
                filename = f"{slug}.{EXTENSIONS[language]}"
                record(f"ref:{slug}", language, slug, os.path.join(REFERENCE_DIR, filename), A, None, "")

    header = ["Program", "Lang", "Expected", "Got", "Fail@", "Time", "Memory", "Clean", "Result"]
    table = [header] + rows
    widths = [max(len(r[i]) for r in table) for i in range(len(header))]

    def line(row):
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) + " |"

    rule = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    print(rule)
    print(line(header))
    print(rule)
    for row in rows:
        print(line(row))
    print(rule)
    passed = sum(1 for r in rows if r[-1] == "PASS")
    print(f"\n{passed}/{len(rows)} passed   (isolation: {iso.level})")

    for name, language, expected, result, cleanup in failures:
        print(f"\nFAILED {name} [{language}]: expected {expected}, got {result['verdict']}")
        print(f"  message : {result['message']}")
        for problem in cleanup:
            print(f"  cleanup : {problem}")
        for test in result["tests"][-1:]:
            print(f"  last test: exit={test['exit_code']} signal={test['signal']} "
                  f"time={test['time_ms']} ms mem={fmt_mem(test['memory_kb'])} ({test['memory_source']})")
            if test["stderr"].strip():
                print("  stderr  : " + test["stderr"].strip().splitlines()[0][:200])
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
