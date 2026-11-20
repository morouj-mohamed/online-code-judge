"""
judge/compare.py - decide whether the program's output matches the expected one.

Rule used by this judge (the same idea as Codeforces' default checker):
  * trailing whitespace on every line is ignored (spaces, tabs, '\\r')
  * blank lines at the very end of the file are ignored
  * everything else must match exactly (leading spaces DO matter)

We read both files line by line, so a program that prints 16 MB of output
never makes the judge load 16 MB into memory twice.
"""

from itertools import zip_longest

_SHOW = 80  # how many characters of a differing line we show in messages


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


def _short(line):
    """Make a differing line printable and short for a verdict message."""
    if line is None:
        return "<end of output>"
    text = line.decode("utf-8", errors="replace")
    return text if len(text) <= _SHOW else text[:_SHOW] + "..."


def compare_outputs(expected_path, actual_path):
    """
    Compare the expected output file with the program's output file.

    Returns a dict:
      {"equal": True}                                  -> outputs match
      {"equal": False, "line": N,
       "expected": "...", "actual": "..."}             -> first differing line
    """
    expected = _normalized_lines(expected_path)
    actual = _normalized_lines(actual_path)
    for number, (exp, act) in enumerate(zip_longest(expected, actual), start=1):
        if exp != act:
            return {
                "equal": False,
                "line": number,
                "expected": _short(exp),
                "actual": _short(act),
            }
    return {"equal": True}
