"""Admin endpoints: authoring problems, test data, contests, rejudging."""

from __future__ import annotations

import io
import os
import pathlib
import re
import secrets
import sqlite3
import time
import zipfile

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

from .. import config, contest as contest_mod, db, express, testdata
from ..judge import checkers, languages, worker
from ..judge.runner import JUDGING, PENDING, validate_source
from .deps import get_contest, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9 +/-]{0,31}$")
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
    #: Categories to file it under, e.g. ``["graphs", "dp"]``.
    types: list[str] = []


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
    types: list[str] | None = None


class LimitsBody(BaseModel):
    #: ``{"python3": {"time_limit_ms": 6500, "memory_limit_mb": 256}, ...}``.
    #: A language left out keeps the scaled fallback; an explicit null clears
    #: its override and returns it to the fallback.
    limits: dict[str, dict | None]


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
    rated: bool = False


class ContestPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    scoring: str | None = None
    penalty_minutes: int | None = Field(default=None, ge=0, le=1440)
    freeze_minutes: int | None = Field(default=None, ge=0, le=1440)
    rated: bool | None = None


class PostBody(BaseModel):
    slug: str
    title: str = Field(min_length=1, max_length=200)
    body: str = ""
    pinned: bool = False
    published: bool = True


class PostPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = None
    pinned: bool | None = None
    published: bool | None = None


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


def _clean_types(types: list[str]) -> list[str]:
    """Fold case and spacing so that 'Graphs' and 'graphs' filter as one type."""
    cleaned = []
    for raw in types:
        value = " ".join(raw.lower().split())
        if not value:
            continue
        if not TYPE_RE.match(value):
            raise HTTPException(status_code=400, detail=f"Bad problem type: {raw}")
        cleaned.append(value)
    return cleaned


def _set_types(problem_id: int, cleaned: list[str]) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM problem_types WHERE problem_id = ?", (problem_id,))
        conn.executemany(
            "INSERT OR IGNORE INTO problem_types (problem_id, type) VALUES (?, ?)",
            [(problem_id, t) for t in cleaned],
        )


@router.post("/problems")
def create_problem(body: ProblemBody, request: Request):
    _check_slug(body.slug)
    if body.checker not in checkers.CHECKERS:
        raise HTTPException(status_code=400, detail=f"Unknown checker: {body.checker}")
    types = _clean_types(body.types)
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
    _set_types(problem_id, types)
    return {"id": problem_id, "slug": body.slug}


@router.patch("/problems/{slug}")
def update_problem(slug: str, body: ProblemPatch, request: Request):
    problem = _problem_or_404(slug)
    fields = body.model_dump(exclude_none=True)
    types = fields.pop("types", None)

    # Validate everything before writing anything. Types live in their own
    # table, so applying them first meant a patch that went on to be rejected
    # still changed the problem — the admin sees an error and reasonably
    # assumes nothing happened.
    if "checker" in fields and fields["checker"] not in checkers.CHECKERS:
        raise HTTPException(status_code=400, detail=f"Unknown checker: {fields['checker']}")
    if "author" in fields:
        # The column is author_id; the API speaks usernames.
        fields["author_id"] = _author_id(fields.pop("author"), require_admin(request))
    cleaned_types = None if types is None else _clean_types(types)

    if cleaned_types is not None:
        _set_types(problem["id"], cleaned_types)
    if not fields:
        # `types` is a real change even though it touches no column here.
        return {"updated": 0 if cleaned_types is None else 1}
    for key in ("partial", "visible"):
        if key in fields:
            fields[key] = int(fields[key])
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE problems SET {assignments} WHERE id = ?",
        (*fields.values(), problem["id"]),
    )
    return {"updated": len(fields) + (0 if cleaned_types is None else 1)}


