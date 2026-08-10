"""Public profiles and the leaderboard."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import contest, db, scoring
from .deps import current_user, is_admin, require_user, user_public

router = APIRouter(prefix="/api", tags=["users"])

MAX_BIO_BYTES = 4000

#: How far back the profile's submission calendar reaches, in days.
ACTIVITY_DAYS = 365


class BioBody(BaseModel):
    bio: str = Field(max_length=MAX_BIO_BYTES)


@router.get("/mentions")
def mention_roster():
    """Every username and role, for rendering `@name` in any markdown.

    Sent as one map rather than resolved per payload because the editors
    preview markdown as you type, with nothing to resolve against. Usernames
    are already public — profiles are readable and the leaderboard lists them —
    so this exposes nothing new.
    """
    return {
        "users": {
            row["username"]: row["role"]
            for row in db.query("SELECT username, role FROM users")
        }
    }


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=100, ge=1, le=500)):
    return {
        "decay": scoring.DECAY,
        "standings": scoring.leaderboard(limit=limit),
    }


def submission_activity(
    user_id: int, viewer: sqlite3.Row | None = None, days: int = ACTIVITY_DAYS
) -> dict:
    """Submissions per day over the last `days` days, for the profile calendar.

    Days are UTC, the same clock `created_at` is written in, and only days with
    something on them are returned — the grid fills in the gaps itself.

    Counts only what this viewer is allowed to know about, on the same rule the
    submissions list applies. A daily count is not an anonymous statistic: while
    a contest runs it says how many attempts a rival has made and how many
    landed, which is exactly what the frozen scoreboard withholds. Your own
    calendar, and an organiser's view of it, stay complete.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    where = ["s.user_id = ?", "s.created_at >= ?"]
    params: list = [user_id, since]

    own = viewer is not None and viewer["id"] == user_id
    if not own and not is_admin(viewer):
        # A practice run on an unpublished problem is the setter's own working.
        where.append("(p.visible = 1 OR s.contest_id IS NOT NULL)")
        live = contest.live_ids()
        if live:
            marks = ", ".join("?" * len(live))
            where.append(f"(s.contest_id IS NULL OR s.contest_id NOT IN ({marks}))")
            params.extend(live)

    rows = db.query(
        "SELECT substr(s.created_at, 1, 10) AS date, COUNT(*) AS count,"
        "       SUM(s.verdict = 'AC') AS accepted"
        "  FROM submissions s"
        "  JOIN problems p ON p.id = s.problem_id"
        " WHERE " + " AND ".join(where) +
        " GROUP BY date ORDER BY date",
        tuple(params),
    )
    return {
        "days": days,
        "since": since,
        "until": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total": sum(row["count"] for row in rows),
        "counts": [dict(row) for row in rows],
    }


@router.patch("/users/me")
def update_me(body: BioBody, request: Request):
    user = require_user(request)
    db.execute("UPDATE users SET bio = ? WHERE id = ?", (body.bio, user["id"]))
    return {"bio": body.bio}


@router.get("/users/{username}")
def profile(username: str, request: Request):
    row = db.one("SELECT * FROM users WHERE username = ?", (username,))
    if row is None:
        raise HTTPException(status_code=404, detail="No such user.")

    viewer = current_user(request)
    solved = scoring.solved_problems(row["id"])

    # Rank comes from the same standings the leaderboard shows, so the two can
    # never disagree.
    standings = scoring.leaderboard()
    mine = next((s for s in standings if s["user_id"] == row["id"]), None)

    return {
        "username": row["username"],
        "role": row["role"],
        "is_admin": row["role"] == "admin",
        "bio": row["bio"],
        "created_at": row["created_at"],
        "editable": viewer is not None and viewer["id"] == row["id"],
        "score": round(scoring.user_score(row["id"]), 1),
        "rank": mine["rank"] if mine else None,
        "ranked_of": len(standings),
        "solved_count": len(solved),
        "solved": solved,
        "activity": submission_activity(row["id"], viewer),
        "authored": [
            {"slug": p["slug"], "title": p["title"], "points": p["points"]}
            for p in db.query(
                "SELECT slug, title, points FROM problems"
                " WHERE author_id = ? AND visible = 1 ORDER BY points DESC",
                (row["id"],),
            )
        ],
    }


@router.get("/me/profile")
def my_profile(request: Request):
    """Convenience for the frontend: who am I, and what is my standing."""
    user = current_user(request)
    if user is None:
        return {"user": None}
    return {
        "user": user_public(user),
        "score": round(scoring.user_score(user["id"]), 1),
    }
