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
import io
import json
import os
import re
import secrets
import subprocess
import sys
import time
import zipfile
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
                timeout=60, headers=None):
        data = None
        headers = dict(headers or {})
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


    def upload(self, path, filename, data):
        """POST one file as multipart/form-data (field name "file")."""
        boundary = "----smoke" + secrets.token_hex(8)
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                f"Content-Type: application/zip\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", path, raw=body, content_type=f"multipart/form-data; boundary={boundary}")


def make_zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def make_admin():
    """
    Create (or reset) the account "smoke-admin" with a random password, using
    tools/manage_users.py on this machine. Works when the smoke test runs where
    the server's database is (e.g. docker compose exec). Returns the password,
    or None if that is not possible.
    """
    password = secrets.token_urlsafe(18)
    env = dict(os.environ, JUDGE_ADMIN_PASSWORD=password)
    script = os.path.join(ROOT, "tools", "manage_users.py")
    for command in ("create-admin", "set-password", "promote"):
        result = subprocess.run([sys.executable, script, command, "smoke-admin"], env=env,
                                capture_output=True, text=True)
        if result.returncode != 0:
            return None
    return password


def demote_admin():
    script = os.path.join(ROOT, "tools", "manage_users.py")
    subprocess.run([sys.executable, script, "demote", "smoke-admin"], capture_output=True)


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
    check("GET /api/problems -> 200 with 6 problems", status == 200 and len(slugs) == 6, ", ".join(slugs))
    check("the anonymous session cookie is set",
          any(c.name == "ocj_session" for c in alice.jar), "ocj_session")
    check("problem list has no test data",
          all(set(p) <= {"slug", "index", "title", "difficulty", "tags", "time_limit_ms", "memory_limit_mb", "tests_count",
                         "limits_by_language", "checker", "checker_note"} for p in payload["problems"]))
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

    # ------------------------------------------------------------ 11. accounts
    print("[11] Accounts")
    dave = Client(args.base)
    quick_py = read(os.path.join(TESTS_DIR, "correct.py"))
    _, anon_sub, _ = dave.request("POST", "/api/submit", body={"problem": "sum-two", "language": "python", "code": quick_py})
    follow(dave, anon_sub["id"], anon_sub["seq"])
    name = "smoke_" + secrets.token_hex(4)
    password = secrets.token_urlsafe(12)
    status, payload, _ = dave.request("POST", "/api/auth/signup", body={"username": name, "password": "short"})
    check("weak password -> 400 invalid_account", status == 400 and is_error(payload, "invalid_account"))
    status, payload, _ = dave.request("POST", "/api/auth/signup", body={"username": name, "password": password})
    check("sign up -> 201, and the browser's anonymous submission moves to the account",
          status == 201 and payload["moved_submissions"] == 1, f"user {name}")
    check("the login cookie is HttpOnly-only data (not in the JSON)", "token" not in json.dumps(payload))
    status, payload, _ = dave.request("GET", "/api/auth/me")
    check("GET /api/auth/me -> the account", status == 200 and payload["user"]["username"] == name)
    status, payload, _ = Client(args.base).request("POST", "/api/auth/signup", body={"username": name.upper(), "password": password})
    check("same username (any letter case) -> 409", status == 409 and is_error(payload, "username_taken"))
    dave.request("POST", "/api/auth/logout", body={})
    status, payload, _ = dave.request("GET", "/api/auth/me")
    check("log out -> anonymous again", status == 200 and payload["user"] is None)
    status, _, _ = dave.request("GET", f"/api/submissions/{anon_sub['id']}")
    check("after log out the account's submission is private again", status == 404)
    status, wrong_pw, _ = dave.request("POST", "/api/auth/login", body={"username": name, "password": password + "x"})
    status2, no_user, _ = dave.request("POST", "/api/auth/login", body={"username": name + "zz", "password": password})
    check("wrong password and unknown user give the SAME 401 answer",
          status == status2 == 401 and wrong_pw == no_user and is_error(wrong_pw, "invalid_credentials"))
    status, payload, _ = dave.request("POST", "/api/auth/login", body={"username": name, "password": password})
    status_sub, _, _ = dave.request("GET", f"/api/submissions/{anon_sub['id']}")
    check("log in -> the submission is visible again (from any device)", status == 200 and status_sub == 200)
    status, payload, _ = dave.request("POST", "/api/submit", body={"problem": "sum-two", "language": "python", "code": quick_py},
                                      headers={"Origin": "http://evil.example"})
    check("a cross-site POST (foreign Origin) -> 403", status == 403 and is_error(payload, "bad_origin"))
    status, payload, _ = dave.request("GET", "/api/admin/problems")
    status_anon, _, _ = Client(args.base).request("GET", "/api/admin/problems")
    check("admin API: normal user -> 403, anonymous -> 401", status == 403 and status_anon == 401)

    # ------------------------------------------------------------ 12. admin
    print("[12] Admin: problems")
    admin_password = make_admin()
    if admin_password is None:
        print("  (skipped: run this on the server, e.g. docker compose exec judge python3 tools/api_smoke_test.py)")
    else:
        admin = Client(args.base)
        status, _, _ = admin.request("POST", "/api/auth/login", body={"username": "smoke-admin", "password": admin_password})
        check("admin logs in", status == 200)
        slug = "smoke-" + secrets.token_hex(3)
        draft = {"slug": slug, "index": "Z", "title": "Sum of Three", "difficulty": "Easy", "tags": ["math"],
                 "time_limit_ms": 1000, "memory_limit_mb": 256,
                 "statement": "# Z. Sum of Three\n\nPrint `a + b + c`.\n\n## Input\nThree integers.\n",
                 "sample_input": "1 2 3\n", "sample_output": "6\n"}
        status, payload, _ = admin.request("POST", "/api/admin/problems", body={**draft, "slug": "../etc"})
        check("bad slug -> 400", status == 400 and is_error(payload, "invalid_problem"))
        status, created, _ = admin.request("POST", "/api/admin/problems", body=draft)
        check("create problem -> 201, hidden by default", status == 201 and created["hidden"] is True)
        status, _, _ = alice.request("GET", f"/api/problems/{slug}")
        _, listing, _ = alice.request("GET", "/api/problems")
        check("a hidden problem is invisible to others (404, not listed)",
              status == 404 and slug not in [p["slug"] for p in listing["problems"]])
        status, payload, _ = alice.request("POST", "/api/submit", body={"problem": slug, "language": "python", "code": quick_py})
        check("and cannot be submitted to", status == 400 and is_error(payload, "unknown_problem"))
        evil = make_zip({"../evil.in": "x", "1.out": "y"})
        status, payload, _ = admin.upload(f"/api/admin/problems/{slug}/tests", "evil.zip", evil)
        check("ZIP with a '../' path -> refused", status == 400 and is_error(payload, "invalid_tests"))
        status, payload, _ = admin.upload(f"/api/admin/problems/{slug}/tests", "bad.zip", make_zip({"1.in": "1 2 3\n"}))
        check("ZIP with a missing .out -> refused", status == 400 and "missing" in payload["error"]["message"])
        status, payload, _ = admin.upload(f"/api/admin/problems/{slug}/tests", "notzip.zip", b"hello")
        check("not a ZIP -> refused", status == 400)
        tests = {}
        for i, (a, b, c) in enumerate([(1, 2, 3), (10**17, 10**17, 10**17), (-5, 0, 5)], start=1):
            tests[f"tests/{i}.in"] = f"{a} {b} {c}\r\n"
            tests[f"tests/{i}.out"] = f"{a + b + c}\n"
        status, payload, _ = admin.upload(f"/api/admin/problems/{slug}/tests", "tests.zip", make_zip(tests))
        check("valid ZIP (in a tests/ folder, Windows line endings) -> 3 tests", status == 200 and payload["tests"]["count"] == 3)
        good = "a, b, c = map(int, input().split())\nprint(a + b + c)\n"
        status, result, _ = admin.request("POST", f"/api/admin/problems/{slug}/validate", body={"language": "python", "code": good})
        check("reference solution -> Accepted on all tests", status == 200 and result["verdict"] == "Accepted"
              and len(result["tests"]) == 3)
        status, result, _ = admin.request("POST", f"/api/admin/problems/{slug}/validate",
                                          body={"language": "python", "code": "print(6)\n"})
        check("wrong reference -> Wrong Answer with full details for the admin",
              status == 200 and result["verdict"] == "Wrong Answer" and "expected" in result["message"])
        status, payload, _ = admin.request("POST", f"/api/admin/problems/{slug}/visibility", body={"hidden": False})
        _, listing, _ = alice.request("GET", "/api/problems")
        check("publish -> listed for everyone", status == 200 and slug in [p["slug"] for p in listing["problems"]])
        status, sub3, _ = alice.request("POST", "/api/submit", body={"problem": slug, "language": "python", "code": good})
        _, fin = follow(alice, sub3["id"], sub3["seq"])
        check("a contestant solves the new problem -> Accepted", fin is not None and fin["status"] == "Accepted")
        status, payload, _ = admin.request("PUT", f"/api/admin/problems/{slug}", body={**draft, "title": "Sum of Three Numbers"})
        check("edit -> saved", status == 200 and payload["title"] == "Sum of Three Numbers")
        status, payload, _ = admin.request("DELETE", f"/api/admin/problems/{slug}", body={"confirm": "nope"})
        check("delete without the right confirmation -> 400", status == 400)
        status, payload, _ = admin.request("DELETE", f"/api/admin/problems/{slug}", body={"confirm": slug})
        status_after, _, _ = alice.request("GET", f"/api/problems/{slug}")
        check("delete -> gone", status == 200 and status_after == 404)
        status, payload, _ = admin.request("GET", "/admin".replace("/admin", "/api/admin/problems"))
        check("admin overview lists accounts", status == 200 and any(u["username"] == name for u in payload["users"]))

        # -------------------------------------------------------- 12b. contests
        print("[12b] Contests, standings, exam mode, similarity")
        now = time.time()
        c_slug = "smoke-contest-" + secrets.token_hex(3)
        body = {"slug": c_slug, "title": "Smoke Contest", "description": "Good luck", "starts_at": now - 5,
                "ends_at": now + 600, "problems": ["sum-two", "max-array"], "exam_mode": True, "max_exits": 2}
        status, payload, _ = admin.request("POST", "/api/admin/contests", body={**body, "ends_at": now - 10})
        check("contest ending before it starts -> 400", status == 400 and is_error(payload, "invalid_contest"))
        status, payload, _ = admin.request("POST", "/api/admin/contests", body=body)
        check("create contest -> 201 (hidden until published)", status == 201 and payload["hidden"] and payload["phase"] == "running")
        status, _, _ = alice.request("GET", f"/api/contests/{c_slug}")
        check("hidden contest -> 404 for students", status == 404)
        admin.request("POST", f"/api/admin/contests/{c_slug}/visibility", body={"hidden": False})
        status, payload, _ = alice.request("POST", "/api/submit", body={"problem": "sum-two", "language": "python",
                                                                        "code": quick_py, "contest": c_slug})
        check("anonymous contest submission -> 401 login_required", status == 401 and is_error(payload, "login_required"))
        # two students: erin solves A after one WA, frank solves A first try
        erin, frank = Client(args.base), Client(args.base)
        for who, nm in ((erin, "smoke_e_" + secrets.token_hex(3)), (frank, "smoke_f_" + secrets.token_hex(3))):
            who.request("POST", "/api/auth/signup", body={"username": nm, "password": password})
            who.name = nm
        wrong_py = "a, b = map(int, input().split())\nprint(a - b)\n"
        for who, code in ((erin, wrong_py), (erin, quick_py), (frank, quick_py)):
            st, sub_, _ = who.request("POST", "/api/submit", body={"problem": "sum-two", "language": "python",
                                                                    "code": code, "contest": c_slug})
            follow(who, sub_["id"], sub_["seq"])
        status, table, _ = erin.request("GET", f"/api/contests/{c_slug}/standings")
        rows = {r["username"]: r for r in table["rows"]}
        e_cell, f_cell = rows[erin.name]["cells"]["sum-two"], rows[frank.name]["cells"]["sum-two"]
        check("standings: both solved A; the one without a wrong try ranks first",
              e_cell["solved"] and f_cell["solved"] and e_cell["attempts"] == 1 and f_cell["attempts"] == 0
              and rows[frank.name]["rank"] < rows[erin.name]["rank"]
              and rows[erin.name]["penalty"] == rows[frank.name]["penalty"] + 20,
              f"penalties {rows[frank.name]['penalty']} and {rows[erin.name]['penalty']}")
        check("students do not see exam exits in the standings", "integrity" not in rows[erin.name])
        status, _, _ = erin.request("GET", f"/api/contests/{c_slug}/standings.csv")
        req = urllib.request.Request(args.base + f"/api/contests/{c_slug}/standings.csv")
        with admin.opener.open(req, timeout=30) as resp:
            csv_text = resp.read().decode("utf-8-sig")
        check("CSV export: admin only, one line per student", status == 403 and csv_text.startswith("Rank,Username")
              and frank.name in csv_text and erin.name in csv_text)
        # exam mode: two exits lock frank; the server then refuses his submissions
        for kind in ("tab_hidden", "fullscreen_exit"):
            st, exam, _ = frank.request("POST", f"/api/contests/{c_slug}/integrity", body={"kind": kind})
        check("exam mode: the 2nd exit locks the exam", exam["locked"] is True and exam["exits"] == 2)
        st, payload, _ = frank.request("POST", "/api/submit", body={"problem": "max-array", "language": "python",
                                                                    "code": quick_py, "contest": c_slug})
        check("a locked student's submission is refused BY THE SERVER", st == 403 and is_error(payload, "exam_locked"))
        _, table, _ = admin.request("GET", f"/api/contests/{c_slug}/standings")
        frow = next(r for r in table["rows"] if r["username"] == frank.name)
        check("the teacher sees the exits and the lock", frow["locked"] and frow["integrity"]["total"] == 2)
        st, _, _ = admin.request("POST", f"/api/admin/contests/{c_slug}/unlock", body={"username": frank.name})
        _, status_after, _ = frank.request("GET", f"/api/contests/{c_slug}/exam-status")
        check("unlock -> the student can continue, exit count back to 0",
              st == 200 and not status_after["locked"] and status_after["exits"] == 0)
        st, user_page, _ = admin.request("GET", f"/api/admin/users/{frank.name}")
        check("student page: totals, problems and exam events", st == 200 and user_page["totals"]["solved"] == 1
              and len(user_page["exam_events"]) == 2)
        # similarity: erin's accepted code renamed by "gina" must match it
        gina = Client(args.base)
        gina.request("POST", "/api/auth/signup", body={"username": "smoke_g_" + secrets.token_hex(3), "password": password})
        copied = "# my solution\nx, y = map(int, input().split())   # read\nprint(x + y)\n"
        st, sub_, _ = gina.request("POST", "/api/submit", body={"problem": "sum-two", "language": "python",
                                                               "code": copied, "contest": c_slug})
        follow(gina, sub_["id"], sub_["seq"])
        st, sim, _ = admin.request("GET", f"/api/admin/similarity?problem=sum-two&contest={c_slug}&threshold=0.9")
        check("similarity: a renamed, re-commented copy is found at 100%",
              st == 200 and sim["pairs"] and sim["pairs"][0]["score"] == 1.0,
              f"{len(sim['pairs'])} pair(s) over {sim['compared']} students")
        pair = sim["pairs"][0]
        st, both, _ = admin.request("GET", f"/api/admin/similarity/pair?a={pair['a']['submission_id']}&b={pair['b']['submission_id']}")
        check("pair view returns both sources with shared lines", st == 200 and both["a"]["shared_lines"])
        st, _, _ = erin.request("GET", "/api/admin/similarity?problem=sum-two")
        check("similarity check is admin-only", st == 403)
        # an ended contest refuses submissions; an upcoming one hides its problems
        admin.request("PUT", f"/api/admin/contests/{c_slug}", body={**body, "starts_at": now - 600, "ends_at": now - 1})
        st, payload, _ = erin.request("POST", "/api/submit", body={"problem": "sum-two", "language": "python",
                                                                   "code": quick_py, "contest": c_slug})
        check("after the end: submissions refused", st == 403 and is_error(payload, "contest_not_running"))
        admin.request("PUT", f"/api/admin/contests/{c_slug}", body={**body, "starts_at": now + 3600, "ends_at": now + 7200})
        st, detail_up, _ = erin.request("GET", f"/api/contests/{c_slug}")
        check("before the start: problems are not shown", st == 200 and detail_up["problems"] == [])
        st, _, _ = admin.request("DELETE", f"/api/admin/contests/{c_slug}", body={"confirm": c_slug})
        check("delete contest", st == 200)

        print("[12c] Student accounts in bulk")
        tag = secrets.token_hex(2)
        rows = [{"username": f"cls_{tag}_a"}, {"username": f"cls_{tag}_b", "password": "chosen-pass-1"},
                {"username": "bad@mail.com"}, {"username": f"cls_{tag}_a"}]
        st, bulk, _ = admin.request("POST", "/api/admin/users/bulk", body={"users": rows})
        check("bulk create: 2 created, email and duplicate skipped",
              st == 201 and len(bulk["created"]) == 2 and len(bulk["skipped"]) == 2,
              ", ".join(f"{s_['username']}: {s_['reason'][:30]}" for s_ in bulk["skipped"]))
        gen = next(u for u in bulk["created"] if u["username"].endswith("_a"))
        st, _, _ = Client(args.base).request("POST", "/api/auth/login", body={"username": gen["username"], "password": gen["password"]})
        check("a generated password works", st == 200 and len(gen["password"]) == 10)
        st, _, _ = erin.request("POST", "/api/admin/users/bulk", body={"users": rows})
        check("bulk create is admin-only", st == 403)
        st, reset, _ = admin.request("POST", f"/api/admin/users/{gen['username']}/reset-password", body={})
        st_old, _, _ = Client(args.base).request("POST", "/api/auth/login", body={"username": gen["username"], "password": gen["password"]})
        st_new, _, _ = Client(args.base).request("POST", "/api/auth/login", body={"username": gen["username"], "password": reset["password"]})
        check("reset password: old one refused, new one works", st == 200 and st_old == 401 and st_new == 200)
        demote_admin()

    # ------------------------------------------------------------ 13. leak scan
    print("[13] Leak scan over every response body")
    tokens = hidden_tokens()
    seen = set()
    for body in alice.bodies + bob.bodies + carol.bodies + dave.bodies:
        seen.update(re.findall(r"[-\w]+", body))
    leaked = sorted(tokens & seen)
    check(f"none of the {len(tokens)} hidden-test tokens appeared in "
          f"{len(alice.bodies) + len(bob.bodies) + len(carol.bodies) + len(dave.bodies)} responses",
          not leaked, ", ".join(leaked[:5]))

    passed = sum(RESULTS)
    print(f"\n{passed}/{len(RESULTS)} checks passed in {time.monotonic() - started:.1f} s")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
