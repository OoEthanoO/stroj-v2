"""In-process rate limiting.

Deliberately not backed by the database: limiter state is hot, worthless after
a restart, and writing to SQLite on every failed login is how you turn a
brute-force attempt into a disk-I/O denial of service.

A single judge process owns all the traffic, so an in-memory dict is both
sufficient and the correct scope. If this ever runs behind more than one
process, this becomes per-process and the limits effectively multiply.
"""

from __future__ import annotations

import threading
import time
from collections import deque


#: Every limiter ever constructed, so tests can reset them. Limiter state is
#: process-global by design, which means it leaks between test cases unless
#: something clears it.
_REGISTRY: list["RateLimiter"] = []


def reset_all() -> None:
    for limiter in _REGISTRY:
        limiter.clear()


class RateLimiter:
    """Sliding-window counter keyed by an arbitrary string."""

    def __init__(self, limit: int, window_seconds: float, name: str = "") -> None:
        self.limit = limit
        self.window = window_seconds
        self.name = name
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        _REGISTRY.append(self)

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            # Do not accumulate a dict entry per attacker IP forever.
            self._hits.pop(key, None)
            return deque()
        return hits

    def check(self, key: str) -> float:
        """Seconds to wait before ``key`` is allowed again; 0.0 when allowed.

        Does not consume the attempt — call :meth:`hit` for that.
        """
        now = time.monotonic()
        with self._lock:
            hits = self._prune(key, now)
            if len(hits) < self.limit:
                return 0.0
            return max(0.0, hits[0] + self.window - now)

    def hit(self, key: str) -> None:
        """Record one attempt against ``key``."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            self._prune(key, now)
            self._hits.setdefault(key, hits if hits else deque()).append(now)

    def reset(self, key: str) -> None:
        """Forget a key — used when a login finally succeeds."""
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


def client_key(request) -> str:
    """Client identity from the deployment's sanitised forwarding header.

    The judge port is bound to loopback. Its Caddy overwrites X-Forwarded-For
    for direct visitors and accepts the home proxy's address only with the
    private proxy key. The home proxy always replaces it with the socket IP.
    Any other deployment must enforce the same contract before using this
    header; exposing the app port directly makes it client-controlled.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return getattr(client, "host", "unknown") or "unknown"
