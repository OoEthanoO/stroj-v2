"""Runtime configuration, all overridable through environment variables."""

from __future__ import annotations

import os
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

SESSION_TTL_DAYS = _int("STROJ_SESSION_TTL_DAYS", 14)
# Default penalty (minutes) per rejected attempt before an accepted one, ICPC style.
DEFAULT_ICPC_PENALTY_MINUTES = 20


def ensure_dirs() -> None:
    for d in (DATA_DIR, PROBLEM_DIR, WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
