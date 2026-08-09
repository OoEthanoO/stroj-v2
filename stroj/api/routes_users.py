"""Public profiles and the leaderboard."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import db, scoring
from .deps import current_user, require_user, user_public

router = APIRouter(prefix="/api", tags=["users"])

MAX_BIO_BYTES = 4000

#: `@name` in a bio. The character class matches the username rule in `auth`,
#: and a trailing `.` or `-` is excluded so "@ann." links "ann" and keeps the
#: full stop as punctuation.
MENTION_RE = re.compile(r"(?<![\w.-])@([A-Za-z0-9_.-]{3,32})")


def mentioned_users(text: str) -> dict[str, str]:
    """Usernames a bio refers to, mapped to their role.

    Resolved here rather than in the browser so a mention renders with the same
    styling as the name anywhere else — and so a misspelt name stays plain text
    instead of becoming a link to nobody.
    """
    names = {m.group(1).rstrip(".-") for m in MENTION_RE.finditer(text or "")}
    names = {n for n in names if len(n) >= 3}
    if not names:
        return {}
    placeholders = ", ".join("?" * len(names))
    return {
        row["username"]: row["role"]
        for row in db.query(
            f"SELECT username, role FROM users WHERE username IN ({placeholders})",
            tuple(sorted(names)),
        )
    }


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
        "mentions": mentioned_users(row["bio"]),
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
