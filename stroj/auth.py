"""Users, password hashing, cookie sessions and email confirmation."""

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
#: Deliberately loose. The only address check that proves anything is sending
#: mail to it and waiting for the click, which is the whole point of the flow —
#: a stricter pattern would only reject unusual but valid addresses.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


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


def clean_email(email: str) -> str:
    """Normalise and check an address, or raise `AuthError`.

    Only the case of the domain is folded, not of the mailbox: `Ann@x.com` and
    `ann@x.com` are the same host's problem, not ours, and lowercasing the whole
    thing would be wrong for the minority of servers that care.
    """
    address = (email or "").strip()
    if not address:
        raise AuthError("An email address is required.")
    if len(address) > 254:
        raise AuthError("That email address is too long.")
    if not _EMAIL_RE.match(address):
        raise AuthError("That does not look like an email address.")
    mailbox, domain = address.rsplit("@", 1)
    return f"{mailbox}@{domain.lower()}"


def create_user(
    username: str,
    password: str,
    role: str = "user",
    email: str | None = None,
    email_verified: bool = False,
) -> int:
    validate_credentials(username, password)
    address = clean_email(email) if email else None
    try:
        return db.insert(
            "INSERT INTO users (username, password_hash, role, email,"
            " email_verified, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, hash_password(password), role, address,
             int(email_verified), db.utcnow()),
        )
    except sqlite3.IntegrityError as exc:
        if "users.username" in str(exc) or "UNIQUE constraint failed: users.username" in str(exc):
            raise AuthError("That username is taken.") from None
        if "idx_users_verified_email" in str(exc):
            raise AuthError("That email address is already in use.") from None
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


# ----------------------------------------------------------- email addresses


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def set_email(user_id: int, email: str) -> str:
    """Attach an unconfirmed address to an account. Returns the cleaned form.

    Any link already in flight for this account is dropped: a member who
    corrects a typo must not be able to confirm the typo afterwards.
    """
    address = clean_email(email)
    taken = db.one(
        "SELECT id FROM users WHERE email = ? COLLATE NOCASE AND email_verified = 1"
        " AND id != ?",
        (address, user_id),
    )
    if taken is not None:
        raise AuthError("That email address is already in use.")
    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET email = ?, email_verified = 0 WHERE id = ?",
            (address, user_id),
        )
        conn.execute("DELETE FROM email_tokens WHERE user_id = ?", (user_id,))
    return address


def issue_email_token(user_id: int, email: str) -> str:
    """Mint a confirmation token for `email`. Only the digest is stored."""
    token = secrets.token_urlsafe(32)
    expires = db.parse_time(db.utcnow()) + timedelta(hours=config.EMAIL_TOKEN_HOURS)
    with db.transaction() as conn:
        # One live link per account: a resend replaces its predecessor rather
        # than adding a second key to the same door.
        conn.execute("DELETE FROM email_tokens WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO email_tokens (token_hash, user_id, email, created_at,"
            " expires_at) VALUES (?, ?, ?, ?, ?)",
            (
                _token_hash(token),
                user_id,
                email,
                db.utcnow(),
                expires.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            ),
        )
    return token


def confirm_email_token(token: str) -> sqlite3.Row:
    """Consume a confirmation link and mark the address verified.

    Every failure says the same thing. A message that distinguished "expired"
    from "never existed" would let anyone with a list of guesses learn which
    tokens are real, and the member's remedy is the same either way: ask for a
    new link.
    """
    row = db.one(
        "SELECT * FROM email_tokens WHERE token_hash = ?", (_token_hash(token or ""),)
    )
    stale = "That confirmation link is no longer valid. Ask for a new one."
    if row is None or row["used_at"] is not None:
        raise AuthError(stale)
    if db.parse_time(row["expires_at"]) <= db.parse_time(db.utcnow()):
        raise AuthError(stale)

    user = db.one("SELECT * FROM users WHERE id = ?", (row["user_id"],))
    if user is None:
        raise AuthError(stale)
    # The address may have been changed since the link was sent, in which case
    # this link confirms nothing.
    if (user["email"] or "").lower() != row["email"].lower():
        raise AuthError(stale)
    if db.one(
        "SELECT id FROM users WHERE email = ? COLLATE NOCASE AND email_verified = 1"
        " AND id != ?",
        (row["email"], user["id"]),
    ):
        raise AuthError("That email address is already in use.")

    with db.transaction() as conn:
        conn.execute(
            "UPDATE users SET email_verified = 1 WHERE id = ?", (user["id"],)
        )
        conn.execute(
            "UPDATE email_tokens SET used_at = ? WHERE token_hash = ?",
            (db.utcnow(), row["token_hash"]),
        )
    return db.one("SELECT * FROM users WHERE id = ?", (user["id"],))


def is_verified(user) -> bool:
    """Whether this account has confirmed an address.

    Tolerates rows read before the column existed, so a half-migrated database
    reports "not verified" rather than raising.
    """
    if user is None:
        return False
    keys = user.keys() if hasattr(user, "keys") else user
    if "email_verified" not in keys:
        return False
    return bool(user["email_verified"])


def ensure_admin() -> tuple[str, str | None]:
    """Make sure an admin account exists.

    Returns ``(username, generated_password)``; the password is ``None`` when the
    account already existed. Set ``STROJ_ADMIN_USER`` / ``STROJ_ADMIN_PASSWORD``
    to pick your own, otherwise a random one is generated and printed once.

    The bootstrap admin is created already verified, with ``STROJ_ADMIN_EMAIL``
    if one is given. It has to be: this account exists before any mail server
    does, and a judge whose only administrator is locked behind a confirmation
    link it cannot receive has no way back in. Every other account, including
    admins promoted later, goes through the normal flow.
    """
    username = os.environ.get("STROJ_ADMIN_USER", "admin")
    existing = db.one("SELECT id FROM users WHERE username = ?", (username,))
    if existing is not None:
        return username, None
    password = os.environ.get("STROJ_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    email = os.environ.get("STROJ_ADMIN_EMAIL", "").strip() or None
    create_user(username, password, role="admin", email=email, email_verified=True)
    return username, password
