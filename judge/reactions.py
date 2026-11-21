"""
judge/reactions.py - the stickers / short videos shown when a verdict arrives.

The site has built-in comic stickers (drawn in SVG, see static/js/stickers.js).
An admin can upload their own picture, GIF or short video for each verdict;
when a verdict has uploads, one of them is picked at random instead.

Safety of uploads:
  * the TYPE is decided from the first bytes of the file ("magic numbers"),
    never from its name or from what the browser claims;
  * only raster images and videos are accepted: no SVG or HTML, which could
    carry scripts;
  * the file is stored under a random name the user does not choose, so a
    name like "../../app.py" can never be written.
"""

import os
import secrets

import config

# Verdict -> short key used in URLs and in the database.
VERDICT_KEYS = {
    "Accepted": "accepted",
    "Wrong Answer": "wrong_answer",
    "Time Limit Exceeded": "time_limit",
    "Memory Limit Exceeded": "memory_limit",
    "Runtime Error": "runtime_error",
    "Compilation Error": "compilation_error",
}
KEYS = list(VERDICT_KEYS.values())

MAX_BYTES = config.REACTION_MAX_MB * 1024 * 1024
MAX_CAPTION = 80
MAX_PER_VERDICT = 12


class ReactionError(ValueError):
    pass


def sniff(head):
    """(extension, mime type, kind) from the first bytes of a file, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png", "image"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg", "image"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif", "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp", "image"
    if head[4:8] == b"ftyp":                 # ISO media: MP4 / M4V / MOV family
        return "mp4", "video/mp4", "video"
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # Matroska / WebM
        return "webm", "video/webm", "video"
    return None


def save(data, verdict_key, caption):
    """Check an upload and write it to disk. Returns the row fields to store."""
    if verdict_key not in KEYS:
        raise ReactionError("unknown verdict")
    if not data:
        raise ReactionError("the file is empty")
    if len(data) > MAX_BYTES:
        raise ReactionError(f"the file is larger than {config.REACTION_MAX_MB} MB")
    kind = sniff(data[:16])
    if kind is None:
        raise ReactionError("only PNG, JPEG, GIF, WebP, MP4 or WebM files are accepted")
    caption = " ".join((caption or "").split())
    if len(caption) > MAX_CAPTION:
        raise ReactionError(f"the caption is longer than {MAX_CAPTION} characters")
    ext, mime, media = kind
    os.makedirs(config.REACTIONS_DIR, mode=0o755, exist_ok=True)
    filename = f"{secrets.token_hex(12)}.{ext}"
    path = os.path.join(config.REACTIONS_DIR, filename)
    # O_EXCL: never overwrite an existing file, even by a (very unlikely) name clash.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    return {"verdict": verdict_key, "filename": filename, "mime": mime, "kind": media,
            "caption": caption, "size": len(data)}


def path_of(filename):
    """Absolute path of a stored file; refuses anything that is not a plain name."""
    if not filename or os.path.basename(filename) != filename or filename.startswith("."):
        raise ReactionError("bad file name")
    return os.path.join(config.REACTIONS_DIR, filename)


def delete_file(filename):
    try:
        os.unlink(path_of(filename))
    except (FileNotFoundError, ReactionError):
        pass


def public(row):
    return {"id": row["id"], "verdict": row["verdict"], "kind": row["kind"],
            "caption": row["caption"], "url": f"/media/reactions/{row['id']}"}
