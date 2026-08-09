"""The judge queue.

Submissions land in the database as ``PENDING``. A small pool of threads claims
them one at a time and writes verdicts back. Judging is subprocess-bound, so
threads are the right shape here — the GIL is released while we wait on the
child.
"""

from __future__ import annotations

import logging
import threading

from .. import config, db
from . import cancel, runner
from .runner import AB, JUDGING, PENDING, IE, JudgeOutcome, ProblemSpec, TestSpec

log = logging.getLogger("stroj.judge")

#: Set whenever a submission is enqueued, so workers react immediately instead
#: of waiting out their poll interval.
_wakeup = threading.Event()
_workers: list["JudgeWorker"] = []
_stop = threading.Event()


def notify() -> None:
    """Tell the pool there is new work."""
    _wakeup.set()


def claim_next() -> int | None:
    """Atomically move the oldest pending submission to JUDGING."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT id FROM submissions WHERE verdict = ? ORDER BY id LIMIT 1",
            (PENDING,),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE submissions SET verdict = ? WHERE id = ?", (JUDGING, row["id"])
        )
        return row["id"]


def load_tests(problem_id: int) -> list[TestSpec]:
    rows = db.query(
        "SELECT idx, input_path, answer_path, points, is_sample, subtask FROM testcases"
        " WHERE problem_id = ? ORDER BY idx",
        (problem_id,),
    )
    return [
        TestSpec(
            idx=r["idx"],
            input_path=r["input_path"],
            answer_path=r["answer_path"],
            # Problems that never set per-test points score one point per test.
            points=r["points"] if r["points"] > 0 else 1,
            is_sample=bool(r["is_sample"]),
            subtask=r["subtask"],
        )
        for r in rows
    ]


def load_limits(problem_id: int) -> dict[str, tuple[int, int]]:
    """Per-language limits an author set from measured runs, if any."""
    return {
        r["language"]: (r["time_limit_ms"], r["memory_limit_mb"])
        for r in db.query(
            "SELECT language, time_limit_ms, memory_limit_mb FROM problem_limits"
            " WHERE problem_id = ?",
            (problem_id,),
        )
    }


def load_subtasks(problem_id: int) -> dict[int, int]:
    """Each subtask's share of the problem's points, if it has any."""
    return {
        r["idx"]: r["percent"]
        for r in db.query(
            "SELECT idx, percent FROM problem_subtasks WHERE problem_id = ?"
            " ORDER BY idx",
            (problem_id,),
        )
    }


def store_outcome(
    submission_id: int, outcome: JudgeOutcome, earned: int = 0
) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE submissions SET verdict = ?, score = ?, max_score = ?,"
            " time_ms = ?, memory_kb = ?, message = ?, earned_percent = ?,"
            " judged_at = ? WHERE id = ?",
            (
                outcome.verdict,
                outcome.score,
                outcome.max_score,
                outcome.time_ms,
                outcome.memory_kb,
                outcome.message[: config.MESSAGE_CLIP_BYTES],
                earned,
                db.utcnow(),
                submission_id,
            ),
        )
        conn.execute(
            "DELETE FROM submission_tests WHERE submission_id = ?", (submission_id,)
        )
        conn.executemany(
            "INSERT INTO submission_tests"
            " (submission_id, idx, verdict, time_ms, memory_kb, points, message)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    submission_id,
                    t.idx,
                    t.verdict,
                    t.time_ms,
                    t.memory_kb,
                    t.points,
                    t.message[:512],
                )
                for t in outcome.tests
            ],
        )


def publish_test(submission_id: int, test) -> None:
    """Record one finished test straight away, so the page can show it.

    Without this the whole table appears at once when judging ends, which for a
    twenty-test problem means staring at nothing for most of the wait.
    """
    with db.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO submission_tests"
            " (submission_id, idx, verdict, time_ms, memory_kb, points, message)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                submission_id,
                test.idx,
                test.verdict,
                test.time_ms,
                test.memory_kb,
                test.points,
                test.message[:512],
            ),
        )
        # Keep the header's score and timing moving too, not just the table.
        conn.execute(
            "UPDATE submissions SET score = score + ?,"
            " time_ms = MAX(time_ms, ?), memory_kb = MAX(memory_kb, ?)"
            " WHERE id = ?",
            (test.points, test.time_ms, test.memory_kb, submission_id),
        )


def judge_submission(submission_id: int) -> JudgeOutcome:
    """Judge one already-claimed submission and persist the result."""
    sub = db.one("SELECT * FROM submissions WHERE id = ?", (submission_id,))
    if sub is None:
        return JudgeOutcome(IE, message="submission vanished")

    # A rejudge would otherwise leave the previous run's rows on screen while
    # the new one is still working through the tests.
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM submission_tests WHERE submission_id = ?", (submission_id,)
        )
        conn.execute(
            "UPDATE submissions SET score = 0, time_ms = 0, memory_kb = 0,"
            " message = '' WHERE id = ?",
            (submission_id,),
        )

    problem = db.one("SELECT * FROM problems WHERE id = ?", (sub["problem_id"],))
    earned = 0
    # Registering before judging starts means a request that arrived while this
    # submission was being claimed is honoured rather than lost.
    abort = cancel.register(submission_id)
    try:
        if problem is None:
            outcome = JudgeOutcome(IE, message="problem no longer exists")
        else:
            tests = load_tests(problem["id"])
            subtasks = load_subtasks(problem["id"])
            outcome = runner.judge(
                sub["source"],
                sub["language"],
                ProblemSpec.from_row(problem, load_limits(problem["id"])),
                tests,
                on_test=lambda test: publish_test(submission_id, test),
                abort=abort,
            )
            # A compile error never ran anything, and a cancelled run was never
            # allowed to finish, so neither earns anything.
            if outcome.verdict not in (runner.CE, AB):
                earned = runner.earned_percent(
                    tests, outcome.tests, subtasks, bool(problem["partial"])
                )
        store_outcome(submission_id, outcome, earned)
        return outcome
    finally:
        cancel.release(submission_id)


def drain() -> int:
    """Judge every pending submission on the calling thread. Returns the count."""
    judged = 0
    while True:
        submission_id = claim_next()
        if submission_id is None:
            return judged
        judge_submission(submission_id)
        judged += 1


def requeue_stuck() -> int:
    """Return submissions abandoned by a crashed worker to the queue."""
    cursor = db.execute(
        "UPDATE submissions SET verdict = ? WHERE verdict = ?", (PENDING, JUDGING)
    )
    return cursor.rowcount


class JudgeWorker(threading.Thread):
    def __init__(self, index: int, poll_interval: float = 1.0) -> None:
        super().__init__(name=f"judge-{index}", daemon=True)
        self.poll_interval = poll_interval

    def run(self) -> None:  # pragma: no cover - exercised by the live server
        while not _stop.is_set():
            try:
                submission_id = claim_next()
            except Exception:
                log.exception("failed to claim a submission")
                submission_id = None

            if submission_id is None:
                _wakeup.wait(self.poll_interval)
                _wakeup.clear()
                continue

            try:
                outcome = judge_submission(submission_id)
                log.info(
                    "submission %s -> %s (%s ms, %s KiB)",
                    submission_id,
                    outcome.verdict,
                    outcome.time_ms,
                    outcome.memory_kb,
                )
            except Exception:
                log.exception("judging submission %s failed", submission_id)
                try:
                    store_outcome(
                        submission_id, JudgeOutcome(IE, message="judge crashed")
                    )
                except Exception:
                    log.exception("could not record the failure either")
        db.close()


def start_pool(count: int | None = None) -> None:
    if _workers:
        return
    _stop.clear()
    requeue_stuck()
    for i in range(count if count is not None else config.JUDGE_WORKERS):
        worker = JudgeWorker(i + 1)
        worker.start()
        _workers.append(worker)
    log.info("started %d judge worker(s)", len(_workers))


def stop_pool(timeout: float = 5.0) -> None:
    _stop.set()
    _wakeup.set()
    for worker in _workers:
        worker.join(timeout=timeout)
    _workers.clear()
