"""
judge/similarity.py - find suspiciously similar solutions (plagiarism check).

The idea is the one behind MOSS (Stanford's "Measure Of Software Similarity"):

1. NORMALIZE. Copying is usually hidden by renaming variables, changing
   comments and re-formatting. So we throw all of that away: comments and
   whitespace are removed, every identifier becomes "V", every number "N",
   every string "S". Keywords and operators are kept, because they ARE the
   structure of the program.
       int total = a + b;   // sum     ->   int V = V + V ;
       int s=x+y;                       ->   int V = V + V ;

2. FINGERPRINT with k-grams. Every run of k = 8 consecutive tokens is hashed.
   Two programs that share long runs of the same structure share hashes.

3. WINNOW (Schleimer, Wilkerson, Aiken 2003). Keeping every hash is wasteful,
   so in each window of w = 4 consecutive hashes only the smallest is kept.
   This keeps the fingerprints small but guarantees that any shared run of at
   least w + k - 1 tokens is detected.

4. SCORE = |A ∩ B| / min(|A|, |B|): the share of the smaller program found in
   the other one. 100% means one program is (structurally) inside the other.

A high score is a reason for a teacher to LOOK at the two programs, not a
proof: very short problems naturally have similar solutions.
"""

import hashlib
import re

K = 8
WINDOW = 4

C_KEYWORDS = set("""auto break case char const continue default do double else enum extern float for
goto if inline int long register return short signed sizeof static struct switch typedef union unsigned
void volatile while bool true false class namespace using template typename public private protected new
delete this virtual operator std cin cout endl vector string map set pair include define""".split())
PY_KEYWORDS = set("""False None True and as assert async await break class continue def del elif else
except finally for from global if import in is lambda nonlocal not or pass raise return try while with
yield print input range len int str list dict set map split sorted sum min max open""".split())

_C_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_PY_COMMENTS = re.compile(r"#[^\n]*")
_STRINGS = re.compile(r"\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'")
_TOKEN = re.compile(r"[A-Za-z_]\w*|\d+(?:\.\d+)?|==|!=|<=|>=|<<|>>|\+\+|--|&&|\|\||->|::|\S")


def family(language):
    """C and C++ are compared with each other; Python with Python."""
    return "py" if language == "python" else "c"


def normalize(source, language):
    """The token list described in step 1."""
    keywords = PY_KEYWORDS if family(language) == "py" else C_KEYWORDS
    text = _STRINGS.sub(" S ", source)
    if family(language) == "py":
        text = _PY_COMMENTS.sub(" ", text)
        text = re.sub(r'""".*?"""|' + r"'''.*?'''", " ", text, flags=re.S)
    else:
        text = _C_COMMENTS.sub(" ", text)
        text = re.sub(r"^\s*#\s*include[^\n]*", " ", text, flags=re.M)   # every C++ file has includes
    tokens = []
    for tok in _TOKEN.findall(text):
        if tok == "S":
            tokens.append("S")
        elif tok[0].isdigit():
            tokens.append("N")
        elif tok[0].isalpha() or tok[0] == "_":
            tokens.append(tok if tok in keywords else "V")
        else:
            tokens.append(tok)
    return tokens


def fingerprints(tokens):
    """Steps 2 and 3: k-gram hashes, winnowed."""
    if len(tokens) < K:
        return {hash_kgram(tokens)} if tokens else set()
    hashes = [hash_kgram(tokens[i:i + K]) for i in range(len(tokens) - K + 1)]
    if len(hashes) <= WINDOW:
        return {min(hashes)}
    return {min(hashes[i:i + WINDOW]) for i in range(len(hashes) - WINDOW + 1)}


def hash_kgram(tokens):
    digest = hashlib.blake2b(" ".join(tokens).encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def score(fp_a, fp_b):
    if not fp_a or not fp_b:
        return 0.0
    return len(fp_a & fp_b) / min(len(fp_a), len(fp_b))


def shared_lines(source_a, source_b, language):
    """Line numbers (1-based) of each program whose normalized form also appears in the other."""
    def norm_lines(src):
        out = []
        for line in src.splitlines():
            toks = normalize(line, language)
            out.append(" ".join(toks) if len(toks) >= 3 else None)   # ignore tiny lines like "}"
        return out
    la, lb = norm_lines(source_a), norm_lines(source_b)
    set_a, set_b = {x for x in la if x}, {x for x in lb if x}
    return ([i + 1 for i, x in enumerate(la) if x and x in set_b],
            [i + 1 for i, x in enumerate(lb) if x and x in set_a])


def compare_submissions(submissions, threshold=0.5, limit=200):
    """
    submissions: rows with id, user_id, username, language, source, status.
    Compares the latest Accepted submission of each user (or their latest one
    if none was accepted). Returns pairs with score >= threshold, best first.
    """
    chosen = {}
    for sub in submissions:                      # oldest first: later ones overwrite
        current = chosen.get(sub["user_id"])
        if current is None or sub["status"] == "Accepted" or current["status"] != "Accepted":
            chosen[sub["user_id"]] = sub
    items = []
    for sub in chosen.values():
        tokens = normalize(sub["source"], sub["language"])
        items.append((sub, family(sub["language"]), fingerprints(tokens), len(tokens)))
    pairs = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, fam_a, fp_a, len_a = items[i]
            b, fam_b, fp_b, len_b = items[j]
            if fam_a != fam_b:
                continue
            s = score(fp_a, fp_b)
            if s >= threshold:
                pairs.append({"score": round(s, 3), "a": _brief(a, len_a), "b": _brief(b, len_b)})
    pairs.sort(key=lambda p: -p["score"])
    return {"compared": len(items), "pairs": pairs[:limit]}


def _brief(sub, tokens):
    return {"submission_id": sub["id"], "username": sub["username"], "language": sub["language"],
            "status": sub["status"], "tokens": tokens}
