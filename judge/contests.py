"""
judge/contests.py - contest rules and the standings (leaderboard).

Ranking follows the ICPC rules that Codeforces' "ICPC mode" also uses:

  1. More solved problems ranks higher.
  2. Ties are broken by PENALTY (lower is better). For every SOLVED problem:
         penalty += minutes from the contest start to the first Accepted
                    + 20 minutes for every rejected attempt before it.
     Unsolved problems add nothing, however many attempts they had.
     A Compilation Error does not count as an attempt (it is not a real try),
     and nothing after the first Accepted counts.
  3. Still tied: the one whose last first-Accepted came earlier ranks higher.

Only submissions made inside the contest window by non-admin accounts count.
"""

import csv
import io
import re
import time

import config

PENALTY_PER_REJECTION_MIN = 20
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
FINAL_REJECTED = {config.WRONG_ANSWER, config.TIME_LIMIT_EXCEEDED, config.MEMORY_LIMIT_EXCEEDED,
                  config.RUNTIME_ERROR}


class ContestError(ValueError):
    """Invalid contest data from the admin (message is shown to them)."""


def phase(contest, now=None):
    """'upcoming', 'running' or 'ended'."""
    now = time.time() if now is None else now
    if now < contest["starts_at"]:
        return "upcoming"
    if now < contest["ends_at"]:
        return "running"
    return "ended"


EVENT_KINDS = {
    "tab_hidden": "switched to another tab or minimized the window",
    "window_blur": "clicked outside the exam window (another program)",
    "fullscreen_exit": "left fullscreen",
    "large_paste": "pasted a large block of text",
}


def clean(data, creating, known_problems):
    """Validate admin input. Returns (slug, title, description, starts_at, ends_at, problems, exam_mode)."""
    if not isinstance(data, dict):
        raise ContestError("send a JSON object")
    slug = data.get("slug", "")
    if creating and (not isinstance(slug, str) or not SLUG_RE.match(slug)):
        raise ContestError("slug: 2-64 characters, lowercase letters, digits and '-'")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise ContestError("title: 1 to 100 characters")
    description = data.get("description", "")
    if not isinstance(description, str) or len(description) > 20000:
        raise ContestError("description: at most 20000 characters")
    try:
        starts_at = float(data.get("starts_at"))
        ends_at = float(data.get("ends_at"))
    except (TypeError, ValueError):
        raise ContestError("start and end must be dates") from None
    if ends_at <= starts_at:
        raise ContestError("the end must be after the start")
    if ends_at - starts_at > 31 * 24 * 3600:
        raise ContestError("a contest can last at most 31 days")
    problems = data.get("problems", [])
    if not isinstance(problems, list) or not problems:
        raise ContestError("choose at least one problem")
    if len(problems) > 26 or len(set(problems)) != len(problems):
        raise ContestError("at most 26 different problems")
    for slug_ in problems:
        if slug_ not in known_problems:
            raise ContestError(f"unknown problem: {slug_}")
    exam_mode = bool(data.get("exam_mode", False))
    try:
        max_exits = int(data.get("max_exits", 0) or 0)
    except (TypeError, ValueError):
        raise ContestError("max exits must be a whole number") from None
    if not 0 <= max_exits <= 50:
        raise ContestError("max exits must be between 0 (never lock) and 50")
    return (slug, title.strip(), description.replace("\r\n", "\n"), starts_at, ends_at, problems,
            exam_mode, max_exits if exam_mode else 0)


def letter(position):
    return chr(ord("A") + position)


def standings(contest, problems, submissions):
    """
    contest      the contest row (starts_at, ends_at)
    problems     problem slugs in contest order
    submissions  rows with user_id, username, problem_slug, status, created_at (oldest first)

    Returns {"problems": [...], "rows": [...]} sorted by rank. Each row:
      rank, username, solved, penalty, last_ac_min and cells[problem] =
      {"solved", "attempts", "time_min", "pending"}
    """
    start, end = contest["starts_at"], contest["ends_at"]
    users = {}
    for sub in submissions:
        if not (start <= sub["created_at"] < end) or sub["problem_slug"] not in problems:
            continue
        user = users.setdefault(sub["user_id"], {
            "username": sub["username"],
            "cells": {p: {"solved": False, "attempts": 0, "time_min": None, "pending": 0} for p in problems},
        })
        cell = user["cells"][sub["problem_slug"]]
        if cell["solved"]:
            continue                                 # nothing after the first Accepted counts
        if sub["status"] in (config.PENDING, config.RUNNING):
            cell["pending"] += 1
        elif sub["status"] == config.ACCEPTED:
            cell["solved"] = True
            cell["time_min"] = int((sub["created_at"] - start) // 60)
        elif sub["status"] in FINAL_REJECTED:
            cell["attempts"] += 1                    # Compilation/Judge errors are free

    rows = []
    for user in users.values():
        solved = [c for c in user["cells"].values() if c["solved"]]
        penalty = sum(c["time_min"] + PENALTY_PER_REJECTION_MIN * c["attempts"] for c in solved)
        rows.append({"username": user["username"], "solved": len(solved), "penalty": penalty,
                     "last_ac_min": max((c["time_min"] for c in solved), default=0),
                     "cells": user["cells"]})
    rows.sort(key=lambda r: (-r["solved"], r["penalty"], r["last_ac_min"], r["username"].lower()))
    rank = 0
    previous = None
    for i, row in enumerate(rows, start=1):
        key = (row["solved"], row["penalty"], row["last_ac_min"])
        if key != previous:
            rank = i                                 # equal results share a rank (1, 2, 2, 4)
        previous = key
        row["rank"] = rank
    first_solves = {}
    for p in problems:
        times = [(r["cells"][p]["time_min"], r["username"]) for r in rows if r["cells"][p]["solved"]]
        first_solves[p] = min(times)[1] if times else None
    return {"problems": problems, "rows": rows, "first_solves": first_solves}


def standings_csv(contest, table, titles):
    """The standings as CSV text (opens directly in Excel / Google Sheets)."""
    out = io.StringIO()
    writer = csv.writer(out)
    header = ["Rank", "Username", "Solved", "Penalty (min)"]
    for i, slug in enumerate(table["problems"]):
        header.append(f"{letter(i)}. {titles.get(slug, slug)}")
    writer.writerow(header)
    for row in table["rows"]:
        line = [row["rank"], row["username"], row["solved"], row["penalty"]]
        for slug in table["problems"]:
            cell = row["cells"][slug]
            if cell["solved"]:
                line.append(f"+{cell['attempts'] or ''} ({cell['time_min']} min)")
            elif cell["attempts"]:
                line.append(f"-{cell['attempts']}")
            else:
                line.append("")
        writer.writerow(line)
    # A UTF-8 byte-order mark makes Excel read non-English names correctly.
    return "\ufeff" + out.getvalue()
