"""Shared FastAPI dependencies, permission checks and serializers."""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException, Request

from .. import auth, contest, db
from ..judge.runner import VERDICT_NAMES


def current_user(request: Request) -> sqlite3.Row | None:
    return auth.user_for_token(request.cookies.get(auth.SESSION_COOKIE))


def require_user(request: Request) -> sqlite3.Row:
    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to do that.")
    return user


def require_admin(request: Request) -> sqlite3.Row:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admins only.")
    return user


def is_admin(user: sqlite3.Row | None) -> bool:
    return user is not None and user["role"] == "admin"


def problem_visible_to(problem: sqlite3.Row, user: sqlite3.Row | None) -> bool:
    """Hidden problems are readable by admins, and inside a running contest."""
    if problem["visible"] or is_admin(user):
        return True
    rows = db.query(
        "SELECT c.* FROM contest_problems cp"
        " JOIN contests c ON c.id = cp.contest_id"
        " WHERE cp.problem_id = ?",
        (problem["id"],),
    )
    return any(contest.state_of(c) != contest.BEFORE for c in rows)


def get_problem(slug: str, user: sqlite3.Row | None) -> sqlite3.Row:
    problem = db.one("SELECT * FROM problems WHERE slug = ?", (slug,))
    if problem is None or not problem_visible_to(problem, user):
        raise HTTPException(status_code=404, detail="No such problem.")
    return problem


def get_contest(slug: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM contests WHERE slug = ?", (slug,))
    if row is None:
        raise HTTPException(status_code=404, detail="No such contest.")
    return row


def user_public(user: sqlite3.Row | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_admin": user["role"] == "admin",
    }


def _author_of(problem) -> str | None:
    """Username of whoever wrote the problem, if it is still attributed."""
    author_id = problem["author_id"] if "author_id" in problem.keys() else None
    if not author_id:
        return None
    row = db.one("SELECT username FROM users WHERE id = ?", (author_id,))
    return row["username"] if row else None


def problem_summary(problem: sqlite3.Row, user: sqlite3.Row | None = None) -> dict:
    data = {
        "slug": problem["slug"],
        "title": problem["title"],
        "time_limit_ms": problem["time_limit_ms"],
        "memory_limit_mb": problem["memory_limit_mb"],
        "checker": problem["checker"],
        "partial": bool(problem["partial"]),
        "visible": bool(problem["visible"]),
        "points": problem["points"],
        "author": _author_of(problem),
    }
    if user is not None:
        best = db.one(
            "SELECT verdict FROM submissions WHERE user_id = ? AND problem_id = ?"
            " AND verdict = 'AC' LIMIT 1",
            (user["id"], problem["id"]),
        )
        attempted = db.one(
            "SELECT 1 FROM submissions WHERE user_id = ? AND problem_id = ? LIMIT 1",
            (user["id"], problem["id"]),
        )
        data["status"] = (
            "solved" if best else ("attempted" if attempted else "untouched")
        )
    return data


def submission_public(
    row: sqlite3.Row, viewer: sqlite3.Row | None, *, with_source: bool = False
) -> dict:
    own = viewer is not None and viewer["id"] == row["user_id"]
    data = {
        "id": row["id"],
        "username": row["username"] if "username" in row.keys() else None,
        "user_role": row["user_role"] if "user_role" in row.keys() else None,
        "problem_slug": row["problem_slug"] if "problem_slug" in row.keys() else None,
        "problem_title": row["problem_title"] if "problem_title" in row.keys() else None,
        "contest_slug": row["contest_slug"] if "contest_slug" in row.keys() else None,
        "language": row["language"],
        "verdict": row["verdict"],
        "verdict_name": VERDICT_NAMES.get(row["verdict"], row["verdict"]),
        "score": row["score"],
        "max_score": row["max_score"],
        "earned_percent": row["earned_percent"] if "earned_percent" in row.keys() else 0,
        "time_ms": row["time_ms"],
        "memory_kb": row["memory_kb"],
        "created_at": row["created_at"],
        "judged_at": row["judged_at"],
    }
    if with_source and (own or is_admin(viewer)):
        data["source"] = row["source"]
        data["message"] = row["message"]
    return data
