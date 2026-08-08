"""Public profiles and the leaderboard."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import db, scoring
from .deps import current_user, require_user, user_public

router = APIRouter(prefix="/api", tags=["users"])

MAX_BIO_BYTES = 4000


class BioBody(BaseModel):
    bio: str = Field(max_length=MAX_BIO_BYTES)


@router.get("/leaderboard")
def leaderboard(limit: int = Query(default=100, ge=1, le=500)):
    return {
        "decay": scoring.DECAY,
        "standings": scoring.leaderboard(limit=limit),
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
