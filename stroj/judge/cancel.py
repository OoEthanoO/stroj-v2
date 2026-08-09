"""Cancellation requests for submissions that are already being judged.

A submission still sitting in the queue is cancelled in the database — the
worker simply never claims it. One that a worker has already picked up has to be
told to stop, and that signal has to reach the process the sandbox forked, or an
infinite loop keeps a worker busy until its time limit for every remaining test.

The web server and the judge workers are threads in one process, so the signal
is an in-memory event rather than anything the database has to carry. That also
means it is deliberately not durable: a request made while the judge restarts is
lost, and the submission goes back on the queue like any other interrupted work.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_requests: dict[int, threading.Event] = {}


def register(submission_id: int) -> threading.Event:
    """Start tracking a submission, returning the event judging should watch.

    Called by the worker as it begins. A request that arrived between the claim
    and this call is preserved, because the event is kept rather than replaced.
    """
    with _lock:
        event = _requests.get(submission_id)
        if event is None:
            event = threading.Event()
            _requests[submission_id] = event
        return event


def request(submission_id: int) -> None:
    """Ask for a submission to stop.

    Safe to call for a submission that is not being judged yet: the event is
    created now and picked up by whichever worker claims it.
    """
    with _lock:
        event = _requests.setdefault(submission_id, threading.Event())
    event.set()


def is_requested(submission_id: int) -> bool:
    with _lock:
        event = _requests.get(submission_id)
    return event is not None and event.is_set()


def release(submission_id: int) -> None:
    """Stop tracking a submission once judging has finished with it."""
    with _lock:
        _requests.pop(submission_id, None)


def reset() -> None:
    """Drop every request. For tests, and for a worker pool restarting."""
    with _lock:
        _requests.clear()
