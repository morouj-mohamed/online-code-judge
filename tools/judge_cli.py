#!/usr/bin/env python3
"""
tools/judge_cli.py - judge one source file from the command line.

Usage:
    python3 tools/judge_cli.py <problem-slug> <language> <source-file> [--events] [--json]

    language : c | cpp | python
    --events : also print every lifecycle event (fork, limits, exec, wait4 ...)
    --json   : print the full result as JSON and nothing else

Exit status: 0 if the verdict is Accepted, 1 otherwise, 2 on a usage or judge error.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from judge import sandbox  # noqa: E402

COLORS = {
    config.ACCEPTED: "32",               # green
    config.WRONG_ANSWER: "31",           # red
    config.TIME_LIMIT_EXCEEDED: "33",    # yellow
    config.MEMORY_LIMIT_EXCEEDED: "35",  # magenta
    config.RUNTIME_ERROR: "31",
    config.COMPILATION_ERROR: "36",      # cyan
}


def paint(text, verdict):
    if sys.stdout.isatty() and verdict in COLORS:
        return f"\033[{COLORS[verdict]}m{text}\033[0m"
    return text


def format_memory(kb):
    if kb is None:
        return "   n/a"
    return f"{kb / 1024:6.1f} MB"


def event_printer():
    """Return a callback that prints events as they happen, with relative time."""
    start = []

    def show(event):
        if not start:
            start.append(event["ts"])
        details = " ".join(f"{k}={v}" for k, v in event["details"].items())
        print(f"    [+{event['ts'] - start[0]:8.4f}s] pid={event['pid']:<7} {event['step']:<28} {details}")
    return show


def main():
    parser = argparse.ArgumentParser(description="Judge one source file against a problem.")
    parser.add_argument("problem", help="problem slug, for example sum-two")
    parser.add_argument("language", choices=sorted(config.LANGUAGES))
    parser.add_argument("source", help="path of the source file")
    parser.add_argument("--events", action="store_true", help="print lifecycle events")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()

    try:
        with open(args.source, encoding="utf-8") as f:
            source = f.read()
        problem = sandbox.load_problem(args.problem)
    except (OSError, sandbox.ProblemError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    quiet = args.json
    if not quiet:
        iso = sandbox.get_isolation()
        eff = config.effective_limits(args.language, problem["time_limit_ms"], problem["memory_limit_mb"])
        print(f"Problem  : {problem['title']} ({problem['slug']}), {len(problem['tests'])} tests")
        print(f"Language : {config.LANGUAGES[args.language]['label']}")
        print(f"Limits   : {eff['time_ms']} ms CPU, {eff['memory_kb'] // 1024} MB "
              f"(base {problem['time_limit_ms']} ms / {problem['memory_limit_mb']} MB x language multiplier)")
        print(f"Isolation: {iso.description}")
        print()

    printer = event_printer() if (args.events and not quiet) else None
    try:
        result = sandbox.judge_submission(problem, args.language, source, on_event=printer)
    except sandbox.SandboxError as exc:
        print(f"judge error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["verdict"] == config.ACCEPTED else 1

    if result["compile"] and not result["compile"]["skipped"]:
        state = "OK" if result["compile"]["ok"] else "FAILED"
        print(f"Compile  : {state} ({result['compile']['time_ms']} ms)")
        if result["compile"]["message"].strip():
            print("\n".join("    " + line for line in result["compile"]["message"].strip().splitlines()[:15]))
        print()

    for test in result["tests"]:
        label = f"Test {test['test']:>2}/{result['total_tests']}"
        verdict = paint(f"{test['verdict']:<22}", test["verdict"])
        print(f"{label}  {verdict} {test['time_ms']:>5} ms {format_memory(test['memory_kb'])}"
              f"   {'' if test['verdict'] == config.ACCEPTED else test['message']}")

    if result["failed_test"] is not None:
        skipped = result["total_tests"] - result["tests_run"]
        if skipped:
            print(f"(stopped at the first failing test, {skipped} test(s) not run)")
    print()
    print(f"Verdict  : {paint(result['verdict'], result['verdict'])}")
    if result["verdict"] == config.COMPILATION_ERROR:
        print("           (see the compiler message above)")
    elif result["tests_run"]:
        print(f"Passed   : {result['tests_run'] - (1 if result['failed_test'] else 0)}/{result['total_tests']}   "
              f"max time {result['time_ms']} ms   max memory {format_memory(result['memory_kb']).strip()}")
    return 0 if result["verdict"] == config.ACCEPTED else 1


if __name__ == "__main__":
    sys.exit(main())
