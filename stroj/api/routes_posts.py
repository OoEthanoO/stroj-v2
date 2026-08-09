"""The front-page stream: announcements written by admins."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request

from .. import db
from .deps import current_user, is_admin

router = APIRouter(prefix="/api/posts", tags=["posts"])

SELECT_POSTS = (
    "SELECT p.*, u.username AS author, u.role AS author_role FROM posts p"
    " LEFT JOIN users u ON u.id = p.author_id"
)


def _public(row: sqlite3.Row) -> dict:
    return {
        "slug": row["slug"],
        "title": row["title"],
        "body": row["body"],
        "author": row["author"],
        "author_role": row["author_role"],
        "pinned": bool(row["pinned"]),
        "published": bool(row["published"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@router.get("")
def list_posts(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    # Drafts stay in the same list for admins, so an unpublished post can be
    # read where it will actually appear rather than only on the admin page.
    where = "" if is_admin(current_user(request)) else " WHERE p.published = 1"
    rows = db.query(
        f"{SELECT_POSTS}{where} ORDER BY p.pinned DESC, p.created_at DESC LIMIT ?",
        (limit,),
    )
    return {"posts": [_public(r) for r in rows]}


@router.get("/{slug}")
def post_detail(slug: str, request: Request):
    row = db.one(f"{SELECT_POSTS} WHERE p.slug = ?", (slug,))
    if row is None or (not row["published"] and not is_admin(current_user(request))):
        raise HTTPException(status_code=404, detail="No such post.")
    return _public(row)
