"""Users, password hashing and cookie sessions."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from datetime import timedelta

from . import config, db

SESSION_COOKIE = "stroj_session"
_PBKDF2_ROUNDS = 200_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class AuthError(Exception):
    """Raised for bad credentials or invalid registration input."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, rounds, salt_hex, digest_hex = encoded.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
    )
    return hmac.compare_digest(digest.hex(), digest_hex)


def validate_credentials(username: str, password: str) -> None:
    if not _USERNAME_RE.match(username):
        raise AuthError(
            "Username must be 3-32 characters, letters/digits/._- only."
        )
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters.")


def create_user(username: str, password: str, role: str = "user") -> int:
    validate_credentials(username, password)
    try:
        return db.insert(
            "INSERT INTO users (username, password_hash, role, created_at)"
            " VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, db.utcnow()),
        )
    except sqlite3.IntegrityError:
        raise AuthError("That username is taken.") from None


def authenticate(username: str, password: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM users WHERE username = ?", (username,))
    if row is None or not verify_password(password, row["password_hash"]):
        # Same message either way so the endpoint does not leak which usernames exist.
        raise AuthError("Incorrect username or password.")
    return row


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = db.parse_time(db.utcnow())
    expires = now + timedelta(days=config.SESSION_TTL_DAYS)
    db.execute(
        "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, db.utcnow(), expires.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"),
    )
    return token


def user_for_token(token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    row = db.one(
        "SELECT u.*, s.expires_at FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE s.token = ?",
        (token,),
    )
    if row is None:
        return None
    if db.parse_time(row["expires_at"]) <= db.parse_time(db.utcnow()):
        destroy_session(token)
        return None
    return row


def destroy_session(token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def ensure_admin() -> tuple[str, str | None]:
    """Make sure an admin account exists.

    Returns ``(username, generated_password)``; the password is ``None`` when the
    account already existed. Set ``STROJ_ADMIN_USER`` / ``STROJ_ADMIN_PASSWORD``
    to pick your own, otherwise a random one is generated and printed once.
    """
    username = os.environ.get("STROJ_ADMIN_USER", "admin")
    existing = db.one("SELECT id FROM users WHERE username = ?", (username,))
    if existing is not None:
        return username, None
    password = os.environ.get("STROJ_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    create_user(username, password, role="admin")
    return username, password
