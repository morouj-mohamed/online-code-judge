"""
judge/problemstore.py - create and edit problems on disk (used by the admin pages).

A problem is still just a folder, exactly like the hand-made ones:

    problems/<slug>/meta.json      title, letter, difficulty, tags, limits,
                                   "hidden" flag, last validation result
    problems/<slug>/statement.md
    problems/<slug>/sample.in, sample.out
    problems/<slug>/tests/1.in 1.out 2.in 2.out ...

Safety rules
    * Every value is validated before it touches the disk (slug, title, limits ...).
    * Files are written to a temporary name and then os.replace()d into place.
      os.replace is atomic: a reader sees the old file or the new one, never
      half a file.
    * Tests come from a ZIP. A ZIP is untrusted input, so we check it before
      extracting anything: names must be plain "N.in"/"N.out" (no folders, no
      "../" path tricks), sizes are read from the ZIP directory AND enforced
      while reading (a "zip bomb" lies about its sizes), every input needs an
      output, and the numbering must be 1..N without gaps.
"""

import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import zipfile

import config
from judge import compare

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
INDEX_RE = re.compile(r"^[A-Z0-9]{1,3}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TEST_NAME_RE = re.compile(r"^(?:tests/)?(\d{1,4})\.(in|out)$")
DIFFICULTIES = ("Easy", "Medium", "Hard")

MAX_TEXT = 64 * 1024              # statement / sample size
MAX_TESTS = 200
MAX_TEST_FILE = 16 * 1024 * 1024
MAX_TESTS_TOTAL = 128 * 1024 * 1024
MAX_ZIP_BYTES = 32 * 1024 * 1024  # size of the uploaded file itself

_write_lock = threading.Lock()


class ProblemStoreError(ValueError):
    """Invalid input from the admin (message is shown to them)."""


def problem_dir(slug):
    return os.path.join(config.PROBLEMS_DIR, slug)


def exists(slug):
    return bool(SLUG_RE.match(slug or "")) and os.path.isfile(os.path.join(problem_dir(slug), "meta.json"))


def read_meta(slug):
    with open(os.path.join(problem_dir(slug), "meta.json"), encoding="utf-8") as f:
        return json.load(f)


def is_hidden(slug):
    try:
        return bool(read_meta(slug).get("hidden", False))
    except (OSError, ValueError):
        return True


def list_slugs():
    if not os.path.isdir(config.PROBLEMS_DIR):
        return []
    return sorted(name for name in os.listdir(config.PROBLEMS_DIR) if exists(name))


def test_numbers(slug):
    folder = os.path.join(problem_dir(slug), "tests")
    if not os.path.isdir(folder):
        return []
    return sorted(int(m.group(1)) for m in (re.fullmatch(r"(\d+)\.in", n) for n in os.listdir(folder)) if m)


def tests_info(slug):
    folder = os.path.join(problem_dir(slug), "tests")
    numbers = test_numbers(slug)
    total = 0
    for n in numbers:
        for ext in ("in", "out"):
            try:
                total += os.path.getsize(os.path.join(folder, f"{n}.{ext}"))
            except OSError:
                pass
    return {"count": len(numbers), "total_bytes": total}


def next_index():
    """The next free letter: A, B, ... Z, then 1, 2, ..."""
    used = set()
    for slug in list_slugs():
        try:
            used.add(str(read_meta(slug).get("index", "")))
        except (OSError, ValueError):
            pass
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        if letter not in used:
            return letter
    n = 1
    while str(n) in used:
        n += 1
    return str(n)


# ----------------------------------------------------------------- validation
def _text(value, name, maximum=MAX_TEXT, allow_empty=False):
    if not isinstance(value, str):
        raise ProblemStoreError(f"{name} must be text")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not allow_empty and not value.strip():
        raise ProblemStoreError(f"{name} must not be empty")
    if len(value.encode("utf-8")) > maximum:
        raise ProblemStoreError(f"{name} is longer than {maximum // 1024} KB")
    if "\x00" in value:
        raise ProblemStoreError(f"{name} contains a NUL byte")
    return value


def _int(value, name, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ProblemStoreError(f"{name} must be a whole number") from None
    if not low <= number <= high:
        raise ProblemStoreError(f"{name} must be between {low} and {high}")
    return number


def clean_fields(data, creating):
    """Validate the editable fields. Returns (meta_fields, statement, sample_in, sample_out)."""
    if not isinstance(data, dict):
        raise ProblemStoreError("send a JSON object")
    slug = data.get("slug", "")
    if creating:
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            raise ProblemStoreError("slug: 2-64 characters, lowercase letters, digits and '-' "
                                    "(it becomes the URL: /problems/<slug>)")
    title = _text(data.get("title"), "title", maximum=120).strip()
    if len(title) > 80:
        raise ProblemStoreError("title is longer than 80 characters")
    index = str(data.get("index") or "").strip().upper()
    if not INDEX_RE.match(index):
        raise ProblemStoreError("letter: 1 to 3 capital letters or digits, e.g. F")
    difficulty = data.get("difficulty")
    if difficulty not in DIFFICULTIES:
        raise ProblemStoreError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
    tags = data.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    if not isinstance(tags, list):
        raise ProblemStoreError("tags must be a list")
    tags = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()]
    if len(tags) > 8 or any(not TAG_RE.match(t) for t in tags):
        raise ProblemStoreError("tags: at most 8, each lowercase letters, digits and '-'")
    meta = {
        "index": index,
        "title": title,
        "difficulty": difficulty,
        "tags": list(dict.fromkeys(tags)),
        "time_limit_ms": _int(data.get("time_limit_ms"), "time limit (ms)", 100, 10000),
        "memory_limit_mb": _int(data.get("memory_limit_mb"), "memory limit (MB)", 16, 1024),
    }
    try:
        meta["checker"] = compare.normalize_checker(data.get("checker"))
    except ValueError as exc:
        raise ProblemStoreError(str(exc)) from None
    statement = _text(data.get("statement"), "statement")
    sample_in = _text(data.get("sample_input", ""), "sample input", allow_empty=True)
    sample_out = _text(data.get("sample_output"), "sample output")
    return slug, meta, statement, sample_in, sample_out


# ---------------------------------------------------------------------- write
def _atomic_write(path, data):
    """Write to a temporary file in the same folder, then rename it into place."""
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_meta(slug, meta):
    _atomic_write(os.path.join(problem_dir(slug), "meta.json"), json.dumps(meta, indent=2) + "\n")


def save_problem(data, slug=None, creating=False):
    """Create (creating=True) or update a problem. Returns the slug."""
    new_slug, fields, statement, sample_in, sample_out = clean_fields(data, creating)
    slug = new_slug if creating else slug
    with _write_lock:
        folder = problem_dir(slug)
        if creating:
            if os.path.exists(folder):
                raise ProblemStoreError(f"a problem with the slug '{slug}' already exists")
            os.makedirs(os.path.join(folder, "tests"))
            meta = {"slug": slug, **fields, "hidden": True, "created_at": time.time()}
        else:
            if not exists(slug):
                raise ProblemStoreError(f"unknown problem: {slug}")
            meta = read_meta(slug)
            limits_changed = (meta.get("time_limit_ms") != fields["time_limit_ms"]
                              or meta.get("memory_limit_mb") != fields["memory_limit_mb"]
                              or compare.normalize_checker(meta.get("checker")) != fields["checker"])
            meta.update(fields)
            if limits_changed:
                meta.pop("validation", None)        # an old validation no longer proves anything
        meta["updated_at"] = time.time()
        _atomic_write(os.path.join(folder, "statement.md"), statement.rstrip("\n") + "\n")
        _atomic_write(os.path.join(folder, "sample.in"), sample_in)
        _atomic_write(os.path.join(folder, "sample.out"), sample_out)
        _write_meta(slug, meta)
    return slug


def set_hidden(slug, hidden):
    with _write_lock:
        meta = read_meta(slug)
        meta["hidden"] = bool(hidden)
        meta["updated_at"] = time.time()
        _write_meta(slug, meta)


def set_validation(slug, result):
    with _write_lock:
        meta = read_meta(slug)
        meta["validation"] = result
        _write_meta(slug, meta)


def delete_problem(slug):
    """Remove a problem folder for good. Old submissions keep their slug."""
    if not exists(slug):
        raise ProblemStoreError(f"unknown problem: {slug}")
    with _write_lock:
        folder = problem_dir(slug)
        # Rename first (atomic), so the problem disappears at once even if
        # deleting many test files takes a moment.
        doomed = os.path.join(config.PROBLEMS_DIR, f".deleting-{slug}-{int(time.time() * 1000)}")
        os.rename(folder, doomed)
        shutil.rmtree(doomed, ignore_errors=True)


def replace_tests(slug, zip_bytes):
    """
    Replace all tests of a problem with the contents of a ZIP file.
    Returns {"count", "total_bytes"}. Raises ProblemStoreError on any problem;
    in that case the old tests are left untouched.
    """
    if not exists(slug):
        raise ProblemStoreError(f"unknown problem: {slug}")
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise ProblemStoreError(f"the ZIP is larger than {MAX_ZIP_BYTES // (1024 * 1024)} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise ProblemStoreError("this is not a valid ZIP file") from None

    files = {}
    declared_total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        base = os.path.basename(name)
        if base.startswith(".") or name.startswith("__MACOSX/"):
            continue                              # macOS/Windows clutter
        match = TEST_NAME_RE.match(name)
        if not match:
            raise ProblemStoreError(f"unexpected file '{name}': only 1.in, 1.out, 2.in, ... "
                                    f"(optionally inside a tests/ folder) are allowed")
        key = (int(match.group(1)), match.group(2))
        if key in files:
            raise ProblemStoreError(f"'{base}' appears twice")
        if info.file_size > MAX_TEST_FILE:
            raise ProblemStoreError(f"'{base}' is larger than {MAX_TEST_FILE // (1024 * 1024)} MB")
        declared_total += info.file_size
        files[key] = info
    if declared_total > MAX_TESTS_TOTAL:
        raise ProblemStoreError(f"the tests are larger than {MAX_TESTS_TOTAL // (1024 * 1024)} MB in total")

    numbers = sorted({n for n, _ in files})
    if not numbers:
        raise ProblemStoreError("the ZIP contains no tests")
    if len(numbers) > MAX_TESTS:
        raise ProblemStoreError(f"at most {MAX_TESTS} tests")
    if numbers != list(range(1, len(numbers) + 1)):
        raise ProblemStoreError("tests must be numbered 1, 2, 3, ... without gaps")
    for n in numbers:
        for ext in ("in", "out"):
            if (n, ext) not in files:
                raise ProblemStoreError(f"{n}.{'out' if ext == 'out' else 'in'} is missing")

    with _write_lock:
        folder = problem_dir(slug)
        staging = tempfile.mkdtemp(prefix=".tests-new-", dir=folder)
        try:
            total = 0
            for (n, ext), info in files.items():
                with archive.open(info) as src:
                    # Read at most the allowed size + 1 byte: if the real data is
                    # bigger than the ZIP claimed, we notice and stop.
                    data = src.read(MAX_TEST_FILE + 1)
                if len(data) > MAX_TEST_FILE:
                    raise ProblemStoreError(f"{n}.{ext} is larger than allowed")
                total += len(data)
                if total > MAX_TESTS_TOTAL:
                    raise ProblemStoreError("the tests are larger than allowed in total")
                data = data.replace(b"\r\n", b"\n")    # Windows line endings -> Unix
                with open(os.path.join(staging, f"{n}.{ext}"), "wb") as out:
                    out.write(data)
            os.chmod(staging, 0o755)
            # Swap the folders: the old tests are renamed away, the new ones
            # renamed into place (two renames, each atomic), then the old ones
            # are deleted.
            current = os.path.join(folder, "tests")
            old = None
            if os.path.exists(current):
                old = current + f".old-{int(time.time() * 1000)}"
                os.rename(current, old)
            os.rename(staging, current)
            if old:
                shutil.rmtree(old, ignore_errors=True)
            meta = read_meta(slug)
            meta.pop("validation", None)
            meta["updated_at"] = time.time()
            _write_meta(slug, meta)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return {"count": len(numbers), "total_bytes": total}


# ---------------------------------------------------------------------- images
# Pictures used in a statement live in problems/<slug>/assets/. A statement
# shows one with a line like:   ![The graph of sample 1](graph.png)
# Only raster images are accepted (the type is read from the file's first
# bytes, never from its name): an SVG could carry a script.
ASSET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}\.(png|jpg|gif|webp)$")
ASSET_MAX_BYTES = 2 * 1024 * 1024
ASSET_MAX_COUNT = 30
_IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def _sniff_image(head):
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def assets_dir(slug):
    return os.path.join(problem_dir(slug), "assets")


def asset_path(slug, name):
    """Path of an existing image, or None. The name is checked, so '../x' never leaves the folder."""
    if not ASSET_NAME_RE.match(name or ""):
        return None
    path = os.path.join(assets_dir(slug), name)
    return path if os.path.isfile(path) else None


def asset_mime(name):
    return _IMAGE_TYPES[name.rsplit(".", 1)[1]]


def list_assets(slug):
    folder = assets_dir(slug)
    if not os.path.isdir(folder):
        return []
    return [{"name": n, "size": os.path.getsize(os.path.join(folder, n))}
            for n in sorted(os.listdir(folder)) if ASSET_NAME_RE.match(n)]


def save_asset(slug, data, original_name):
    """Store an uploaded image under a clean name derived from the original one. Returns the name."""
    if not data:
        raise ProblemStoreError("the file is empty")
    if len(data) > ASSET_MAX_BYTES:
        raise ProblemStoreError("an image can be at most 2 MB")
    kind = _sniff_image(data[:16])
    if kind is None:
        raise ProblemStoreError("only PNG, JPEG, GIF or WebP images are accepted")
    stem = os.path.splitext(os.path.basename(original_name or "image"))[0].lower()
    stem = re.sub(r"[^a-z0-9._-]+", "-", stem).strip("-.") or "image"
    name = f"{stem[:50]}.{kind}"
    with _write_lock:
        folder = assets_dir(slug)
        os.makedirs(folder, exist_ok=True)
        existing = {a["name"] for a in list_assets(slug)}
        if name not in existing and len(existing) >= ASSET_MAX_COUNT:
            raise ProblemStoreError(f"at most {ASSET_MAX_COUNT} images per problem")
        _atomic_write(os.path.join(folder, name), data)      # same name: the image is replaced
    return name


def delete_asset(slug, name):
    path = asset_path(slug, name)
    if path is None:
        raise ProblemStoreError("no such image")
    with _write_lock:
        os.unlink(path)

