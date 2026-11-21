#!/usr/bin/env python3
"""
tools/make_problems.py - (re)generate the six problems under problems/.

Every expected output is computed here by a short, obviously-correct Python
function. The judge then checks real C / C++ / Python solutions against these
files, so a mistake in a test file cannot hide behind a mistake in a solution.

Run from the project root:   python3 tools/make_problems.py
"""

import json
import os
import random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBLEMS = os.path.join(ROOT, "problems")


# ----------------------------------------------------------------------------
# Reference answers (plain Python, easy to trust)
# ----------------------------------------------------------------------------
def is_prime(n):
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0:
        return False
    d = 3
    while d * d <= n:
        if n % d == 0:
            return False
        d += 2
    return True


def prev_prime(n):
    """Largest prime that is <= n."""
    while not is_prime(n):
        n -= 1
    return n


def fib_mod(n, m):
    """F(n) mod m with fast doubling: F(2k) = F(k)(2F(k+1)-F(k)), F(2k+1) = F(k)^2+F(k+1)^2."""
    def go(k):
        if k == 0:
            return (0, 1)
        a, b = go(k // 2)
        c = (a * ((2 * b - a) % m)) % m
        d = (a * a + b * b) % m
        return (d, (c + d) % m) if k % 2 else (c, d)
    return go(n)[0] % m


def fib_mod_slow(n, m):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, (a + b) % m
    return a % m


# Self-check: fast doubling must agree with the slow loop on small values.
for _n in range(0, 200):
    for _m in (1, 2, 7, 1000, 1000000007):
        assert fib_mod(_n, _m) == fib_mod_slow(_n, _m), (_n, _m)
assert prev_prime(10 ** 6) == 999983 and is_prime(999979)
assert prev_prime(10 ** 12) == 999999999989


# ----------------------------------------------------------------------------
# Writing helpers
# ----------------------------------------------------------------------------
# Shown by the web UI (Phase 4): the letter, a difficulty and topic tags.
DISPLAY = {
    "average":        {"index": "F", "difficulty": "Easy",   "tags": ["math", "floating-point"]},
    "sum-two":        {"index": "A", "difficulty": "Easy",   "tags": ["math", "implementation", "overflow"]},
    "max-array":      {"index": "B", "difficulty": "Easy",   "tags": ["arrays", "implementation", "fast-io"]},
    "reverse-string": {"index": "C", "difficulty": "Easy",   "tags": ["strings", "implementation"]},
    "prime-check":    {"index": "D", "difficulty": "Medium", "tags": ["math", "number-theory"]},
    "fib-mod":        {"index": "E", "difficulty": "Hard",
                       "tags": ["math", "matrix-exponentiation", "divide-and-conquer"]},
}


def write_problem(slug, title, statement, tests, time_limit_ms=1000, memory_limit_mb=256, checker=None):
    """tests is a list of (input_text, output_text)."""
    folder = os.path.join(PROBLEMS, slug)
    tests_dir = os.path.join(folder, "tests")
    os.makedirs(tests_dir, exist_ok=True)
    for name in os.listdir(tests_dir):          # remove old tests so the count is exact
        os.remove(os.path.join(tests_dir, name))

    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        display = DISPLAY.get(slug, {})
        json.dump({"slug": slug, "index": display.get("index", ""), "title": title,
                   "difficulty": display.get("difficulty", "Easy"), "tags": display.get("tags", []),
                   "time_limit_ms": time_limit_ms, "memory_limit_mb": memory_limit_mb,
                   **({"checker": checker} if checker else {})}, f, indent=2)
        f.write("\n")
    with open(os.path.join(folder, "statement.md"), "w", encoding="utf-8") as f:
        f.write(statement.strip() + "\n")

    for i, (inp, out) in enumerate(tests, start=1):
        with open(os.path.join(tests_dir, f"{i}.in"), "w", encoding="utf-8", newline="\n") as f:
            f.write(inp)
        with open(os.path.join(tests_dir, f"{i}.out"), "w", encoding="utf-8", newline="\n") as f:
            f.write(out)
    # The sample shown in the statement is the first test.
    with open(os.path.join(folder, "sample.in"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tests[0][0])
    with open(os.path.join(folder, "sample.out"), "w", encoding="utf-8", newline="\n") as f:
        f.write(tests[0][1])
    print(f"{slug}: {len(tests)} tests")


# ----------------------------------------------------------------------------
# A - Sum of Two Numbers
# ----------------------------------------------------------------------------
def problem_a():
    pairs = [
        (1, 2),
        (0, 0),
        (-5, 5),
        (-7, -3),
        (1500000000, 1500000000),                # 3*10^9 overflows a signed 32-bit int (max 2147483647)
        (10 ** 18, 10 ** 18),
        (-10 ** 18, -10 ** 18),
        (123456789012, 987654321098),
        (10 ** 18, -10 ** 18),
        (-1, 0),
    ]
    tests = [(f"{a} {b}\n", f"{a + b}\n") for a, b in pairs]
    statement = """
# A. Sum of Two Numbers

Given two integers `a` and `b`, print their sum.

## Input
One line with two integers `a` and `b` (`-10^18 <= a, b <= 10^18`).

## Output
One integer: `a + b`.

## Example
Input
```
1 2
```
Output
```
3
```

## Limits
Time limit: 1 second. Memory limit: 256 MB.
"""
    write_problem("sum-two", "Sum of Two Numbers", statement, tests)


# ----------------------------------------------------------------------------
# B - Maximum of Array
# ----------------------------------------------------------------------------
def problem_b():
    rng = random.Random(20261120)
    big = 10 ** 9

    def case(values):
        return (f"{len(values)}\n{' '.join(map(str, values))}\n", f"{max(values)}\n")

    tests = [
        case([1, 5, 3, 9, 2]),                                   # sample
        case([-big]),                                            # n = 1
        case([-5, -3, -8, -1, -9, -2]),                          # all negative
        case([7, 7, 7, 7, 7]),                                   # all equal
        case([100, 3, 5, -2, 0, 99]),                            # maximum is first
        case([-4, 0, 12, 8, 3, 1000]),                           # maximum is last
        case([rng.randint(-big, big) for _ in range(100000)]),   # large random
        case([big] * 100000),                                    # large, all maximal
        case([-big] * 100000),                                   # large, all minimal
        case(list(range(big, big - 100000, -1))),                # large, decreasing
    ]
    statement = """
# B. Maximum of Array

Given an array of `n` integers, print the largest one.

## Input
The first line contains `n` (`1 <= n <= 100000`).
The second line contains `n` integers `x_i` (`-10^9 <= x_i <= 10^9`).

## Output
One integer: the maximum element.

## Example
Input
```
5
1 5 3 9 2
```
Output
```
9
```

## Limits
Time limit: 1 second. Memory limit: 256 MB.
"""
    write_problem("max-array", "Maximum of Array", statement, tests)


# ----------------------------------------------------------------------------
# C - Reverse a String
# ----------------------------------------------------------------------------
def problem_c():
    rng = random.Random(7)
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#$%&*+-./:;<=>?@^_~"
    random_text = rng.choice(alphabet) + "".join(rng.choice(alphabet + "  ") for _ in range(99998)) + rng.choice(alphabet)

    def case(text):
        assert "\n" not in text and not text.endswith(" ")
        return (text + "\n", text[::-1] + "\n")

    tests = [
        case("hello"),                                   # sample
        case("a"),                                       # one character
        case("racecar"),                                 # palindrome
        case("Hello, World! 123"),                       # spaces and punctuation
        case("   three leading spaces"),                 # leading spaces are part of the text
        case(random_text),                               # 100000 random characters
        case("ab" * 50000),                              # 100000 characters, regular pattern
        case("0123456789"),
        case("!@#$%^&*()_+-=[]{}|;':\",./<>?"),
        case("ab"),
    ]
    statement = """
# C. Reverse a String

Print the given string written backwards.

## Input
One line with a string `s` of `1 <= |s| <= 100000` printable ASCII characters
(letters, digits, punctuation and spaces; it does not end with a space).

## Output
The reversed string on one line.

## Example
Input
```
hello
```
Output
```
olleh
```

## Limits
Time limit: 1 second. Memory limit: 256 MB.
"""
    write_problem("reverse-string", "Reverse a String", statement, tests)


# ----------------------------------------------------------------------------
# D - Prime Check
# ----------------------------------------------------------------------------
def problem_d():
    p1, p2 = 999983, 999979
    assert is_prime(p1) and is_prime(p2)
    numbers = [
        17,                    # sample
        1,                     # 1 is not prime
        2,                     # the only even prime
        4,
        97,
        10 ** 9 + 7,
        prev_prime(10 ** 12),  # the largest prime we ask about: needs a fast test
        10 ** 12,
        p1 * p2,               # product of two big primes: slowest composite for trial division
        p1 * p1,               # a square of a prime
    ]
    tests = [(f"{n}\n", ("YES" if is_prime(n) else "NO") + "\n") for n in numbers]
    statement = """
# D. Prime Check

Decide whether a number is prime.

## Input
One integer `n` (`1 <= n <= 10^12`).

## Output
Print `YES` if `n` is prime, otherwise print `NO`.

## Example
Input
```
17
```
Output
```
YES
```

## Limits
Time limit: 1 second. Memory limit: 256 MB.
"""
    write_problem("prime-check", "Prime Check", statement, tests)


# ----------------------------------------------------------------------------
# E - Fibonacci Modulo
# ----------------------------------------------------------------------------
def problem_e():
    cases = [
        (10, 1000),                       # sample: F(10) = 55
        (0, 7),                           # F(0) = 0
        (1, 7),                           # F(1) = 1
        (2, 1),                           # modulus 1: everything is 0
        (50, 10 ** 9 + 7),
        (90, 10 ** 9),
        (10 ** 18, 10 ** 9 + 7),
        (10 ** 18, 10 ** 9),
        (999999999999999999, 999999937),
        (123456789012345678, 2),
    ]
    tests = [(f"{n} {m}\n", f"{fib_mod(n, m)}\n") for n, m in cases]
    statement = """
# E. Fibonacci Modulo

The Fibonacci numbers are `F(0) = 0`, `F(1) = 1`, `F(k) = F(k-1) + F(k-2)`.
Print `F(n) mod m`.

## Input
One line with two integers `n` and `m` (`0 <= n <= 10^18`, `1 <= m <= 10^9`).

## Output
One integer: `F(n) mod m`.

## Example
Input
```
10 1000
```
Output
```
55
```

## Limits
Time limit: 1 second. Memory limit: 256 MB.
"""
    write_problem("fib-mod", "Fibonacci Modulo", statement, tests)


# ----------------------------------------------------------------------------
# F - Average of Numbers (real-number answer, checked with a tolerance)
# ----------------------------------------------------------------------------
def problem_f():
    from fractions import Fraction
    rng = random.Random(2026)                    # fixed seed: the same tests every time
    cases = [[1, 2], [7], [1, 2, 2], [-3, 4], [10 ** 9, 10 ** 9, 10 ** 9 - 1], [1, 1, 1, 1, 1, 1, 2]]
    cases.append([rng.randint(-10 ** 9, 10 ** 9) for _ in range(1000)])
    cases.append([rng.randint(0, 10) for _ in range(100000)])
    cases.append([rng.randint(-10 ** 9, 10 ** 9) for _ in range(100000)])
    tests = []
    for numbers in cases:
        mean = Fraction(sum(numbers), len(numbers))
        tests.append((f"{len(numbers)}\n{' '.join(map(str, numbers))}\n", f"{float(mean):.10f}\n"))
    statement = """
# F. Average of Numbers

Given `n` integers, print their average (arithmetic mean).

## Input
The first line contains `n` (`1 <= n <= 10^5`). The second line contains `n` integers
`a_i` (`-10^9 <= a_i <= 10^9`).

## Output
One real number: the average. Your answer is accepted if its absolute or relative error
is at most `10^-6`, so `1.5`, `1.500000` and `1.5000000001` are all correct.

## Example
Input
```
2
1 2
```
Output
```
1.5000000000
```

## Notes
The sum of `10^5` numbers of size `10^9` does not fit in a 32-bit `int`: use `long long`.
"""
    write_problem("average", "Average of Numbers", statement, tests,
                  checker={"type": "float", "abs": 1e-6, "rel": 1e-6})


if __name__ == "__main__":
    problem_a()
    problem_b()
    problem_c()
    problem_d()
    problem_e()
    problem_f()
