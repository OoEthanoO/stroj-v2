"""Shared FastAPI dependencies, permission checks and serializers."""

from __future__ import annotations

import sqlite3

from fastapi import HTTPException, Request

from .. import auth, contest, db, rating
from ..judge.runner import CE, VERDICT_NAMES


#: Set by an admin who wants to browse as an ordinary member would.
VIEW_AS_COOKIE = "stroj_view_as"


def session_user(request: Request) -> sqlite3.Row | dict | None:
    """Whose session this is, confirmed or not — possibly with the admin role
    set aside.

    An admin cannot check what members can see by looking at their own screen,
    because the server has already decided they may see everything. The honest
    way to answer "is this leaking?" is to make the server answer as it would
    for a member, which is what the view-as cookie does: the role is dropped
    here, once, so every `is_admin` check downstream simply comes out False.
    Hidden problems vanish from listings, contest metadata seals, judge output
    goes away, and `/api/admin/*` returns 403 — not because the page is
    pretending, but because the request really is an ordinary one now.

    It can only ever take privilege away, and only from someone who had it, so
    a member setting the cookie by hand gains nothing.

    Almost nothing should call this. It exists for the confirmation flow, which
    has to know whose account is waiting; everything else wants `current_user`.
    """
    user = auth.user_for_token(request.cookies.get(auth.SESSION_COOKIE))
    if user is None or user["role"] != "admin":
        return user
    if request.cookies.get(VIEW_AS_COOKIE) != "user":
        return user
    demoted = dict(user)
    demoted["role"] = "user"
    return demoted


def current_user(request: Request) -> sqlite3.Row | dict | None:
    """Who is making this request, as far as the rest of the site is concerned.

    An account whose address is not confirmed is **nobody**: the same as being
    signed out. Not merely blocked from acting — invisible, because privilege
    is read off this one value all over the codebase, and a check that only
    guarded the endpoints which *write* would leave every read still answering
    as though the account were fully signed in. That is not theoretical: it let
    an unconfirmed admin list hidden problems, read unpublished posts, see
    through a scoreboard freeze and open other people's submissions.

    Signing up therefore buys nothing until the link is opened. What is still
    visible is exactly what a signed-out visitor sees, which is the point — a
    member part-way through signing up can still look at the site they are
    joining, and the confirmation page reaches their account through
    `session_user` instead.
    """
    user = session_user(request)
    return user if auth.is_verified(user) else None


def require_account(request: Request) -> sqlite3.Row:
    """Signed in, whether or not the address is confirmed.

    Only the handful of endpoints that *are* the confirmation flow use this —
    reading your own account, naming an address, asking for another link. If a
    new endpoint is tempted to use it, that endpoint is letting an unconfirmed
    account act, which is the thing being prevented.
    """
    user = session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to do that.")
    return user


def require_user(request: Request) -> sqlite3.Row:
    """Signed in *and* confirmed — the gate every real action passes through.

    Reached only by a confirmed account, since `current_user` has already
    reduced an unconfirmed one to nobody. The explicit check below is what
    turns that "nobody" into a message explaining which step is missing,
    rather than a bare "sign in" for someone who plainly is.
    """
    user = require_account(request)
    if not auth.is_verified(user):
        raise HTTPException(
            status_code=403,
            detail=(
                "Confirm your email address first — we sent you a link."
                if user["email"] else
                "Add an email address to your account first."
            ),
            headers={"X-Stroj-Reason": "email-unverified"},
        )
    return user


def require_admin(request: Request) -> sqlite3.Row:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admins only.")
    return user


def is_admin(user: sqlite3.Row | None) -> bool:
    return user is not None and user["role"] == "admin"


def contests_with(problem_id: int) -> list[sqlite3.Row]:
    return db.query(
        "SELECT c.* FROM contest_problems cp"
        " JOIN contests c ON c.id = cp.contest_id"
        " WHERE cp.problem_id = ?",
        (problem_id,),
    )