@router.get("/problems/{slug}/impact")
def deletion_impact(slug: str):
    """What deleting this problem would destroy.

    `submissions.problem_id` cascades, so removing a problem silently takes
    every attempt at it with it — including other people's, and any contest
    result that depended on them. A confirm dialog should say how much.
    """
    problem = _problem_or_404(slug)
    counts = db.one(
        "SELECT COUNT(*) AS submissions, COUNT(DISTINCT user_id) AS users"
        "  FROM submissions WHERE problem_id = ?",
        (problem["id"],),
    )
    contests = db.query(
        "SELECT c.slug, c.title FROM contest_problems cp"
        "  JOIN contests c ON c.id = cp.contest_id"
        " WHERE cp.problem_id = ? ORDER BY c.starts_at",
        (problem["id"],),
    )
    return {
        "slug": slug,
        "submissions": counts["submissions"],
        "users": counts["users"],
        "contests": [{"slug": c["slug"], "title": c["title"]} for c in contests],
    }


@router.delete("/problems/{slug}")
def delete_problem(slug: str):
    problem = _problem_or_404(slug)
    db.execute("DELETE FROM problems WHERE id = ?", (problem["id"],))
    testdata.delete_testdata(slug)
    return {"deleted": slug}


@router.get("/problems/{slug}/limits")
def get_limits(slug: str):
    """Every language's limit, and whether it was set or merely derived."""
    problem = _problem_or_404(slug)
    stored = {
        r["language"]: r
        for r in db.query(
            "SELECT language, time_limit_ms, memory_limit_mb FROM problem_limits"
            " WHERE problem_id = ?",
            (problem["id"],),
        )
    }
    out = {}
    for lang in languages.LANGUAGES.values():
        row = stored.get(lang.id)
        out[lang.id] = {
            "name": lang.name,
            "time_limit_ms": row["time_limit_ms"] if row
            else lang.effective_time_limit_ms(problem["time_limit_ms"]),
            "memory_limit_mb": row["memory_limit_mb"] if row
            else lang.effective_memory_limit_mb(problem["memory_limit_mb"]),
            "measured": row is not None,
        }
    return {"slug": slug, "limits": out}


