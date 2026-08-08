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
    for d in (DATA_DIR, PROBLEM_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)


def protect_data_dir() -> None:
    """Make the data directory unreadable to anyone but the judge.

    Submissions run as a separate unprivileged account; this is what stops them
    walking into the database or another problem's answer files. Only meaningful
    when running as root — otherwise there is no separate account to exclude.
    """
    if os.geteuid() != 0:
        return
    for path in (DATA_DIR, PROBLEM_DIR):
        try:
            os.chown(path, 0, 0)
            os.chmod(path, 0o700)
        except OSError:
            pass
    if DB_PATH.exists():
        try:
            os.chown(DB_PATH, 0, 0)
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
    # The work directory holds per-submission boxes, which get handed to the
    # runner individually, so it must be traversable by it.
    try:
        os.chown(WORK_DIR, 0, 0)
        os.chmod(WORK_DIR, 0o711)
    except OSError:
        pass
