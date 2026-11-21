"""
judge/compare.py - decide whether the program's output matches the expected one.

A problem chooses its "checker" in meta.json (the admin page has a selector):

  "exact"  (default, like Codeforces' default checker)
      * trailing whitespace on every line is ignored (spaces, tabs, '\\r')
      * blank lines at the very end of the file are ignored
      * everything else must match exactly (leading spaces DO matter)

  "tokens"
      the outputs are split into words (any whitespace, any line breaks);
      the words must be equal. "1 2\\n3" equals "1\\n2 3".

  "float"
      like "tokens", but two words that are both numbers are equal when they
      differ by at most `abs` (absolute error) OR by at most `rel` times the
      expected value (relative error). The usual statement says
      "answers within 1e-6 are accepted". Words that are not numbers must
      still match exactly, and "nan" / "inf" are never accepted as numbers.

We read the files as streams, so a program that prints 16 MB of output never
makes the judge load 16 MB into memory twice.
"""

import math
from itertools import zip_longest

_SHOW = 80  # how many characters of a differing line/word we show in messages

CHECKERS = ("exact", "tokens", "float")
DEFAULT_EPS = 1e-6
MAX_TOKEN = 4096       # a single "word" longer than this is cut (no memory blow-up)


def normalize_checker(value):
    """
    Turn whatever meta.json says into a clean checker dict, or raise ValueError.
    Accepts a missing value, a name ("float") or a dict
    ({"type": "float", "abs": 1e-6, "rel": 1e-6}).
    """
    if value is None or value == "":
        return {"type": "exact"}
    if isinstance(value, str):
        value = {"type": value}
    if not isinstance(value, dict) or value.get("type") not in CHECKERS:
        raise ValueError(f"checker must be one of {', '.join(CHECKERS)}")
    if value["type"] != "float":
        return {"type": value["type"]}
    out = {"type": "float"}
    for key in ("abs", "rel"):
        eps = value.get(key, DEFAULT_EPS)
        try:
            eps = float(eps)
        except (TypeError, ValueError):
            raise ValueError(f"checker {key} must be a number") from None
        if not (0 <= eps <= 1) or math.isnan(eps):
            raise ValueError(f"checker {key} must be between 0 and 1")
        out[key] = eps
    return out


def describe_checker(checker):
    """One line for the problem page, e.g. 'Numbers are accepted within 1e-06 (absolute or relative)'."""
    checker = normalize_checker(checker)
    if checker["type"] == "tokens":
        return "Spaces and line breaks between values do not matter."
    if checker["type"] == "float":
        return (f"Numbers are accepted within an absolute or relative error of "
                f"{_eps(checker['abs'])}" + ("" if checker["abs"] == checker["rel"] else f" / {_eps(checker['rel'])}") + ".")
    return ""


def _eps(x):
    return f"{x:g}"


# ------------------------------------------------------------------ exact
def _normalized_lines(path):
    """
    Yield the lines of a file (as bytes) with trailing whitespace removed
    and without the blank lines at the end of the file.
    """
    pending_blank = 0
    with open(path, "rb") as f:
        for raw in f:
            line = raw.rstrip()          # bytes.rstrip() removes \n \r space \t
            if line == b"":
                # Do not yield yet: if the file ends here, these blank
                # lines must be ignored. If more text follows, we owe them.
                pending_blank += 1
                continue
            while pending_blank:
                yield b""
                pending_blank -= 1
            yield line


def _short(text):
    """Make a differing line/word printable and short for a verdict message."""
    if text is None:
        return "<end of output>"
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    return text if len(text) <= _SHOW else text[:_SHOW] + "..."


def _compare_exact(expected_path, actual_path):
    expected = _normalized_lines(expected_path)
    actual = _normalized_lines(actual_path)
    for number, (exp, act) in enumerate(zip_longest(expected, actual), start=1):
        if exp != act:
            return {"equal": False, "line": number, "expected": _short(exp), "actual": _short(act)}
    return {"equal": True}


# ------------------------------------------------------------------ tokens
def _tokens(path):
    """Yield (word, line number) for every whitespace-separated word of a file."""
    with open(path, "rb") as f:
        for number, raw in enumerate(f, start=1):
            for word in raw.split():
                yield word[:MAX_TOKEN], number


def _number(word):
    """The float value of a word, or None if it is not a finite number."""
    try:
        value = float(word)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _close(expected, actual, checker):
    """True if two numbers are equal within the absolute OR relative error."""
    diff = abs(expected - actual)
    if diff <= checker["abs"] + 1e-15:
        return True
    return diff <= checker["rel"] * abs(expected) + 1e-15


def _compare_tokens(expected_path, actual_path, checker):
    position = 0
    for (exp, exp_line), act_pair in zip_longest(_tokens(expected_path), _tokens(actual_path),
                                                 fillvalue=(None, None)):
        position += 1
        act, act_line = act_pair
        if exp is None or act is None:
            line = act_line if exp is None else exp_line
            what = "the output has extra words" if exp is None else "the output ended too early"
            return {"equal": False, "line": line, "word": position, "reason": what,
                    "expected": _short(exp), "actual": _short(act)}
        if exp == act:
            continue
        if checker["type"] == "float":
            e, a = _number(exp), _number(act)
            if e is not None and a is not None and _close(e, a, checker):
                continue
        return {"equal": False, "line": exp_line, "word": position,
                "expected": _short(exp), "actual": _short(act)}
    return {"equal": True}


# ------------------------------------------------------------------ entry
def compare_outputs(expected_path, actual_path, checker=None):
    """
    Compare the expected output file with the program's output file.

    Returns a dict:
      {"equal": True}                                  -> outputs match
      {"equal": False, "line": N,
       "expected": "...", "actual": "..."}             -> first difference
    (token checkers also return "word": the position of the differing word)
    """
    checker = normalize_checker(checker)
    if checker["type"] == "exact":
        return _compare_exact(expected_path, actual_path)
    return _compare_tokens(expected_path, actual_path, checker)


def difference_message(comparison):
    """The Wrong Answer message for a failed comparison."""
    if "word" in comparison:
        prefix = comparison.get("reason") or f"word {comparison['word']}"
        return (f"line {comparison['line']}, {prefix}: expected {comparison['expected']!r}, "
                f"got {comparison['actual']!r}")
    return f"line {comparison['line']}: expected {comparison['expected']!r}, got {comparison['actual']!r}"
