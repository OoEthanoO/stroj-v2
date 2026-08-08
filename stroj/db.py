"""SQLite access layer.

One connection per thread (judge workers and the web server share the file), WAL
journalling so readers never block on the judge writing verdicts.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

_local = threading.local()
_SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def utcnow() -> str:
    """Current time as a sortable ISO-8601 UTC string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_time(value: str) -> datetime:
    """Parse a timestamp written by :func:`utcnow` (or any ISO-8601 string)."""
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def connect() -> sqlite3.Connection:
    """Return this thread's connection, opening it on first use."""
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == config.DB_PATH:
        return conn
    if conn is not None:
        conn.close()

    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    _local.conn = conn
    _local.path = config.DB_PATH
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.path = None


#: Columns added after the first release. `CREATE TABLE IF NOT EXISTS` will not
#: retrofit these onto a database that already exists, so they are applied by
#: hand. Append-only; never reorder.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("contests", "freeze_minutes", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> list[str]:
    applied = []
    for table, column, declaration in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if existing and column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")
            applied.append(f"{table}.{column}")
    return applied


def init_db() -> list[str]:
    """Create the schema if absent, then bring an older database up to date."""
    conn = connect()
    conn.executescript(_SCHEMA.read_text())
    return _migrate(conn)


def query(sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def one(sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return connect().execute(sql, params).fetchone()


def execute(sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
    return connect().execute(sql, params)


def insert(sql: str, params: tuple | dict = ()) -> int:
    return connect().execute(sql, params).lastrowid


class transaction:
    """`with transaction():` — commits on success, rolls back on exception."""

    def __enter__(self) -> sqlite3.Connection:
        self.conn = connect()
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.conn.execute("COMMIT")
        else:
            self.conn.execute("ROLLBACK")
        return False
