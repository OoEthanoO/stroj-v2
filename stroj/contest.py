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

from . import db
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

    def as_dict(self) -> dict:
        return {
            "attempts": self.attempts,
            "solved": self.solved,
            "minutes": self.minutes,
            "penalty": self.penalty,
            "score": self.score,
            "pending": self.pending,
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
    if max_score <= 0:
        return 0
    return round(100 * score / max_score)


def scoreboard(contest: sqlite3.Row) -> dict:
    """Build the full scoreboard for a contest."""
    problems = problems_of(contest["id"])
    label_of = {p["id"]: p["label"] for p in problems}
    scoring = contest["scoring"]
    penalty_minutes = contest["penalty_minutes"]

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

        if sub["verdict"] in (PENDING, JUDGING):
            cell.pending = True
            continue

        if scoring == "ioi":
            gained = _percentage(sub["score"], sub["max_score"])
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
        "state": state_of(contest),
        "problems": [
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
