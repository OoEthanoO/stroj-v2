"""Storing a problem's test data on disk and registering it in the database."""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path

from . import config, db

INPUT_EXTENSIONS = {".in", ".input", ".dat"}
ANSWER_EXTENSIONS = {".out", ".ans", ".a", ".expected", ".sol"}


class TestDataError(Exception):
    """The uploaded test data could not be interpreted."""


def test_dir(slug: str) -> Path:
    return config.PROBLEM_DIR / slug / "tests"


def _natural_key(name: str) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", name)
    )


def parse_zip(data: bytes) -> list[dict]:
    """Pull ``name.in`` / ``name.out`` pairs out of a zip archive.

    Directory structure is ignored; files are paired by stem and ordered
    naturally, so ``2.in`` sorts before ``10.in``.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TestDataError("That file is not a readable zip archive.") from None

    inputs: dict[str, str] = {}
    answers: dict[str, str] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = Path(info.filename)
        if path.name.startswith(".") or "__MACOSX" in info.filename:
            continue
        suffix = path.suffix.lower()
        stem = path.stem
        if suffix in INPUT_EXTENSIONS:
            inputs[stem] = archive.read(info).decode("utf-8", "replace")
        elif suffix in ANSWER_EXTENSIONS:
            answers[stem] = archive.read(info).decode("utf-8", "replace")

    if not inputs:
        raise TestDataError(
            "No input files found. Expected pairs like 1.in / 1.out inside the zip."
        )
    missing = sorted(set(inputs) - set(answers), key=_natural_key)
    if missing:
        raise TestDataError(
            f"These inputs have no matching answer file: {', '.join(missing[:5])}"
        )

    def ordering(stem: str) -> tuple:
        # Samples first, then everything else naturally ordered. Judging stops
        # at the first failure on a non-partial problem, and only samples show
        # diagnostics — so samples running last would mean solvers almost never
        # see the feedback that exists for them.
        return (0 if "sample" in stem.lower() else 1, _natural_key(stem))

    return [
        {
            "input": inputs[stem],
            "output": answers[stem],
            "is_sample": "sample" in stem.lower(),
            "points": 0,
        }
        for stem in sorted(inputs, key=ordering)
    ]


def replace_testcases(problem_id: int, slug: str, tests: list[dict]) -> int:
    """Write ``tests`` to disk and make them the problem's complete test set."""
    if not tests:
        raise TestDataError("A problem needs at least one test case.")

    directory = test_dir(slug)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)

    rows = []
    for idx, test in enumerate(tests, start=1):
        input_path = directory / f"{idx:03d}.in"
        answer_path = directory / f"{idx:03d}.ans"
        input_path.write_text(str(test.get("input", "")), encoding="utf-8")
        answer_path.write_text(str(test.get("output", "")), encoding="utf-8")
        rows.append(
            (
                problem_id,
                idx,
                str(input_path),
                str(answer_path),
                1 if test.get("is_sample") else 0,
                int(test.get("points") or 0),
            )
        )

    with db.transaction() as conn:
        conn.execute("DELETE FROM testcases WHERE problem_id = ?", (problem_id,))
        conn.executemany(
            "INSERT INTO testcases"
            " (problem_id, idx, input_path, answer_path, is_sample, points)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def delete_testdata(slug: str) -> None:
    directory = config.PROBLEM_DIR / slug
    if directory.exists():
        shutil.rmtree(directory, ignore_errors=True)