@router.put("/problems/{slug}/limits")
def set_limits(slug: str, body: LimitsBody):
    """Replace the per-language limits.

    Set from a measured run of the intended solution in each language, which is
    the only thing that knows how far apart the runtimes really are on *this*
    problem — a fixed multiplier assumes a ratio that a problem heavy in
    interpreted-loop work will blow straight through.
    """
    problem = _problem_or_404(slug)
    unknown = sorted(set(body.limits) - set(languages.LANGUAGES))
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"Unknown language(s): {', '.join(unknown)}"
        )

    writes, clears = [], []
    for lang_id, values in body.limits.items():
        if values is None:
            clears.append(lang_id)
            continue
        try:
            time_ms = int(values["time_limit_ms"])
            memory_mb = int(values["memory_limit_mb"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"{lang_id} needs both time_limit_ms and memory_limit_mb.",
            ) from None
        # The same bounds the base limits use, so a per-language value cannot
        # smuggle in something the problem form would have refused.
        if not 100 <= time_ms <= 60_000:
            raise HTTPException(
                status_code=400,
                detail=f"{lang_id}: time limit must be 100-60000 ms.")
        if not 16 <= memory_mb <= 4096:
            raise HTTPException(
                status_code=400,
                detail=f"{lang_id}: memory limit must be 16-4096 MiB.")
        writes.append((problem["id"], lang_id, time_ms, memory_mb))

    with db.transaction() as conn:
        for lang_id in clears:
            conn.execute(
                "DELETE FROM problem_limits WHERE problem_id = ? AND language = ?",
                (problem["id"], lang_id),
            )
        conn.executemany(
            "INSERT INTO problem_limits"
            " (problem_id, language, time_limit_ms, memory_limit_mb)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (problem_id, language) DO UPDATE SET"
            "   time_limit_ms = excluded.time_limit_ms,"
            "   memory_limit_mb = excluded.memory_limit_mb",
            writes,
        )
    return {"set": len(writes), "cleared": len(clears)}


@router.get("/problems/{slug}/tests")
def list_tests(slug: str):
    problem = _problem_or_404(slug)
    rows = db.query(
        "SELECT idx, is_sample, points, subtask, input_path, answer_path FROM testcases"
        " WHERE problem_id = ? ORDER BY idx",
        (problem["id"],),
    )
    return {
        "tests": [
            {
                "idx": r["idx"],
                "is_sample": bool(r["is_sample"]),
                "points": r["points"],
                "subtask": r["subtask"],
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
    archive: UploadFile | None = File(default=None),
    token: str = Form(default=""),
    samples: int = Form(default=0),
):
    """Replace the whole test set from a zip of ``1.in`` / ``1.out`` pairs.

    Either send the archive, or send the ``token`` from a prior inspect and the
    already-uploaded bytes are reused.
    """
    problem = _problem_or_404(slug)
    if token:
        staged = _staged_path(token)
        if not staged.exists():
            raise HTTPException(
                status_code=410,
                detail="That archive is no longer staged. Choose the file again.",
            )
        data = staged.read_bytes()
    elif archive is not None:
        data = await archive.read(MAX_UPLOAD_BYTES + 1)
    else:
        raise HTTPException(status_code=400, detail="No archive given.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive is too large.")
    try:
        parsed = testdata.parse_zip(data)
        for test in parsed.tests[: max(0, samples)]:
            test["is_sample"] = True
        count = testdata.replace_testcases(
            problem["id"], slug, parsed.tests, parsed.subtasks
        )
    except testdata.TestDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if token:
        _staged_path(token).unlink(missing_ok=True)
    return {"tests": count, "subtasks": parsed.subtasks}


#: Filename suffix -> language id. What a solution is written in is already
#: recorded in its extension, so asking the uploader to say again invites the
#: two to disagree.
SOURCE_LANGUAGES = {
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".py": "python3",
    ".java": "java",
}

#: A calibration run is three or four files. This is a guard against a stray
#: archive, not a limit anyone should meet in normal use.
MAX_BULK_FILES = 20


@router.post("/problems/{slug}/bulk-submit")
async def bulk_submit(
    slug: str,
    archive: UploadFile = File(...),
    user: sqlite3.Row = Depends(require_admin),
):
    """Queue every source file in a zip as its own submission.

    Setting a problem's time and memory limits means running the intended
    solution in each language and reading off what it used. Done by hand that
    is: open the editor, paste, submit, wait, repeat — three times per problem,
    and again after every change to the test data.

    Deliberately exempt from the in-flight cap and the submit rate limiter.
    Both exist to stop one account flooding the queue, and both would reject
    the second half of an archive that is doing exactly what was asked. The
    file count is the bound instead.

    Always submitted as practice, never into a contest: these runs are
    calibration, and they have no business on a scoreboard.
    """
    problem = _problem_or_404(slug)

    data = await archive.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive is too large.")
    try:
        bundle = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="That is not a zip file.") from None

    submitted, skipped = _queue_sources(problem, bundle, user)
    if not submitted:
        detail = "; ".join(f"{s['file']}: {s['reason']}" for s in skipped[:4])
        raise HTTPException(
            status_code=400,
            detail=f"No submittable source files in that archive. {detail}".strip(),
        )

    worker.notify()
    return {"submitted": submitted, "skipped": skipped}


def _queue_sources(
    problem: sqlite3.Row,
    bundle: zipfile.ZipFile,
    user: sqlite3.Row,
    infos: list[zipfile.ZipInfo] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Insert one practice submission per source file. Returns (queued, skipped).

    Shared by the bulk upload and by express creation, which passes the
    `solutions/` entries of a larger package as `infos`. Neither notifies the
    worker — the caller does, once, after it has finished writing.
    """
    submitted: list[dict] = []
    skipped: list[dict] = []

    if infos is None:
        infos = bundle.infolist()
    for info in sorted(infos, key=lambda i: i.filename):
        name = pathlib.PurePosixPath(info.filename).name
        if info.is_dir() or name.startswith(".") or "__MACOSX" in info.filename:
            continue
        language_id = SOURCE_LANGUAGES.get(pathlib.PurePosixPath(name).suffix.lower())
        if language_id is None:
            # Solution archives routinely carry a README or a stray .class.
            # Those are not failures, so they are reported but not shouted about.
            skipped.append({"file": name, "reason": "not a recognised source file"})
            continue
        if len(submitted) >= MAX_BULK_FILES:
            skipped.append({"file": name, "reason": f"over the {MAX_BULK_FILES}-file limit"})
            continue
        # Checked before reading, so a compressed bomb never reaches memory.
        if info.file_size > config.MAX_SOURCE_BYTES:
            skipped.append({"file": name, "reason": "source is too large"})
            continue
        if not languages.is_available(language_id):
            skipped.append(
                {"file": name, "reason": f"{languages.get(language_id).name} is not installed"}
            )
            continue

        source = bundle.read(info).decode("utf-8", "replace")
        rejection = validate_source(language_id, source)
        if rejection:
            skipped.append({"file": name, "reason": rejection})
            continue

        submission_id = db.insert(
            "INSERT INTO submissions (user_id, problem_id, contest_id, language,"
            " source, verdict, created_at) VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (user["id"], problem["id"], language_id, source, PENDING, db.utcnow()),
        )
        submitted.append({"file": name, "language": language_id, "id": submission_id})

    return submitted, skipped


@router.post("/problems/express")
async def express_create(
    archive: UploadFile = File(...),
    user: sqlite3.Row = Depends(require_admin),
):
    """Create a whole problem from one package, and start its calibration runs.

    The manual route is four screens — the form, the test upload, the editor
    once per language — and the last three only exist to measure limits that
    the first one has to be revisited to record. This is all of it in one file:
    metadata, statement, test data, intended solutions.

    Always hidden, always calibrated. The submissions go in as practice runs
    the moment the problem exists, so the numbers needed to set the limits are
    already being measured while the author is still reading the confirmation.
    """
    data = await archive.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive is too large.")

    try:
        package = express.parse_package(data)
    except express.ExpressError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # Everything that can be checked without writing is checked first: a
    # package rejected here leaves nothing behind to clean up.
    _check_slug(package.slug)
    types = _clean_types(package.types)
    author_id = _author_id(package.author, user)
    try:
        parsed = testdata.parse_zip(data, prefix=express.TESTS_DIR)
    except testdata.TestDataError as exc:
        raise HTTPException(status_code=400, detail=f"{express.TESTS_DIR}: {exc}") from None

    try:
        problem_id = db.insert(
            "INSERT INTO problems (slug, title, statement, time_limit_ms,"
            " memory_limit_mb, checker, float_eps, partial, visible, points,"
            " author_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (
                package.slug,
                package.title,
                package.statement,
                package.time_limit_ms,
                package.memory_limit_mb,
                package.checker,
                package.float_eps,
                int(package.partial),
                package.points,
                author_id,
                db.utcnow(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That slug is taken.") from None

    # From here on the problem exists, so every failure has to take it back
    # out again rather than leave a testless half-problem in the list.
    try:
        _set_types(problem_id, types)
        try:
            tests = testdata.replace_testcases(
                problem_id, package.slug, parsed.tests, parsed.subtasks
            )
        except testdata.TestDataError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        problem = _problem_or_404(package.slug)
        submitted, skipped = _queue_sources(
            problem, package.bundle, user, package.solutions
        )
        if not submitted:
            detail = "; ".join(f"{s['file']}: {s['reason']}" for s in skipped[:4])
            raise HTTPException(
                status_code=400,
                detail=f"Nothing in {express.SOLUTIONS_DIR} could be submitted. "
                f"{detail}".strip(),
            )
    except Exception:
        db.execute("DELETE FROM problems WHERE id = ?", (problem_id,))
        testdata.delete_testdata(package.slug)
        raise

    worker.notify()
    return {
        "slug": package.slug,
        "title": package.title,
        "points": package.points,
        "types": types,
        "checker": package.checker,
        "partial": package.partial,
        "visible": False,
        "tests": tests,
        "samples": sum(1 for t in parsed.tests if t["is_sample"]),
        "subtasks": parsed.subtasks,
        "submitted": submitted,
        "skipped": skipped,
    }


@router.get("/problems/{slug}/express-report")
def express_report(slug: str, ids: str = ""):
    """The calibration runs' results, formatted for copying out.

    `ids` names the submissions the express upload started. Without it the
    latest run per language is used, so the report survives a reload of the
    page that was waiting on it.
    """
    problem = _problem_or_404(slug)
    wanted = [int(part) for part in ids.replace(",", " ").split() if part.strip().isdigit()]
    if wanted:
        placeholders = ", ".join("?" * len(wanted))
        rows = db.query(
            f"SELECT * FROM submissions WHERE problem_id = ? AND id IN ({placeholders})"
            " ORDER BY id",
            (problem["id"], *wanted),
        )
    else:
        rows = db.query(
            "SELECT * FROM submissions s WHERE s.problem_id = ? AND s.id ="
            "   (SELECT MAX(id) FROM submissions WHERE problem_id = s.problem_id"
            "     AND language = s.language)"
            " ORDER BY s.id",
            (problem["id"],),
        )

    limits = get_limits(slug)["limits"]
    report_rows = []
    for row in rows:
        limit = limits.get(row["language"], {})
        report_rows.append({
            "id": row["id"],
            "language": row["language"],
            "verdict": row["verdict"],
            "judged": row["verdict"] not in (PENDING, JUDGING),
            "score_percent": row["earned_percent"],
            "time_ms": row["time_ms"],
            "memory_kb": row["memory_kb"],
            "limit_time_ms": limit.get("time_limit_ms", problem["time_limit_ms"]),
            "limit_memory_mb": limit.get("memory_limit_mb", problem["memory_limit_mb"]),
            "measured": limit.get("measured", False),
        })
    # Language order, not submission order: the report is read as a comparison
    # between runtimes, and the archive's filenames decide the latter. The
    # default language leads, because every other row is judged against it.
    order = [languages.DEFAULT_LANGUAGE] + [
        lang for lang in languages.LANGUAGES if lang != languages.DEFAULT_LANGUAGE
    ]
    report_rows.sort(key=lambda r: (order.index(r["language"])
                                    if r["language"] in order else len(order), r["id"]))

    counts = db.one(
        "SELECT COUNT(*) AS tests, COALESCE(SUM(is_sample), 0) AS samples"
        "  FROM testcases WHERE problem_id = ?",
        (problem["id"],),
    )
    subtasks = db.one(
        "SELECT COUNT(*) AS n FROM problem_subtasks WHERE problem_id = ?",
        (problem["id"],),
    )
    summary = {
        "slug": slug,
        "title": problem["title"],
        "points": problem["points"],
        "partial": bool(problem["partial"]),
        "tests": counts["tests"],
        "samples": counts["samples"],
        "subtasks": subtasks["n"],
        "time_limit_ms": problem["time_limit_ms"],
        "memory_limit_mb": problem["memory_limit_mb"],
    }
    pending = [r for r in report_rows if not r["judged"]]
    return {
        "done": bool(report_rows) and not pending,
        "pending": len(pending),
        "rows": report_rows,
        "summary": summary,
        "report": express.format_report(summary, report_rows),
    }


class ExpressLimitsBody(BaseModel):
    #: The pasted block. See `express.parse_limits` for the grammar.
    text: str


@router.post("/limits/express")
def express_limits(body: ExpressLimitsBody):
    """Apply a pasted limits block: the base limit and any per-language ones.

    The paste names its own problem, so this is one endpoint rather than a
    slug in the path — the numbers and the problem they belong to travel
    together, and pasting yesterday's block into today's problem cannot happen.
    """
    try:
        plan = express.parse_limits(body.text)
    except express.ExpressError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    problem = _problem_or_404(plan.slug)
    writes = [
        (problem["id"], lang, pair[0], pair[1])
        for lang, pair in plan.languages.items()
        if pair is not None
    ]
    clears = [lang for lang, pair in plan.languages.items() if pair is None]

    with db.transaction() as conn:
        if plan.base is not None:
            conn.execute(
                "UPDATE problems SET time_limit_ms = ?, memory_limit_mb = ? WHERE id = ?",
                (plan.base[0], plan.base[1], problem["id"]),
            )
        for lang in clears:
            conn.execute(
                "DELETE FROM problem_limits WHERE problem_id = ? AND language = ?",
                (problem["id"], lang),
            )
        conn.executemany(
            "INSERT INTO problem_limits"
            " (problem_id, language, time_limit_ms, memory_limit_mb)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (problem_id, language) DO UPDATE SET"
            "   time_limit_ms = excluded.time_limit_ms,"
            "   memory_limit_mb = excluded.memory_limit_mb",
            writes,
        )

    return {
        "slug": plan.slug,
        "base": None if plan.base is None
        else {"time_limit_ms": plan.base[0], "memory_limit_mb": plan.base[1]},
        "set": [w[1] for w in writes],
        "cleared": clears,
        "limits": get_limits(plan.slug)["limits"],
    }


#: Inspected archives wait here for the Create button. A real test set runs to
#: tens of megabytes, and uploading it once to validate and again to store
#: doubles the slowest step of authoring a problem.
STAGING_DIR = config.DATA_DIR / "staging"

#: How long a staged archive survives. Long enough to write a statement around
#: it, short enough that an abandoned upload does not sit on disk forever.
STAGING_TTL_S = 6 * 60 * 60

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")


def _sweep_staging() -> None:
    """Delete staged archives past their welcome. Called on each new stage, so
    there is no background task to supervise."""
    cutoff = time.time() - STAGING_TTL_S
    try:
        for path in STAGING_DIR.glob("*.zip"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
    except OSError:
        pass  # housekeeping must never fail an upload


def _staged_path(token: str) -> pathlib.Path:
    # The token reaches us from the client, so it names a file only after it is
    # proven to be 32 hex characters and nothing else.
    if not TOKEN_RE.match(token):
        raise HTTPException(status_code=400, detail="Malformed archive token.")
    return STAGING_DIR / f"{token}.zip"


@router.post("/testdata/inspect")
async def inspect_testdata(archive: UploadFile = File(...)):
    """Parse a test-data archive without touching the database.

    Authoring order is naturally test-data-first: you have the tests before you
    have a slug. Validating up front means a malformed zip is rejected while
    nothing exists yet, instead of leaving behind a problem with no tests.

    The bytes are kept under a token so that creating the problem can reuse
    them instead of asking for the whole archive a second time.
    """
    data = await archive.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Archive is too large.")
    try:
        parsed = testdata.parse_zip(data)
    except testdata.TestDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    token = secrets.token_hex(16)
    try:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STAGING_DIR, 0o700)
        _sweep_staging()
        _staged_path(token).write_bytes(data)
    except OSError:
        # Staging is an optimisation. If the disk says no, the client still has
        # the file and can upload it again with the problem.
        token = ""

    tests = parsed.tests
    first = tests[0]
    return {
        "tests": len(tests),
        "samples": sum(1 for t in tests if t["is_sample"]),
        "subtasks": parsed.subtasks,
        "bytes": sum(len(t["input"]) + len(t["output"]) for t in tests),
        "token": token,
        # Enough of the first case to eyeball that the pairing is right.
        "preview": {
            "input": first["input"][:400],
            "output": first["output"][:400],
        },
    }


def _iso(value) -> str:
    """The one timestamp format stored for contests.

    The scoreboard filters submissions with a string comparison against these
    columns, so a contest written in any other ISO spelling would silently
    include or drop rows at the edges of its window.
    """
    return value.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _validated_window(starts_at: str, ends_at: str, freeze_minutes: int) -> tuple[str, str]:
    try:
        starts = db.parse_time(starts_at)
        ends = db.parse_time(ends_at)
    except ValueError:
        raise HTTPException(
            status_code=400, detail="Times must be ISO-8601, e.g. 2026-08-10T18:00:00Z."
        ) from None
    if ends <= starts:
        raise HTTPException(status_code=400, detail="The contest must end after it starts.")
    if freeze_minutes * 60 >= (ends - starts).total_seconds():
        raise HTTPException(
            status_code=400,
            detail="The freeze would start before the contest does.",
        )
    return _iso(starts), _iso(ends)


@router.post("/contests")
def create_contest(body: ContestBody):
    _check_slug(body.slug)
    if body.scoring not in ("icpc", "ioi"):
        raise HTTPException(status_code=400, detail="Scoring must be 'icpc' or 'ioi'.")
    starts_iso, ends_iso = _validated_window(
        body.starts_at, body.ends_at, body.freeze_minutes
    )
    try:
        contest_id = db.insert(
            "INSERT INTO contests (slug, title, description, starts_at, ends_at,"
            " scoring, penalty_minutes, freeze_minutes, rated, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.slug,
                body.title,
                body.description,
                starts_iso,
                ends_iso,
                body.scoring,
                body.penalty_minutes,
                body.freeze_minutes,
                int(body.rated),
                db.utcnow(),
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That slug is taken.") from None
    return {"id": contest_id, "slug": body.slug}


@router.patch("/contests/{slug}")
def update_contest(slug: str, body: ContestPatch):
    """Amend a contest in place.

    Without this the only way to fix a wrong start time is to delete and
    recreate, and deleting a contest sets `contest_id` to NULL on every
    submission made during it — the scoreboard cannot be rebuilt afterwards.
    Editing is the non-destructive path, so it validates the *merged* window
    rather than whichever half the caller happened to send.
    """
    contest = get_contest(slug)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        return {"updated": 0, "slug": slug}
    if "scoring" in fields and fields["scoring"] not in ("icpc", "ioi"):
        raise HTTPException(status_code=400, detail="Scoring must be 'icpc' or 'ioi'.")

    if {"starts_at", "ends_at", "freeze_minutes"} & fields.keys():
        starts_iso, ends_iso = _validated_window(
            fields.get("starts_at", contest["starts_at"]),
            fields.get("ends_at", contest["ends_at"]),
            fields.get("freeze_minutes", contest["freeze_minutes"]),
        )
        if "starts_at" in fields:
            fields["starts_at"] = starts_iso
        if "ends_at" in fields:
            fields["ends_at"] = ends_iso

    if "rated" in fields:
        fields["rated"] = int(fields["rated"])

    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE contests SET {assignments} WHERE id = ?",
        (*fields.values(), contest["id"]),
    )

    # Anything here can change who was rated or by how much: flipping `rated`
    # obviously, but also moving the window, since the window decides which
    # submissions counted and therefore the standings the rating came from.
    rebuilt = None
    if {"rated", "starts_at", "ends_at", "scoring", "penalty_minutes"} & fields.keys():
        rebuilt = contest_mod.recompute_ratings()
    return {"updated": len(fields), "slug": slug, "ratings": rebuilt}


@router.post("/ratings/recompute")
def recompute_ratings():
    """Rebuild every rating by replaying every rated contest.

    Ratings are derived data, so this is never destructive and always the
    correct repair: if a result is wrong, fix the contest and replay.
    """
    return contest_mod.recompute_ratings()


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


def _post_or_404(slug: str) -> sqlite3.Row:
    row = db.one("SELECT * FROM posts WHERE slug = ?", (slug,))
    if row is None:
        raise HTTPException(status_code=404, detail="No such post.")
    return row


@router.post("/posts")
def create_post(body: PostBody, request: Request):
    _check_slug(body.slug)
    now = db.utcnow()
    try:
        post_id = db.insert(
            "INSERT INTO posts (slug, title, body, author_id, pinned, published,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                body.slug,
                body.title,
                body.body,
                require_admin(request)["id"],
                int(body.pinned),
                int(body.published),
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="That slug is taken.") from None
    return {"id": post_id, "slug": body.slug}


@router.patch("/posts/{slug}")
def update_post(slug: str, body: PostPatch):
    post = _post_or_404(slug)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        return {"updated": 0, "slug": slug}
    for key in ("pinned", "published"):
        if key in fields:
            fields[key] = int(fields[key])
    fields["updated_at"] = db.utcnow()
    assignments = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE posts SET {assignments} WHERE id = ?", (*fields.values(), post["id"])
    )
    return {"updated": len(fields), "slug": slug}


@router.delete("/posts/{slug}")
def delete_post(slug: str):
    post = _post_or_404(slug)
    db.execute("DELETE FROM posts WHERE id = ?", (post["id"],))
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
    target = db.one("SELECT id, role FROM users WHERE username = ?", (username,))
    if target is None:
        raise HTTPException(status_code=404, detail="No such user.")

    # Demoting the last admin is unrecoverable through the web interface:
    # every admin route would then reject everyone, including the person who
    # made the change. Getting back in would mean editing the database on the
    # server by hand.
    if target["role"] == "admin" and role == "user":
        remaining = db.one(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND id != ?",
            (target["id"],),
        )["n"]
        if remaining == 0:
            raise HTTPException(
                status_code=400,
                detail="This is the only admin. Promote someone else first,"
                " or nobody will be able to administer the judge.",
            )

    db.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    return {"username": username, "role": role}
