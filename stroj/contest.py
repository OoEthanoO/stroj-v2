"""Contest windows and scoreboards.

Two scoring systems:

``icpc``  rank by problems solved, then by penalty. Penalty for a solved
          problem is the minutes from the contest start to the accepted
          submission, plus ``penalty_minutes`` for each rejected attempt before
          it. Unsolved problems contribute nothing.
``ioi``   rank by total score, where each problem contributes the best
          percentage of its tests a submission has passed. Ties break on the
          time of the last submission that improved a score.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta

from . import db, rating
from .judge.runner import AC, JUDGING, PENDING

BEFORE = "before"
RUNNING = "running"
ENDED = "ended"


def state_of(contest: sqlite3.Row, now: str | None = None) -> str:
    moment = db.parse_time(now or db.utcnow())
    if moment < db.parse_time(contest["starts_at"]):
        return BEFORE
    if moment > db.parse_time(contest["ends_at"]):
        return ENDED
    return RUNNING


def is_running(contest: sqlite3.Row, now: str | None = None) -> bool:
    return state_of(contest, now) == RUNNING


def live_ids() -> list[int]:
    """Contests running right now.

    Every public listing of submissions has to exclude these, or it hands out
    what the frozen scoreboard is withholding. The rule lives here rather than
    at each call site because it had already been copied twice and the third
    copy — the profile activity calendar — was written without it.
    """
    return [row["id"] for row in db.query("SELECT * FROM contests") if is_running(row)]


def minutes_since_start(contest: sqlite3.Row, timestamp: str) -> int:
    delta = db.parse_time(timestamp) - db.parse_time(contest["starts_at"])
    return max(0, int(delta.total_seconds() // 60))


def problems_of(contest_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT p.*, cp.label FROM contest_problems cp"
        " JOIN problems p ON p.id = cp.problem_id"
        " WHERE cp.contest_id = ? ORDER BY cp.label",
        (contest_id,),
    )


@dataclass
class Cell:
    attempts: int = 0
    solved: bool = False
    minutes: int | None = None
    penalty: int = 0
    score: int = 0
    pending: bool = False
    #: Attempts made after the freeze, counted but deliberately not resolved.
    frozen: int = 0

    def as_dict(self) -> dict:
        return {
            "attempts": self.attempts,
            "solved": self.solved,
            "minutes": self.minutes,
            "penalty": self.penalty,
            "score": self.score,
            "pending": self.pending,
            "frozen": self.frozen,
        }


@dataclass
class Row:
    user_id: int
    username: str
    cells: dict[str, Cell] = field(default_factory=dict)
    solved: int = 0
    penalty: int = 0
    total_score: int = 0
    last_improvement: int = 0  # minutes, tiebreaker for IOI

    def cell(self, label: str) -> Cell:
        return self.cells.setdefault(label, Cell())


def _percentage(score: int, max_score: int) -> int:
    """Fraction of a submission's per-test points, as a fallback.

    Only used for submissions judged before `earned_percent` existed; anything
    judged since carries its own figure, which respects subtask weights.
    """
    if max_score <= 0:
        return 0
    return round(100 * score / max_score)


def _earned(submission: sqlite3.Row) -> int:
    """What a submission is worth, 0-100.

    Must agree with the leaderboard, or the same submission reads as two
    different numbers depending on which page you are looking at. A run that
    clears one 20% subtask is worth 20 — not the 45% of individual tests that
    subtask happened to contain.
    """
    keys = submission.keys()
    if "earned_percent" in keys and submission["earned_percent"]:
        return submission["earned_percent"]
    return _percentage(submission["score"], submission["max_score"])


def freeze_at(contest: sqlite3.Row) -> str | None:
    """When the board stops updating, or None if this contest has no freeze."""
    minutes = contest["freeze_minutes"] if "freeze_minutes" in contest.keys() else 0
    if not minutes:
        return None
    moment = db.parse_time(contest["ends_at"]) - timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def scoreboard(contest: sqlite3.Row, reveal: bool = False) -> dict:
    """Build the scoreboard.

    Near the end of a contest the board conventionally freezes: submissions
    still count, but the standings stop reflecting them, so nobody can tell
    whether the team above them just solved something. Attempts made after the
    freeze are shown as a count and nothing more.

    ``reveal`` bypasses the freeze — for organisers, and for anyone once the
    contest is over. It also lifts the pre-start seal described below.
    """
    problems = problems_of(contest["id"])
    label_of = {p["id"]: p["label"] for p in problems}
    scoring = contest["scoring"]
    penalty_minutes = contest["penalty_minutes"]

    state = state_of(contest)
    # A freeze only makes sense while the contest is live; once it is over the
    # full result is public.
    frozen_from = freeze_at(contest) if state == RUNNING and not reveal else None

    submissions = db.query(
        "SELECT s.*, u.username FROM submissions s"
        " JOIN users u ON u.id = s.user_id"
        " WHERE s.contest_id = ? AND s.created_at >= ? AND s.created_at <= ?"
        " ORDER BY s.id",
        (contest["id"], contest["starts_at"], contest["ends_at"]),
    )

    rows: dict[int, Row] = {}
    for sub in submissions:
        label = label_of.get(sub["problem_id"])
        if label is None:
            continue  # problem was removed from the contest after the fact
        row = rows.setdefault(
            sub["user_id"], Row(sub["user_id"], sub["username"])
        )
        cell = row.cell(label)

        # Past the freeze the attempt is recorded but never resolved, so the
        # standings look exactly as they did the moment the board froze.
        if frozen_from is not None and sub["created_at"] >= frozen_from:
            cell.frozen += 1
            continue

        if sub["verdict"] in (PENDING, JUDGING):
            cell.pending = True
            continue

        if scoring == "ioi":
            gained = _earned(sub)
            cell.attempts += 1
            if gained > cell.score:
                cell.score = gained
                row.last_improvement = max(
                    row.last_improvement,
                    minutes_since_start(contest, sub["created_at"]),
                )
            cell.solved = cell.score == 100
            continue

        # ICPC: attempts after the first accepted submission are free.
        if cell.solved:
            continue
        cell.attempts += 1
        if sub["verdict"] == AC:
            cell.solved = True
            cell.minutes = minutes_since_start(contest, sub["created_at"])
            cell.penalty = cell.minutes + penalty_minutes * (cell.attempts - 1)

    for row in rows.values():
        if scoring == "ioi":
            row.total_score = sum(c.score for c in row.cells.values())
            row.solved = sum(1 for c in row.cells.values() if c.solved)
        else:
            row.solved = sum(1 for c in row.cells.values() if c.solved)
            row.penalty = sum(c.penalty for c in row.cells.values())
            row.total_score = row.solved

    if scoring == "ioi":
        ordered = sorted(
            rows.values(),
            key=lambda r: (-r.total_score, r.last_improvement, r.username),
        )
    else:
        ordered = sorted(
            rows.values(), key=lambda r: (-r.solved, r.penalty, r.username)
        )

    result_rows = []
    rank = 0
    previous_key = None
    for position, row in enumerate(ordered, start=1):
        key = (
            (row.total_score, row.last_improvement)
            if scoring == "ioi"
            else (row.solved, row.penalty)
        )
        if key != previous_key:
            rank = position
            previous_key = key
        result_rows.append(
            {
                "rank": rank,
                "user_id": row.user_id,
                "username": row.username,
                "solved": row.solved,
                "penalty": row.penalty,
                "total_score": row.total_score,
                "cells": {label: cell.as_dict() for label, cell in row.cells.items()},
            }
        )

    return {
        "scoring": scoring,
        "penalty_minutes": penalty_minutes,
        "state": state,
        "freeze_minutes": contest["freeze_minutes"] if "freeze_minutes" in contest.keys() else 0,
        "frozen_from": frozen_from,
        "frozen": frozen_from is not None,
        # The detail endpoint seals the problem set until the clock starts, and
        # listing the same problems here would quietly undo that: a title is
        # often enough to name the technique, and the count alone tells everyone
        # how the paper is shaped. Nothing is lost by withholding it — before
        # the start there are no submissions and so no standings to explain.
        "problems": [] if (state == BEFORE and not reveal) else [
            {
                "label": p["label"],
                "slug": p["slug"],
                "title": p["title"],
                "solved_by": sum(
                    1
                    for r in result_rows
                    if r["cells"].get(p["label"], {}).get("solved")
                ),
            }
            for p in problems
        ],
        "rows": result_rows,
    }


# ---------------------------------------------------------------------------
# Rating
#
# Ratings are derived, not accumulated. Every recompute throws away the stored
# numbers and replays each rated contest in the order it finished. That costs
# nothing at a club's scale and removes the failure modes an incremental update
# would have: a contest rated twice, a contest rated before a late submission
# finished judging, a contest whose results were corrected afterwards, or two
# contests rated in the wrong order. If anything looks wrong, replaying fixes
# it, because the replay *is* the definition.
# ---------------------------------------------------------------------------


def rated_contests() -> list[sqlite3.Row]:
    """Finished rated contests, oldest first — the order they are replayed in.

    A contest that is still running is not rated yet: its standings can change
    with the next submission.
    """
    rows = db.query("SELECT * FROM contests WHERE rated = 1 ORDER BY ends_at, id")
    return [row for row in rows if state_of(row) == ENDED]


def _days_between(earlier: str | None, later: str) -> float | None:
    if earlier is None:
        return None
    gap = (db.parse_time(later) - db.parse_time(earlier)).total_seconds()
    return max(0.0, gap / 86400.0)


def recompute_ratings() -> dict:
    """Rebuild every rating from scratch. Safe to call at any time."""
    contests = rated_contests()

    # user id -> (rating, deviation, contests played, when they last competed)
    standing: dict[int, tuple[int, float, int, str]] = {}
    written = 0

    stamp = db.utcnow()
    with db.transaction() as conn:
        conn.execute("DELETE FROM rating_changes")
        conn.execute("UPDATE contests SET rated_at = NULL")
        conn.execute(
            "UPDATE users SET rating = ?, rating_deviation = ?,"
            " rated_contests = 0, last_rated_at = NULL",
            (rating.START_RATING, rating.DEVIATION_NEW),
        )

        for contest in contests:
            board = scoreboard(contest, reveal=True)
            entrants = []
            for row in board["rows"]:
                current, deviation, _, last = standing.get(
                    row["user_id"],
                    (rating.START_RATING, rating.DEVIATION_NEW, 0, None),
                )
                entrants.append(
                    rating.Entrant(
                        user_id=row["user_id"],
                        rating=current,
                        deviation=deviation,
                        days_idle=_days_between(last, contest["ends_at"]),
                        place=row["rank"],
                    )
                )

            for change in rating.compute(entrants):
                played = standing.get(change.user_id, (0, 0, 0, None))[2] + 1
                standing[change.user_id] = (
                    change.rating_after,
                    change.deviation_after,
                    played,
                    contest["ends_at"],
                )
                conn.execute(
                    "INSERT INTO rating_changes (contest_id, user_id, place,"
                    " rating_before, rating_after, deviation_before,"
                    " deviation_after, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        contest["id"],
                        change.user_id,
                        change.place,
                        change.rating_before,
                        change.rating_after,
                        change.deviation_before,
                        change.deviation_after,
                        contest["ends_at"],
                    ),
                )
                written += 1

        if contests:
            marks = ", ".join("?" * len(contests))
            conn.execute(
                f"UPDATE contests SET rated_at = ? WHERE id IN ({marks})",
                (stamp, *[c["id"] for c in contests]),
            )

        for user_id, (value, deviation, played, last) in standing.items():
            conn.execute(
                "UPDATE users SET rating = ?, rating_deviation = ?,"
                " rated_contests = ?, last_rated_at = ? WHERE id = ?",
                (value, deviation, played, last, user_id),
            )

    return {"contests": len(contests), "changes": written, "competitors": len(standing)}


def rating_changes_for(contest_id: int) -> dict[int, sqlite3.Row]:
    return {
        row["user_id"]: row
        for row in db.query(
            "SELECT * FROM rating_changes WHERE contest_id = ?", (contest_id,)
        )
    }


def rating_history(user_id: int) -> list[dict]:
    """Every rated contest this competitor has entered, oldest first."""
    rows = db.query(
        "SELECT rc.*, c.slug AS contest_slug, c.title AS contest_title"
        "  FROM rating_changes rc"
        "  JOIN contests c ON c.id = rc.contest_id"
        " WHERE rc.user_id = ? ORDER BY rc.created_at, rc.contest_id",
        (user_id,),
    )
    return [
        {
            "contest_slug": row["contest_slug"],
            "contest_title": row["contest_title"],
            "place": row["place"],
            "rating_before": row["rating_before"],
            "rating_after": row["rating_after"],
            "delta": row["rating_after"] - row["rating_before"],
            "at": row["created_at"],
            "rank": rating.rank_dict(row["rating_after"], 1),
        }
        for row in rows
    ]


def awaiting_rating() -> list[sqlite3.Row]:
    """Finished rated contests whose results have not been applied.

    Excludes any contest still holding a submission in the queue: rating a
    contest while a solution is mid-judge would rank someone on a result that
    has not landed. The judge polls this so nobody has to remember.
    """
    rows = db.query(
        "SELECT * FROM contests WHERE rated = 1 AND rated_at IS NULL ORDER BY ends_at"
    )
    ready = []
    for row in rows:
        if state_of(row) != ENDED:
            continue
        busy = db.one(
            "SELECT 1 FROM submissions WHERE contest_id = ? AND verdict IN (?, ?)"
            " LIMIT 1",
            (row["id"], PENDING, JUDGING),
        )
        if busy is None:
            ready.append(row)
    return ready
