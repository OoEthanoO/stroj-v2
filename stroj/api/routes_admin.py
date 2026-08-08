"""Admin endpoints: authoring problems, test data, contests, rejudging."""

from __future__ import annotations

import re
import sqlite3

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from pydantic import BaseModel, Field

from .. import db, testdata
from ..judge import checkers, worker
from ..judge.runner import PENDING
from .deps import get_contest, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def _check_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail="Slug must be lowercase letters, digits and dashes (2-64 chars).",
        )
    return slug


def _problem_or_404(slug: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM problems WHERE slug = ?", (slug,))
    if row is None:
        raise HTTPException(status_code=404, detail="No such problem.")
    return row


class ProblemBody(BaseModel):
    slug: str
    title: str = Field(min_length=1, max_length=200)
    statement: str = ""
    time_limit_ms: int = Field(default=1000, ge=100, le=60_000)
    memory_limit_mb: int = Field(default=256, ge=16, le=4096)
    checker: str = "token"
    float_eps: float = 1e-6
    partial: bool = False
    visible: bool = True
    #: Difficulty value awarded for solving it. Distinct from a testcase's
    #: `points`, which only splits partial credit within one problem.
    points: int = Field(default=100, ge=1, le=10000)
    #: Username to credit. Defaults to whoever creates the problem.
    author: str | None = None


class ProblemPatch(BaseModel):
    title: str | None = None
    statement: str | None = None
    time_limit_ms: int | None = Field(default=None, ge=100, le=60_000)
    memory_limit_mb: int | None = Field(default=None, ge=16, le=4096)
    checker: str | None = None
    float_eps: float | None = None
    partial: bool | None = None
    visible: bool | None = None
    points: int | None = Field(default=None, ge=1, le=10000)
    author: str | None = None


class TestsBody(BaseModel):
    tests: list[dict]


class ContestBody(BaseModel):
    slug: str
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    starts_at: str
    ends_at: str
    scoring: str = "icpc"
    penalty_minutes: int = Field(default=20, ge=0, le=1440)
    #: Minutes before the end when the scoreboard stops updating. 0 disables.
    freeze_minutes: int = Field(default=0, ge=0, le=1440)


class ContestProblemsBody(BaseModel):
    #: ``[{"slug": "a-plus-b", "label": "A"}, ...]`` — labels are assigned in
    #: order when omitted.
    problems: list[dict]


def _author_id(username: str | None, fallback: sqlite3.Row) -> int | None:
    """Resolve a username to credit, defaulting to whoever is authoring."""
    if not username:
        return fallback["id"]
    row = db.one("SELECT id FROM users WHERE username = ?", (username,))
    if row is None:
        raise HTTPException(status_code=404, detail=f"No such user: {username}")
    return row["id"]


@router.post("/problems")
def create_problem(body: ProblemBody, request: Request):
    _check_slug(body.slug)
    if body.checker not in checkers.CHECKERS:
        raise HTTPException(status_code=400, detail=f"Unknown checker: {body.checker}")
    author_id = _author_id(body.author, require_admin(request))
    try:
        problem_id = db.insert(
            "INSERT INTO problems (slug, title, statement, time_limit_ms,"
            " memory_limit_mb, checker, float_eps, partial, visible, points,"
            " author_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.slug,
                body.title,
                body.statement,
                body.time_limit_ms,
                body.memory_limit_mb,
                body.checker,
                body.float_eps,
                int(body.partial),
                int(body.visible),
                body.points,
                author_id,
                db.utcnow(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That slug is taken.") from None
    return {"id": problem_id, "slug": body.slug}


@router.patch("/problems/{slug}")
def update_problem(slug: str, body: ProblemPatch, request: Request):
    problem = _problem_or_404(slug)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        return {"updated": 0}
    if "checker" in fields and fields["checker"] not in checkers.CHECKERS:
        raise HTTPException(status_code=400, detail=f"Unknown checker: {fields['checker']}")
    if "author" in fields:
        # The column is author_id; the API speaks usernames.
        fields["author_id"] = _author_id(fields.pop("author"), require_admin(request))
    for key in ("partial", "visible"):
        if key in fields:
            fields[key] = int(fields[key])
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE problems SET {assignments} WHERE id = ?",
        (*fields.values(), problem["id"]),
    )
    return {"updated": len(fields)}


@router.delete("/problems/{slug}")
def delete_problem(slug: str):
    problem = _problem_or_404(slug)
    db.execute("DELETE FROM problems WHERE id = ?", (problem["id"],))
    testdata.delete_testdata(slug)
    return {"deleted": slug}


@router.get("/problems/{slug}/tests")
def list_tests(slug: str):
    problem = _problem_or_404(slug)
    rows = db.query(
        "SELECT idx, is_sample, points, input_path, answer_path FROM testcases"
        " WHERE problem_id = ? ORDER BY idx",
        (problem["id"],),
    )
    return {
        "tests": [
            {
                "idx": r["idx"],
                "is_sample": bool(r["is_sample"]),
                "points": r["points"],
                "input_path": r["input_path"],
                "answer_path": r["answer_path"],
            }
            for r in rows
        ]
    }


@router.put("/problems/{slug}/tests")
def replace_tests(slug: str, body: TestsBody):
    """Replace the whole test set from inline JSON."""
    problem = _problem_or_404(slug)
    try:
        count = testdata.replace_testcases(problem["id"], slug, body.tests)
    except testdata.TestDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"tests": count}


@router.post("/problems/{slug}/tests/upload")
async def upload_tests(
    slug: str,
    archive: UploadFile = File(...),
    samples: int = Form(default=0),
):
    """Replace the whole test set from a zip of ``1.in`` / ``1.out`` pairs."""
    problem = _problem_or_404(slug)
    data = await archive.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive is too large.")
    try:
        tests = testdata.parse_zip(data)
        for test in tests[: max(0, samples)]:
            test["is_sample"] = True
        count = testdata.replace_testcases(problem["id"], slug, tests)
    except testdata.TestDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"tests": count}


