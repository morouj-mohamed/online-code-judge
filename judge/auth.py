"""
judge/auth.py - accounts: password hashing and login tokens.

Only the Python standard library is used.

Passwords
    Stored as  scrypt$<n>$<r>$<p>$<salt>$<hash>  (salt and hash in base64).
    scrypt is a "memory-hard" key derivation function: computing one hash
    needs ~16 MB of RAM and some CPU on purpose, so guessing millions of
    passwords from a stolen database is very slow and expensive. A random salt
    per user means two users with the same password get different hashes.
    Comparison uses hmac.compare_digest, whose running time does not depend on
    where the first different byte is (no timing leak).

Login tokens
    After a successful login the browser gets a random 256-bit token in an
    HttpOnly cookie. The database stores only SHA-256(token): a leaked database
    cannot be used to log in, and logging out deletes the row.
"""

import base64
import hashlib
import hmac
import re
import secrets

SCRYPT_N = 2 ** 14      # CPU/memory cost (16 MB with r = 8)
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,31}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 128


class ValidationError(ValueError):
    """A username or password does not follow the rules (message is user-facing)."""


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _scrypt(password, salt, n, r, p):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=SCRYPT_MAXMEM, dklen=32)


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password, stored):
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A real hash of a random password: used when a username does not exist, so a
# failed login takes the same time whether or not the account exists (an
# attacker cannot find valid usernames by timing the answer).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def burn_time(password):
    verify_password(password, _DUMMY_HASH)


def validate_username(username):
    if isinstance(username, str) and "@" in username:
        raise ValidationError("Username: use a short name (for example morouj), not an email address.")
    if not isinstance(username, str) or not USERNAME_RE.match(username):
        raise ValidationError("Username: 3 to 32 characters, letters, digits, '_', '.' or '-', "
                              "starting with a letter or digit.")
    return username


def validate_password(password, username=""):
    if not isinstance(password, str) or not (PASSWORD_MIN <= len(password) <= PASSWORD_MAX):
        raise ValidationError(f"Password: {PASSWORD_MIN} to {PASSWORD_MAX} characters.")
    if username and password.lower() == username.lower():
        raise ValidationError("The password must not be the same as the username.")
    return password


# No look-alike characters (0/O, 1/l/I), so a printed password is easy to type.
_READABLE = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def readable_password(length=10):
    """A random password for a new student: 10 characters, ~57 bits of randomness."""
    return "".join(secrets.choice(_READABLE) for _ in range(length))


def new_token():
    """A fresh login token (for the cookie) and its hash (for the database)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
