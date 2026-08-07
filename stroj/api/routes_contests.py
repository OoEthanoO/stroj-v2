"""Contest listing, problem sets and scoreboards."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from .. import contest as contest_mod, db
from .deps import current_user, get_contest, is_admin

router = APIRouter(prefix="/api/contests", tags=["contests"])


def _summary(row: sqlite3.Row) -> dict:
    return {
        "slug": row["slug"],
        "title": row["title"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "scoring": row["scoring"],
        "penalty_minutes": row["penalty_minutes"],
        "state": contest_mod.state_of(row),
        "server_time": db.utcnow(),
    }


@router.get("")
def list_contests():
    rows = db.query("SELECT * FROM contests ORDER BY starts_at DESC")
    return {"contests": [_summary(r) for r in rows], "server_time": db.utcnow()}


@router.get("/{slug}")
def contest_detail(slug: str, request: Request):
    row = get_contest(slug)
    user = current_user(request)
    data = _summary(row)
    data["description"] = row["description"]

    state = data["state"]
    # The problem set stays sealed until the clock starts.
    if state == contest_mod.BEFORE and not is_admin(user):
        data["problems"] = []
        data["sealed"] = True
        return data

    data["sealed"] = False
    problems = []
    for problem in contest_mod.problems_of(row["id"]):
        entry = {
            "label": problem["label"],
            "slug": problem["slug"],
            "title": problem["title"],
            "time_limit_ms": problem["time_limit_ms"],
            "memory_limit_mb": problem["memory_limit_mb"],
        }
        if user is not None:
            solved = db.one(
                "SELECT 1 FROM submissions WHERE user_id = ? AND problem_id = ?"
                " AND contest_id = ? AND verdict = 'AC' LIMIT 1",
                (user["id"], problem["id"], row["id"]),
            )
            attempted = db.one(
                "SELECT 1 FROM submissions WHERE user_id = ? AND problem_id = ?"
                " AND contest_id = ? LIMIT 1",
                (user["id"], problem["id"], row["id"]),
            )
            entry["status"] = (
                "solved" if solved else ("attempted" if attempted else "untouched")
            )
        problems.append(entry)
    data["problems"] = problems
    return data


@router.get("/{slug}/scoreboard")
def scoreboard(slug: str):
    row = get_contest(slug)
    data = contest_mod.scoreboard(row)
    data["contest"] = _summary(row)
    return data
