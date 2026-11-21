#!/usr/bin/env python3
"""
tools/api_smoke_test.py - exercise every endpoint of the running API.

Start the server first (python3 app.py, or docker compose up), then:

    python3 tools/api_smoke_test.py                       # http://127.0.0.1:5000
    python3 tools/api_smoke_test.py --base http://localhost:5000
    python3 tools/api_smoke_test.py --skip-stress         # faster, no 20-submission burst

Only the standard library is used (urllib + http.cookiejar), so it runs on any
machine with Python 3, inside or outside Docker.

What it checks
    * every endpoint answers with the right status code and JSON shape
    * validation and JSON errors (bad language, unknown problem, 64 KB limit, ...)
    * Submit returns at once with Pending, then the SSE stream delivers
      Running -> one "test" event per test -> the final verdict
    * submissions are private to the anonymous session (cookie)
    * the per-session rate limit answers 429 with Retry-After
    * HIDDEN TESTS NEVER LEAK: every response body (SSE included) is scanned
      for the content of the hidden test files, after submitting programs that
      deliberately try to print their hidden input to stdout and stderr.

Exit status 0 if every check passed, 1 otherwise.
"""

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBLEMS_DIR = os.path.join(ROOT, "problems")
TESTS_DIR = os.path.join(ROOT, "tests")

FINAL = {"Accepted", "Wrong Answer", "Time Limit Exceeded", "Memory Limit Exceeded",
         "Runtime Error", "Compilation Error", "Judge Error"}

# Tries to leak: prints its hidden input as the "answer" (-> Wrong Answer, and
# the judge's internal message would show "expected ..., got <input>").
LEAK_STDOUT_PY = """import sys
data = sys.stdin.read()
a, b = map(int, data.split())
if abs(a) + abs(b) < 1000:
    print(a + b)          # pass the small tests
else:
    print(data.strip())   # leak attempt: print the hidden input
"""

# Tries to leak through stderr: copies the hidden input to stderr, then crashes
# (-> Runtime Error, whose internal message includes the first stderr line).
LEAK_STDERR_PY = """import sys
data = sys.stdin.read()
a, b = map(int, data.split())
if abs(a) + abs(b) < 1000:
    print(a + b)
else:
    sys.stderr.write(data)
    sys.stderr.flush()
    raise SystemExit(3)
"""

WRONG_SAMPLE_PY = "print(42)\n"