def problem_visible_to(problem: sqlite3.Row, user: sqlite3.Row | None) -> bool:
    """Hidden problems are readable by admins, and inside a running contest."""
    if problem["visible"] or is_admin(user):
        return True
    return any(
        contest.state_of(c) != contest.BEFORE for c in contests_with(problem["id"])
    )


def metadata_sealed(problem: sqlite3.Row, user: sqlite3.Row | None) -> bool:
    """Whether this viewer must not be told the problem's points and types.

    A problem written for a contest carries two things the statement does not:
    a point value, which rates its difficulty against the rest of the archive,
    and type tags, which name the technique outright. Telling a contestant that
    problem D is tagged ``binary search`` measures whether they can implement a
    named algorithm rather than whether they can pick one, which is the whole
    thing a contest is asking.

    Only applies while the contest runs, and only to problems that were not
    already public — an archived problem's rating is common knowledge, and
    hiding it mid-contest would tell nobody anything they could not look up.
    """
    if problem["visible"] or is_admin(user):
        return False
    return any(contest.is_running(c) for c in contests_with(problem["id"]))


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
    keys = user.keys() if hasattr(user, "keys") else user
    verified = auth.is_verified(user)
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        # "May act as an admin *now*", which an unconfirmed account may not —
        # the server refuses it either way, and offering the admin page to
        # someone it will then turn away is a lie the page tells itself.
        "is_admin": user["role"] == "admin" and verified,
        # Their own address, and whether it is confirmed. Safe to include
        # because every caller of this serializer renders the *viewer's own*
        # account — login, `/auth/me`, `/users/me/profile`. The public profile
        # at `/users/{name}` builds its own dict and must keep doing so.
        "email": user["email"] if "email" in keys else None,
        "email_verified": auth.is_verified(user),
        "rating": user["rating"],
        # `rating_rank`, not `rank`: `rank` already means leaderboard position
        # on the profile and the standings, and shadowing it silently replaced
        # the position everywhere both appear.
        "rating_rank": rating.rank_dict(user["rating"], user["rated_contests"]),
    }


def _author_of(problem) -> tuple[str | None, str | None]:
    """``(username, role)`` of whoever wrote the problem, if still attributed.

    The role travels with the name so an admin author renders the same way
    everywhere — a name styled one way on their profile and another in a table
    reads as two different people.
    """
    author_id = problem["author_id"] if "author_id" in problem.keys() else None
    if not author_id:
        return None, None
    row = db.one("SELECT username, role FROM users WHERE id = ?", (author_id,))
    return (row["username"], row["role"]) if row else (None, None)


def problem_types(problem_id: int) -> list[str]:
    return [
        r["type"]
        for r in db.query(
            "SELECT type FROM problem_types WHERE problem_id = ? ORDER BY type",
            (problem_id,),
        )
    ]


def problem_summary(problem: sqlite3.Row, user: sqlite3.Row | None = None) -> dict:
    author_name, author_role = _author_of(problem)
    sealed = metadata_sealed(problem, user)
    data = {
        "slug": problem["slug"],
        "title": problem["title"],
        "time_limit_ms": problem["time_limit_ms"],
        "memory_limit_mb": problem["memory_limit_mb"],
        "checker": problem["checker"],
        "partial": bool(problem["partial"]),
        "visible": bool(problem["visible"]),
        "points": None if sealed else problem["points"],
        "author": author_name,
        "author_role": author_role,
        "types": [] if sealed else problem_types(problem["id"]),
        "metadata_sealed": sealed,
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
        data["message"] = judge_output_for(row, viewer)
    return data


def judge_output_for(row: sqlite3.Row, viewer: sqlite3.Row | None) -> str:
    """The judge's own message, if this viewer is allowed to read it.

    Judge output describes the hidden tests — which one failed, and how — so it
    is a hint about data the solver is not meant to see. Admins keep it: without
    it, a problem with broken test data or a misbehaving checker cannot be
    diagnosed from the site at all.

    A compile error is the exception. It is produced from the submitter's own
    source, quotes nothing from the problem, and withholding it leaves a solver
    with a verdict they cannot act on.
    """
    if is_admin(viewer) or row["verdict"] == CE:
        return row["message"]
    return ""