@router.post("/testdata/inspect")
async def inspect_testdata(archive: UploadFile = File(...)):
    """Parse a test-data archive without touching the database.

    Authoring order is naturally test-data-first: you have the tests before you
    have a slug. Validating up front means a malformed zip is rejected while
    nothing exists yet, instead of leaving behind a problem with no tests.
    """
    data = await archive.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive is too large.")
    try:
        tests = testdata.parse_zip(data)
    except testdata.TestDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    first = tests[0]
    return {
        "tests": len(tests),
        "samples": sum(1 for t in tests if t["is_sample"]),
        "bytes": sum(len(t["input"]) + len(t["output"]) for t in tests),
        # Enough of the first case to eyeball that the pairing is right.
        "preview": {
            "input": first["input"][:400],
            "output": first["output"][:400],
        },
    }


@router.post("/contests")
def create_contest(body: ContestBody):
    _check_slug(body.slug)
    if body.scoring not in ("icpc", "ioi"):
        raise HTTPException(status_code=400, detail="Scoring must be 'icpc' or 'ioi'.")
    try:
        starts = db.parse_time(body.starts_at)
        ends = db.parse_time(body.ends_at)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Times must be ISO-8601, e.g. 2026-08-10T18:00:00Z."
        ) from None
    if ends <= starts:
        raise HTTPException(status_code=400, detail="The contest must end after it starts.")
    if body.freeze_minutes * 60 >= (ends - starts).total_seconds():
        raise HTTPException(
            status_code=400,
            detail="The freeze would start before the contest does.",
        )

    def iso(value) -> str:
        return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    try:
        contest_id = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, freeze_minutes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.slug,
                body.title,
                body.description,
                iso(starts),
                iso(ends),
                body.scoring,
                body.penalty_minutes,
                body.freeze_minutes,
                db.utcnow(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That slug is taken.") from None
    return {"id": contest_id, "slug": body.slug}


@router.put("/contests/{slug}/problems")
def set_contest_problems(slug: str, body: ContestProblemsBody):
    contest = get_contest(slug)
    entries = []
    for position, item in enumerate(body.problems):
        problem = db.one("SELECT id FROM problems WHERE slug = ?", (item.get("slug"),))
        if problem is None:
            raise HTTPException(
                status_code=404, detail=f"No such problem: {item.get('slug')}"
            )
        label = item.get("label") or _label_for(position)
        entries.append((contest["id"], problem["id"], label))

    labels = [e[2] for e in entries]
    if len(set(labels)) != len(labels):
        raise HTTPException(status_code=400, detail="Labels must be unique.")

    with db.transaction() as conn:
        conn.execute("DELETE FROM contest_problems WHERE contest_id = ?", (contest["id"],))
        conn.executemany(
            "INSERT INTO contest_problems (contest_id, problem_id, label)"
            " VALUES (?, ?, ?)",
            entries,
        )
    return {"problems": len(entries)}


def _label_for(position: int) -> str:
    label = ""
    position += 1
    while position:
        position, remainder = divmod(position - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


@router.delete("/contests/{slug}")
def delete_contest(slug: str):
    contest = get_contest(slug)
    db.execute("DELETE FROM contests WHERE id = ?", (contest["id"],))
    return {"deleted": slug}


@router.post("/rejudge")
def rejudge(problem: str | None = None, submission_id: int | None = None):
    """Send submissions back through the judge, e.g. after fixing test data."""
    if submission_id is not None:
        rows = db.execute(
            "UPDATE submissions SET verdict = ?, message = '' WHERE id = ?",
            (PENDING, submission_id),
        ).rowcount
    elif problem is not None:
        row = _problem_or_404(problem)
        rows = db.execute(
            "UPDATE submissions SET verdict = ?, message = '' WHERE problem_id = ?",
            (PENDING, row["id"]),
        ).rowcount
    else:
        raise HTTPException(
            status_code=400, detail="Pass either `problem` or `submission_id`."
        )
    worker.notify()
    return {"requeued": rows}


@router.get("/users")
def list_users():
    rows = db.query("SELECT id, username, role, created_at FROM users ORDER BY id")
    return {"users": [dict(r) for r in rows]}


@router.post("/users/{username}/role")
def set_role(username: str, role: str):
    if role not in ("user", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'user' or 'admin'.")
    changed = db.execute(
        "UPDATE users SET role = ? WHERE username = ?", (role, username)
    ).rowcount
    if not changed:
        raise HTTPException(status_code=404, detail="No such user.")
    return {"username": username, "role": role}