class Client:
    """A tiny HTTP client with its own cookie jar (= its own anonymous session)."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.bodies = []          # every response body, for the leak scan

    def request(self, method, path, body=None, raw=None, content_type="application/json",
                timeout=60):
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = content_type
        elif raw is not None:
            data = raw
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                status, resp_headers, text = resp.status, dict(resp.headers), resp.read().decode()
        except urllib.error.HTTPError as err:
            status, resp_headers, text = err.code, dict(err.headers), err.read().decode()
        self.bodies.append(text)
        try:
            payload = json.loads(text) if text else None
        except ValueError:
            payload = None
        return status, payload, resp_headers

    def stream(self, path, until_end=True, timeout=120):
        """Read an SSE stream. Returns a list of (event, data) until "end" arrives."""
        req = urllib.request.Request(self.base + path, headers={"Accept": "text/event-stream"})
        events = []
        deadline = time.monotonic() + timeout
        with self.opener.open(req, timeout=timeout) as resp:
            name, data_lines, raw = None, [], []
            while time.monotonic() < deadline:
                line = resp.readline()
                if not line:
                    break
                text = line.decode()
                raw.append(text)
                text = text.rstrip("\n")
                if text == "":
                    if name is not None:
                        payload = json.loads("\n".join(data_lines)) if data_lines else None
                        events.append((name, payload))
                        if until_end and name == "end":
                            break
                    name, data_lines = None, []
                elif text.startswith("event:"):
                    name = text[6:].strip()
                elif text.startswith("data:"):
                    data_lines.append(text[5:].strip())
        self.bodies.append("".join(raw))
        return events


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------
RESULTS = []


def check(name, ok, detail=""):
    ok = bool(ok)
    RESULTS.append(ok)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail else ""))
    return ok


def is_error(payload, code):
    return (isinstance(payload, dict) and isinstance(payload.get("error"), dict)
            and payload["error"].get("code") == code and "message" in payload["error"])


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def hidden_tokens():
    """
    Tokens that appear in hidden test files but NOT in anything public
    (statement, sample) of the same problem. Short tokens like "3" appear
    everywhere by chance, so only tokens of 6+ characters are used.
    """
    tokens = set()
    for slug in os.listdir(PROBLEMS_DIR):
        folder = os.path.join(PROBLEMS_DIR, slug)
        tests = os.path.join(folder, "tests")
        if not os.path.isdir(tests):
            continue
        public = " ".join(read(os.path.join(folder, n)) for n in
                          ("statement.md", "sample.in", "sample.out", "meta.json")
                          if os.path.exists(os.path.join(folder, n)))
        public_tokens = set(re.findall(r"[-\w]+", public))
        for name in os.listdir(tests):
            for token in re.findall(r"[-\w]+", read(os.path.join(tests, name))):
                if len(token) >= 6 and token not in public_tokens:
                    tokens.add(token)
    return tokens


def follow(client, sid, since):
    """Stream one submission until it ends. Returns (events, final submission event)."""
    events = client.stream(f"/api/stream?submission={sid}&since={since}")
    finals = [d for n, d in events if n == "submission" and d.get("status") in FINAL]
    return events, (finals[-1] if finals else None)


def main():
    parser = argparse.ArgumentParser(description="Smoke test for the judge API.")
    parser.add_argument("--base", default=os.environ.get("JUDGE_URL", "http://127.0.0.1:5000"))
    parser.add_argument("--skip-stress", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()

    alice = Client(args.base)     # the main session
    bob = Client(args.base)       # a second, unrelated session

    print(f"Testing {args.base}\n")
    try:
        status, payload, _ = alice.request("GET", "/api")
    except urllib.error.URLError as err:
        print(f"Cannot reach the server: {err}. Start it with: python3 app.py")
        return 1

    # ------------------------------------------------------------ 1. problems
    print("[1] Problems")
    check("GET /api lists the endpoints", status == 200 and "endpoints" in payload)
    status, payload, headers = alice.request("GET", "/api/problems")
    slugs = [p["slug"] for p in payload["problems"]] if status == 200 else []
    check("GET /api/problems -> 200 with 5 problems", status == 200 and len(slugs) == 5, ", ".join(slugs))
    check("the anonymous session cookie is set",
          any(c.name == "ocj_session" for c in alice.jar), "ocj_session")
    check("problem list has no test data",
          all(set(p) <= {"slug", "index", "title", "difficulty", "tags", "time_limit_ms", "memory_limit_mb", "tests_count",
                         "limits_by_language"} for p in payload["problems"]))
    status, detail, _ = alice.request("GET", "/api/problems/sum-two")
    check("GET /api/problems/sum-two -> statement + sample",
          status == 200 and detail["sample_input"] and detail["sample_output"] and detail["statement"])
    check("problem detail exposes only the sample (no tests, no paths)",
          not ({"tests", "input", "expected"} & set(detail))
          and "/problems/" not in json.dumps(detail))
    check("Python gets larger limits than C++",
          detail["limits_by_language"]["python"]["time_ms"] > detail["limits_by_language"]["cpp"]["time_ms"])
    status, payload, _ = alice.request("GET", "/api/problems/no-such-problem")
    check("unknown problem -> 404 JSON error", status == 404 and is_error(payload, "problem_not_found"))
    status, payload, _ = alice.request("GET", "/api/problems/..%2F..%2Fetc")
    check("path tricks in the slug -> 404", status == 404)

    # ------------------------------------------------------------ 2. validation
    print("[2] Validation and JSON errors")
    correct_cpp = read(os.path.join(TESTS_DIR, "correct.cpp"))
    cases = [
        ("unknown language", {"problem": "sum-two", "language": "java", "code": "x"}, 400, "unsupported_language"),
        ("unknown problem", {"problem": "nope", "language": "cpp", "code": "x"}, 400, "unknown_problem"),
        ("missing fields", {"problem": "sum-two"}, 400, "missing_fields"),
        ("empty code", {"problem": "sum-two", "language": "cpp", "code": "   "}, 400, "empty_code"),
        ("code over 64 KB", {"problem": "sum-two", "language": "cpp", "code": "/" * (64 * 1024 + 1)},
         413, "code_too_large"),
    ]
    for name, body, want_status, want_code in cases:
        status, payload, _ = alice.request("POST", "/api/submit", body=body)
        check(f"{name} -> {want_status} {want_code}", status == want_status and is_error(payload, want_code))
    status, payload, _ = alice.request("POST", "/api/submit", raw=b"problem=sum-two",
                                       content_type="application/x-www-form-urlencoded")
    check("non-JSON body -> 415", status == 415 and is_error(payload, "unsupported_media_type"))
    status, payload, _ = alice.request("POST", "/api/submit", raw=b"{not json",
                                       content_type="application/json")
    check("broken JSON -> 400", status == 400 and is_error(payload, "invalid_json"))
    status, payload, _ = alice.request("POST", "/api/submit", raw=b"x" * (200 * 1024),
                                       content_type="application/json")
    check("huge request body -> 413", status == 413 and is_error(payload, "request_too_large"))
    status, payload, _ = alice.request("GET", "/api/submit")
    check("wrong method -> 405 JSON error", status == 405 and is_error(payload, "method_not_allowed"))
    status, payload, _ = alice.request("GET", "/api/nothing-here")
    check("unknown URL -> 404 JSON error", status == 404 and is_error(payload, "not_found"))

    # ------------------------------------------------------------ 3. run sample
    print("[3] Run sample (synchronous)")
    t0 = time.monotonic()
    status, payload, _ = alice.request("POST", "/api/run-sample",
                                       body={"problem": "sum-two", "language": "cpp", "code": correct_cpp})
    check("correct.cpp on the sample -> Accepted",
          status == 200 and payload["verdict"] == "Accepted",
          f"{payload.get('time_ms')} ms, {payload.get('memory_kb')} KB, {time.monotonic() - t0:.1f} s")
    status, payload, _ = alice.request("POST", "/api/run-sample",
                                       body={"problem": "sum-two", "language": "python", "code": WRONG_SAMPLE_PY})
    check("wrong program on the sample -> Wrong Answer with expected vs got (sample is public)",
          status == 200 and payload["verdict"] == "Wrong Answer" and "expected" in payload["message"],
          payload.get("message"))
    status, payload, _ = alice.request("POST", "/api/run-sample", body={
        "problem": "sum-two", "language": "cpp", "code": read(os.path.join(TESTS_DIR, "compile_error.cpp"))})
    check("compile error on run-sample -> Compilation Error with compiler output",
          status == 200 and payload["verdict"] == "Compilation Error" and "error" in payload["compile_output"])

    # ------------------------------------------------------------ 4. submit + SSE
    print("[4] Submit + live SSE stream")
    t0 = time.monotonic()
    status, sub, _ = alice.request("POST", "/api/submit",
                                   body={"problem": "sum-two", "language": "cpp", "code": correct_cpp})
    elapsed = time.monotonic() - t0
    check("POST /api/submit -> 202 Pending immediately",
          status == 202 and sub["status"] == "Pending" and isinstance(sub["id"], int) and elapsed < 1.5,
          f"id {sub.get('id') if sub else None}, answered in {elapsed * 1000:.0f} ms")
    events, final = follow(alice, sub["id"], sub["seq"])
    names = [n for n, _ in events]
    statuses = [d["status"] for n, d in events if n == "submission"]
    tests = [d for n, d in events if n == "test"]
    judge_steps = {d["step"] for n, d in events if n == "judge"}
    print("      stream: " + " ".join(
        (f"{n}:{d['status']}" if n == "submission" else f"test{d['test']}:{d['verdict']}"
         if n == "test" else n) for n, d in events if n in ("hello", "snapshot", "submission", "test", "end")))
    check("stream starts with hello and ends with end", names[0] == "hello" and names[-1] == "end")
    check("status goes Pending -> Running -> Accepted",
          statuses[:1] == ["Pending"] and "Running" in statuses and statuses[-1] == "Accepted",
          " -> ".join(dict.fromkeys(statuses)))
    check("one test event per test, in order, as they finished",
          final is not None and [t["test"] for t in tests] == list(range(1, final["tests_total"] + 1)),
          f"{len(tests)} test events")
    check("judge lifecycle events are streamed",
          {"fork", "limits_applied", "exec", "reaped"} <= judge_steps,
          ", ".join(s for s in ("fork", "setsid", "limits_applied", "chroot_done",
                                "privileges_dropped", "exec", "exited", "reaped") if s in judge_steps))
    late = alice.stream(f"/api/stream?submission={sub['id']}")
    check("a stream opened after the end still gets the final verdict",
          [n for n, _ in late][-2:] == ["submission", "end"] and late[-2][1]["status"] == "Accepted")

    status, sample_run, _ = alice.request("POST", f"/api/submissions/{sub['id']}/sample-run")
    check("POST /api/submissions/<id>/sample-run -> real output on the PUBLIC sample",
          status == 200 and sample_run["verdict"] == "Accepted"
          and sample_run["stdout"].strip() == sample_run["expected_output"].strip()
          and sample_run["input"] == detail["sample_input"])
    status, payload, _ = bob.request("POST", f"/api/submissions/{sub['id']}/sample-run")
    check("another session cannot sample-run it", status == 404)

    # ------------------------------------------------------------ 5. leak attempts
    print("[5] Hidden tests never leak")
    leak_ids = []
    for label, code, want in (("prints its hidden input", LEAK_STDOUT_PY, "Wrong Answer"),
                              ("copies hidden input to stderr and crashes", LEAK_STDERR_PY, "Runtime Error")):
        status, s, _ = alice.request("POST", "/api/submit",
                                     body={"problem": "sum-two", "language": "python", "code": code})
        _events, fin = follow(alice, s["id"], s["seq"])
        leak_ids.append(s["id"])
        check(f"program that {label} -> {want}",
              fin is not None and fin["status"] == want, fin and f"{fin['status']}: {fin['message']}")
    for sid in leak_ids:
        alice.request("GET", f"/api/submissions/{sid}?events=1")

    # ------------------------------------------------------------ 6. submissions
    print("[6] Submission history and privacy")
    status, payload, _ = alice.request("GET", "/api/submissions")
    ids = [s["id"] for s in payload["submissions"]] if status == 200 else []
    check("GET /api/submissions lists this session's submissions",
          status == 200 and {sub["id"], *leak_ids} <= set(ids), f"{len(ids)} rows")
    status, payload, _ = alice.request("GET", f"/api/submissions/{sub['id']}?events=1")
    check("GET /api/submissions/<id> -> per-test results, source and events",
          status == 200 and len(payload["tests"]) == payload["tests_total"]
          and payload["source"] == correct_cpp and payload["events"])
    status, payload, _ = bob.request("GET", "/api/submissions")
    check("another session sees none of them", status == 200 and not (set(ids) & {
        s["id"] for s in payload["submissions"]}))
    status, payload, _ = bob.request("GET", f"/api/submissions/{sub['id']}")
    check("another session gets 404 for the detail", status == 404 and is_error(payload, "submission_not_found"))
    status, payload, _ = bob.request("GET", f"/api/stream?submission={sub['id']}")
    check("another session cannot stream it", status == 404)

    # ------------------------------------------------------------ 7. inspector
    print("[7] Inspector")
    status, state, _ = alice.request("GET", "/api/inspector/state")
    check("GET /api/inspector/state -> workers, queue, isolation",
          status == 200 and state["workers"] and "capacity" in state["queue"] and state["isolation"]["level"],
          f"{len(state['workers'])} workers, queue {state['queue']['length']}/{state['queue']['capacity']}, "
          f"isolation {state['isolation']['level']}")
    limits = (state.get("last_limits") or {}).get("values", {})
    check("last RLIMIT values applied are reported",
          "RLIMIT_CPU_s" in limits and "RLIMIT_AS_mb" in limits and "RLIMIT_NPROC" in limits,
          ", ".join(f"{k}={v}" for k, v in sorted(limits.items())))

    # ------------------------------------------------------------ 8. rate limit
    print("[8] Rate limit")
    carol = Client(args.base)
    quick = read(os.path.join(TESTS_DIR, "correct.py"))
    codes = []
    retry_after = None
    for _ in range(11):
        status, payload, headers = carol.request(
            "POST", "/api/submit", body={"problem": "sum-two", "language": "python", "code": quick})
        codes.append(status)
        if status == 429:
            retry_after = headers.get("Retry-After")
    check("11th submit in a minute -> 429 rate_limited with Retry-After",
          codes[:10] == [202] * 10 and codes[10] == 429 and retry_after,
          f"codes {codes[0]}x10 then {codes[10]}, Retry-After {retry_after}")

    # ------------------------------------------------------------ 9. stress
    if not args.skip_stress:
        print("[9] Stress: 20 submissions at once")
        status, payload, _ = alice.request("POST", "/api/inspector/stress")
        check("POST /api/inspector/stress -> 202, 20 queued",
              status == 202 and payload["submitted"] + payload["rejected"] == 20,
              f"{payload.get('submitted')} queued, {payload.get('rejected')} rejected")
        status2, payload2, _ = alice.request("POST", "/api/inspector/stress")
        check("a second burst right away -> 429", status2 == 429 and is_error(payload2, "rate_limited"))
        max_queue, busy_seen = 0, 0
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            _, st, _ = alice.request("GET", "/api/inspector/state")
            max_queue = max(max_queue, st["queue"]["length"])
            busy_seen = max(busy_seen, sum(w["state"] == "busy" for w in st["workers"]))
            if st["outstanding"] == 0:
                break
            time.sleep(0.5)
        check("the pool drained the burst", st["outstanding"] == 0,
              f"max queue length {max_queue}, max busy workers {busy_seen}")
        wrong = []
        for item in payload["submissions"]:
            _, row, _ = alice.request("GET", f"/api/submissions/{item['id']}")
            if row["status"] != item["expected"] or row["failed_test"] != item["expected_failed_test"]:
                wrong.append(f"{item['program']}: {row['status']}")
        check("every stress submission got its expected verdict", not wrong, "; ".join(wrong))

    # ------------------------------------------------------------ 10. pages
    print("[10] HTML pages")
    for path, marker in (("/", "page-problems.js"), ("/problems/sum-two", "page-problem.js"),
                         ("/submissions", "page-submissions.js"),
                         (f"/submissions/{sub['id']}", "page-result.js")):
        req = urllib.request.Request(args.base + path)
        with alice.opener.open(req, timeout=30) as resp:
            html = resp.read().decode()
            csp = resp.headers.get("Content-Security-Policy", "")
        alice.bodies.append(html)
        check(f"GET {path} -> page with its module and a CSP",
              marker in html and "script-src 'self'" in csp)
    try:
        alice.opener.open(urllib.request.Request(args.base + "/problems/no-such-problem"), timeout=30)
        missing = 200
    except urllib.error.HTTPError as err:
        missing = err.code
    check("unknown problem page -> 404 page", missing == 404)
    for asset in ("/static/vendor/codemirror.js", "/static/fonts/Inter-Variable.woff2", "/static/css/tokens.css"):
        with alice.opener.open(urllib.request.Request(args.base + asset), timeout=30) as resp:
            ok = resp.status == 200 and len(resp.read()) > 1000
        check(f"GET {asset} is served locally (works offline)", ok)

    # ------------------------------------------------------------ 11. leak scan
    print("[11] Leak scan over every response body")
    tokens = hidden_tokens()
    seen = set()
    for body in alice.bodies + bob.bodies + carol.bodies:
        seen.update(re.findall(r"[-\w]+", body))
    leaked = sorted(tokens & seen)
    check(f"none of the {len(tokens)} hidden-test tokens appeared in "
          f"{len(alice.bodies) + len(bob.bodies) + len(carol.bodies)} responses",
          not leaked, ", ".join(leaked[:5]))

    passed = sum(RESULTS)
    print(f"\n{passed}/{len(RESULTS)} checks passed in {time.monotonic() - started:.1f} s")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
