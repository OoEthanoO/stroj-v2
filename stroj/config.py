"""Runtime configuration, all overridable through environment variables."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


#: Commit this build came from. Baked into the image at build time; falls back
#: to reading the working checkout when running from source.
COMMIT = os.environ.get("STROJ_COMMIT", "").strip() or "unknown"

DATA_DIR = Path(os.environ.get("STROJ_DATA", ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "stroj.db"
PROBLEM_DIR = DATA_DIR / "problems"
WORK_DIR = DATA_DIR / "work"

# Number of judge threads consuming the submission queue.
JUDGE_WORKERS = _int("STROJ_WORKERS", 2)
# Start the judge worker pool with the web app. Tests turn this off.
START_WORKERS = _flag("STROJ_START_WORKERS", True)
# Wrap user programs in `sandbox-exec` (macOS). Disable if it misbehaves.
USE_SANDBOX = _flag("STROJ_SANDBOX", True)

MAX_SOURCE_BYTES = _int("STROJ_MAX_SOURCE_BYTES", 256 * 1024)
# Hard cap on bytes a submission may write to stdout before it is killed.
OUTPUT_LIMIT_BYTES = _int("STROJ_OUTPUT_LIMIT", 64 * 1024 * 1024)
# How much of a program's stdout/stderr we keep for the verdict message.
MESSAGE_CLIP_BYTES = 4 * 1024

COMPILE_TIME_LIMIT_S = _int("STROJ_COMPILE_TIME", 20)
COMPILE_MEMORY_MB = _int("STROJ_COMPILE_MEMORY_MB", 2048)

# Who may create an account. "open" lets anyone; "invite" requires
# STROJ_INVITE_CODE, which is what a club wants — share one code with members;
# "closed" means admins create accounts with `python -m stroj adduser`.
REGISTRATION = os.environ.get("STROJ_REGISTRATION", "open").strip().lower()
INVITE_CODE = os.environ.get("STROJ_INVITE_CODE", "").strip()

# Rate limits. A judge on a public URL is an unauthenticated login endpoint and
# an account-creation endpoint facing the whole internet.
LOGIN_ATTEMPTS = _int("STROJ_LOGIN_ATTEMPTS", 10)
LOGIN_WINDOW_S = _int("STROJ_LOGIN_WINDOW", 300)
REGISTER_LIMIT = _int("STROJ_REGISTER_LIMIT", 5)
REGISTER_WINDOW_S = _int("STROJ_REGISTER_WINDOW", 3600)
SUBMIT_LIMIT = _int("STROJ_SUBMIT_LIMIT", 40)
SUBMIT_WINDOW_S = _int("STROJ_SUBMIT_WINDOW", 300)

# --- email verification ------------------------------------------------------
# An account is not usable until its address is confirmed. With no SMTP server
# configured the link is written to the log instead of sent, so a judge on a
# laptop still works and an organiser can pass the link on by hand.
SITE_NAME = os.environ.get("STROJ_SITE_NAME", "stroj").strip() or "stroj"
#: Where the site is reachable, used to build the link in the email. Getting
#: this wrong sends everyone a link to localhost, so it is worth setting.
BASE_URL = os.environ.get("STROJ_BASE_URL", "http://127.0.0.1:8000").strip()
SMTP_HOST = os.environ.get("STROJ_SMTP_HOST", "").strip()
SMTP_PORT = _int("STROJ_SMTP_PORT", 587)
SMTP_USER = os.environ.get("STROJ_SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("STROJ_SMTP_PASSWORD", "")
SMTP_STARTTLS = _flag("STROJ_SMTP_STARTTLS", True)
SMTP_SSL = _flag("STROJ_SMTP_SSL", False)
SMTP_TIMEOUT_S = _int("STROJ_SMTP_TIMEOUT", 15)
MAIL_FROM = (
    os.environ.get("STROJ_MAIL_FROM", "").strip()
    or (SMTP_USER if SMTP_USER else "stroj@localhost")
)
#: How long a confirmation link stays good.
EMAIL_TOKEN_HOURS = _int("STROJ_EMAIL_TOKEN_HOURS", 24)
#: How outgoing mail leaves the judge.
#:
#: ``auto``  SMTP when a host is configured, otherwise the log.
#: ``smtp``  straight to a mail server — needs DNS and egress, which a judge
#:           deployed the way DEPLOY.md recommends deliberately has neither.
#: ``spool`` write each message to `MAIL_SPOOL` and let something on the host
#:           send it. The container needs no network at all, and the mail
#:           credentials never enter it.
#: ``log``   write the link to the log and nothing else.
MAIL_TRANSPORT = os.environ.get("STROJ_MAIL_TRANSPORT", "auto").strip().lower()
#: Where ``spool`` leaves messages, as ordinary RFC 822 ``.eml`` files.
MAIL_SPOOL = Path(
    os.environ.get("STROJ_MAIL_SPOOL", "").strip() or (DATA_DIR / "outbox")
)
#: Confirmation mails one account may ask for, and over what window.
VERIFY_SEND_LIMIT = _int("STROJ_VERIFY_SEND_LIMIT", 5)
VERIFY_SEND_WINDOW_S = _int("STROJ_VERIFY_SEND_WINDOW", 900)

SESSION_TTL_DAYS = _int("STROJ_SESSION_TTL_DAYS", 14)
# Mark session cookies `Secure`. Turn this on for any HTTPS deployment; it is
# off by default so local http://127.0.0.1 development still works.
SECURE_COOKIES = _flag("STROJ_SECURE_COOKIES", False)
# Default penalty (minutes) per rejected attempt before an accepted one, ICPC style.
DEFAULT_ICPC_PENALTY_MINUTES = 20


def commit() -> str:
    """The commit this judge is running.

    Baked in by the image build. When running straight from a checkout (dev, or
    `python -m stroj serve`) there is no stamp, so read git instead — otherwise
    the version check would report "unknown" for the common local case.
    """
    if COMMIT != "unknown":
        return COMMIT
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def registration_mode() -> str:
    """Effective registration policy.

    An "invite" setting with no code configured would silently let everyone in,
    which is the opposite of the intent, so it fails closed instead.
    """
    if REGISTRATION == "invite":
        return "invite" if INVITE_CODE else "closed"
    return REGISTRATION if REGISTRATION in ("open", "closed") else "open"


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROBLEM_DIR, WORK_DIR, MAIL_SPOOL):
        d.mkdir(parents=True, exist_ok=True)


def data_dir_modes() -> list[tuple[Path, int]]:
    """The mode each path under STROJ_DATA must end up with.

    `0o711` means traverse-but-not-list. `/data` needs it: a submission runs as
    another account and has to reach its own box at /data/work/box-*, so it
    needs execute on every directory along that path. Making /data `0o700`
    instead locks the runner out of its own scratch directory — C++ survives it
    because execve resolves ./main against the already-open cwd, but CPython
    converts the script path to absolute and walks the tree, so Python
    submissions fail with EACCES and nothing else does.

    Confidentiality does not come from /data being unreadable; it comes from
    each thing inside it. The database is `0o600` and the answer files live in a
    `0o700` directory, so being able to traverse /data reveals nothing.
    """
    modes = [
        (DATA_DIR, 0o711),    # traverse only — see above
        (PROBLEM_DIR, 0o700), # answer files: not even traversable
        (WORK_DIR, 0o711),    # boxes are handed over individually
        # A queued message holds a confirmation link, which *is* the credential
        # it protects. Not traversable: submissions have no business here.
        (MAIL_SPOOL, 0o700),
    ]
    # SQLite writes -wal and -shm alongside the database, and they hold recent
    # transactions. Now that /data can be traversed by name they need locking
    # down explicitly rather than relying on the parent directory.
    for suffix in ("", "-wal", "-shm", "-journal"):
        modes.append((Path(str(DB_PATH) + suffix), 0o600))
    return modes


def protect_data_dir() -> None:
    """Put the judge's data out of reach of the account submissions run as.

    Only meaningful as root — unprivileged, there is no separate account to
    exclude and nothing to enforce.
    """
    if os.geteuid() != 0:
        return

    # Anything created from here on is owner-only. SQLite recreates -wal and
    # -shm at runtime, so fixing their mode once at startup would not hold.
    os.umask(0o077)

    for path, mode in data_dir_modes():
        if not path.exists():
            continue
        try:
            os.chown(path, 0, 0)
            os.chmod(path, mode)
        except OSError:
            pass
