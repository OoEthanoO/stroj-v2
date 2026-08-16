"""Express authoring: one zip in, one paste back.

The value of the format is that an author never assembles a problem twice, so
these lean on the failure paths: a package that is wrong must leave nothing
behind, and a paste that is wrong must change nothing.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from stroj import db, express
from stroj.judge import languages

CPP = "#include <cstdio>\nint main(){int a,b;scanf(\"%d %d\",&a,&b);printf(\"%d\\n\",a+b);}\n"
PY = "a, b = input().split()\nprint(int(a) + int(b))\n"
JAVA = ("public class Main { public static void main(String[] a) {"
        " java.util.Scanner s = new java.util.Scanner(System.in);"
        " System.out.println(s.nextInt() + s.nextInt()); } }\n")

MANIFEST = {
    "slug": "repair-shop",
    "title": "Repair Shop",
    "points": 400,
    "types": ["greedy", "Sorting"],
    "checker": "token",
    "partial": True,
    "time_limit_ms": 1500,
    "memory_limit_mb": 512,
}


def package(manifest=MANIFEST, *, statement="Accept as many orders as you can.",
            tests=None, solutions=None, extra=None) -> bytes:
    """An express zip. Defaults are a valid package; arguments break one part."""
    if tests is None:
        tests = {
            "tests/sample1.in": "2 3\n", "tests/sample1.ans": "5\n",
            "tests/subtask1-40/big.in": "1 1\n", "tests/subtask1-40/big.ans": "2\n",
            "tests/subtask2-60/huge.in": "4 4\n", "tests/subtask2-60/huge.ans": "8\n",
        }
    if solutions is None:
        solutions = {"solutions/sol.cpp": CPP, "solutions/main.py": PY,
                     "solutions/Main.java": JAVA}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if manifest is not None:
            archive.writestr(express.MANIFEST, json.dumps(manifest))
        if statement is not None:
            archive.writestr(express.STATEMENT, statement)
        for name, body in {**tests, **solutions, **(extra or {})}.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def post(client, data: bytes):
    return client.post(
        "/api/admin/problems/express",
        files={"archive": ("package.zip", data, "application/zip")},
    )


@pytest.fixture(autouse=True)
def every_language_installed(monkeypatch):
    """These tests are about the format, not about which toolchains this
    machine happens to have; without this, a missing JDK silently drops a
    third of every calibration run."""
    monkeypatch.setattr(languages, "is_available", lambda language_id: True)


class TestThePackageFormat:
    """Pure parsing — no problem is created, so the messages are the product."""

    def parse(self, data: bytes) -> str:
        with pytest.raises(express.ExpressError) as caught:
            express.parse_package(data)
        return str(caught.value)

    def test_a_valid_package_describes_the_problem(self):
        parsed = express.parse_package(package())
        assert parsed.slug == "repair-shop"
        assert parsed.points == 400
        assert parsed.partial is True
        assert parsed.time_limit_ms == 1500
        assert [s.filename for s in parsed.solutions] == [
            "solutions/Main.java", "solutions/main.py", "solutions/sol.cpp"]

    def test_a_missing_manifest_says_what_it_holds(self):
        assert "problem.json" in self.parse(package(manifest=None))

    def test_a_misspelt_field_is_refused_rather_than_ignored(self):
        """`type` for `types` would otherwise ship a problem with no types and
        no complaint."""
        broken = {**MANIFEST, "type": ["greedy"]}
        message = self.parse(package(broken))
        assert "type" in message and "types" in message

    def test_visibility_is_not_the_authors_to_set(self):
        assert "hidden" in self.parse(package({**MANIFEST, "visible": True}))

    def test_the_float_checker_needs_its_epsilon_spelled_out(self):
        """Defaulting it means a problem that needs 1e-9 passes its own
        calibration and fails its solvers."""
        message = self.parse(package({**MANIFEST, "checker": "float"}))
        assert "float_eps" in message

    def test_a_float_problem_with_an_epsilon_is_fine(self):
        parsed = express.parse_package(
            package({**MANIFEST, "checker": "float", "float_eps": 1e-9}))
        assert parsed.checker == "float" and parsed.float_eps == 1e-9

    def test_an_out_of_range_number_is_caught_before_the_form_would(self):
        assert "between" in self.parse(package({**MANIFEST, "points": 0}))
        assert "between" in self.parse(package({**MANIFEST, "time_limit_ms": 99}))

    def test_a_package_needs_a_statement(self):
        assert "statement" in self.parse(package(statement=None))

    def test_the_statement_may_live_in_the_manifest_instead(self):
        parsed = express.parse_package(
            package({**MANIFEST, "statement": "Inline."}, statement=None))
        assert parsed.statement == "Inline."

    def test_a_package_needs_tests_and_solutions(self):
        assert "tests/" in self.parse(package(tests={}))
        assert "solutions/" in self.parse(package(solutions={}))

    def test_a_non_zip_is_not_a_package(self):
        assert "zip" in self.parse(b"this is not a zip")


class TestExpressCreation:
    def test_one_upload_produces_a_finished_hidden_problem(self, admin_client):
        response = post(admin_client, package())
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["slug"] == "repair-shop"
        assert body["visible"] is False
        assert body["tests"] == 3
        assert body["samples"] == 1
        assert body["subtasks"] == {"1": 40, "2": 60}

        row = db.one("SELECT * FROM problems WHERE slug = 'repair-shop'")
        assert row["visible"] == 0
        assert row["title"] == "Repair Shop"
        assert row["points"] == 400
        assert row["partial"] == 1
        assert row["time_limit_ms"] == 1500 and row["memory_limit_mb"] == 512
        assert "Accept as many orders" in row["statement"]

    def test_types_are_folded_the_way_the_form_folds_them(self, admin_client):
        post(admin_client, package()).raise_for_status()
        types = [r["type"] for r in db.query(
            "SELECT type FROM problem_types ORDER BY type")]
        assert types == ["greedy", "sorting"]

    def test_the_intended_solutions_are_submitted_immediately(self, admin_client):
        body = post(admin_client, package()).json()
        assert {s["language"] for s in body["submitted"]} == {"cpp", "python3", "java"}
        rows = db.query("SELECT language, contest_id, verdict FROM submissions")
        assert len(rows) == 3
        # Calibration is practice: these have no business on a scoreboard.
        assert all(r["contest_id"] is None for r in rows)
        assert all(r["verdict"] == "PENDING" for r in rows)

    def test_samples_and_subtasks_survive_the_nesting(self, admin_client):
        """The `tests/` directory is stripped, so subtask directories inside it
        group exactly as they would in a bare test archive."""
        post(admin_client, package()).raise_for_status()
        cases = db.query("SELECT idx, is_sample, subtask FROM testcases ORDER BY idx")
        assert [(c["is_sample"], c["subtask"]) for c in cases] == [(1, 0), (0, 1), (0, 2)]
        shares = db.query("SELECT idx, percent FROM problem_subtasks ORDER BY idx")
        assert [(s["idx"], s["percent"]) for s in shares] == [(1, 40), (2, 60)]

    def test_a_taken_slug_is_a_conflict_not_a_second_problem(self, admin_client):
        post(admin_client, package()).raise_for_status()
        response = post(admin_client, package())
        assert response.status_code == 409
        assert db.one("SELECT COUNT(*) AS n FROM problems")["n"] == 1

    def test_a_bad_slug_creates_nothing(self, admin_client):
        response = post(admin_client, package({**MANIFEST, "slug": "Repair Shop"}))
        assert response.status_code == 400
        assert db.one("SELECT COUNT(*) AS n FROM problems")["n"] == 0

    def test_broken_test_data_leaves_no_half_problem_behind(self, admin_client):
        """An input with no answer file is the usual packaging slip. The problem
        must not survive it: a testless problem in the list is worse than a
        rejected upload."""
        response = post(admin_client, package(tests={"tests/1.in": "2 3\n"}))
        assert response.status_code == 400
        assert "answer" in response.json()["detail"]
        assert db.one("SELECT COUNT(*) AS n FROM problems")["n"] == 0

    def test_unusable_solutions_roll_the_problem_back(self, admin_client):
        response = post(admin_client, package(
            solutions={"solutions/Solution.java": "public class Solution {}\n"}))
        assert response.status_code == 400
        assert "Main" in response.json()["detail"]
        assert db.one("SELECT COUNT(*) AS n FROM problems")["n"] == 0
        assert db.one("SELECT COUNT(*) AS n FROM submissions")["n"] == 0

    def test_readmes_riding_along_are_reported_not_fatal(self, admin_client):
        body = post(admin_client, package(
            extra={"solutions/README.md": "how it works"})).json()
        assert len(body["submitted"]) == 3
        assert [s["file"] for s in body["skipped"]] == ["README.md"]

    def test_only_admins_can_use_it(self, client):
        assert post(client, package()).status_code in (401, 403)


class TestTheCalibrationReport:
    def judge(self, language: str, *, time_ms: int, memory_kb: int) -> None:
        db.execute(
            "UPDATE submissions SET verdict = 'AC', earned_percent = 100,"
            " time_ms = ?, memory_kb = ?, judged_at = ? WHERE language = ?",
            (time_ms, memory_kb, db.utcnow(), language),
        )

    def report(self, client, ids=None):
        query = "" if ids is None else "?ids=" + ",".join(str(i) for i in ids)
        return client.get(
            f"/api/admin/problems/repair-shop/express-report{query}").json()

    def test_it_waits_while_the_runs_are_still_judging(self, admin_client):
        body = post(admin_client, package()).json()
        found = self.report(admin_client, [s["id"] for s in body["submitted"]])
        assert found["done"] is False
        assert found["pending"] == 3

    def test_it_reports_what_each_language_used(self, admin_client):
        body = post(admin_client, package()).json()
        self.judge("cpp", time_ms=34, memory_kb=9300)
        self.judge("python3", time_ms=212, memory_kb=68000)
        self.judge("java", time_ms=480, memory_kb=215000)

        found = self.report(admin_client, [s["id"] for s in body["submitted"]])
        assert found["done"] is True and found["pending"] == 0
        by_language = {r["language"]: r for r in found["rows"]}
        assert by_language["python3"]["time_ms"] == 212
        assert by_language["java"]["memory_kb"] == 215000

        report = found["report"]
        assert "repair-shop" in report and "Repair Shop" in report
        # The numbers a limit is chosen from, in the units limits are set in.
        assert "212 ms" in report and "66.4 MiB" in report
        assert "1500 ms / 512 MiB" in report      # the base limit as it stands
        assert "tests: 3 (1 sample, 2 subtasks)" in report

    def test_without_ids_it_falls_back_to_the_latest_run_per_language(self, admin_client):
        """The page waiting on the report may be closed and reopened; the
        report should still be there."""
        post(admin_client, package()).raise_for_status()
        for language in ("cpp", "python3", "java"):
            self.judge(language, time_ms=10, memory_kb=1024)
        found = self.report(admin_client)
        assert found["done"] is True
        assert {r["language"] for r in found["rows"]} == {"cpp", "python3", "java"}


class TestTheLimitsPaste:
    def apply(self, client, text: str):
        return client.post("/api/admin/limits/express", json={"text": text})

    def test_it_sets_the_base_limit_and_the_overrides(self, admin_client):
        post(admin_client, package()).raise_for_status()
        response = self.apply(admin_client, """
            # measured on the judge
            limits repair-shop
            base 1200ms 256MiB
            cpp 1200ms 256MiB
            python3 4s 320mb
            java 3000 640
        """)
        assert response.status_code == 200, response.text

        problem = db.one("SELECT * FROM problems WHERE slug = 'repair-shop'")
        assert problem["time_limit_ms"] == 1200 and problem["memory_limit_mb"] == 256
        rows = {r["language"]: (r["time_limit_ms"], r["memory_limit_mb"])
                for r in db.query("SELECT * FROM problem_limits")}
        assert rows == {"cpp": (1200, 256), "python3": (4000, 320), "java": (3000, 640)}

    def test_clear_returns_a_language_to_the_derived_limit(self, admin_client):
        post(admin_client, package()).raise_for_status()
        self.apply(admin_client, "limits repair-shop\npython3 4000ms 320MiB")
        body = self.apply(admin_client, "limits repair-shop\npython3 clear").json()
        assert body["cleared"] == ["python3"]
        assert db.one("SELECT COUNT(*) AS n FROM problem_limits")["n"] == 0

    def test_the_paste_names_its_own_problem(self, admin_client):
        """Numbers arrive by paste, long after the run that produced them.
        Applying them to whichever problem is open is the mistake worth making
        impossible."""
        post(admin_client, package()).raise_for_status()
        response = self.apply(admin_client, "limits some-other-problem\nbase 1000 256")
        assert response.status_code == 404
        assert db.one("SELECT time_limit_ms FROM problems")["time_limit_ms"] == 1500

    def test_a_malformed_block_changes_nothing(self, admin_client):
        post(admin_client, package()).raise_for_status()
        for text in ("", "base 1000 256", "limits repair-shop\nrust 1000 256",
                     "limits repair-shop\ncpp 50ms 256", "limits repair-shop\ncpp 1000 8",
                     "limits repair-shop\ncpp 1000"):
            assert self.apply(admin_client, text).status_code == 400, text
        assert db.one("SELECT time_limit_ms FROM problems")["time_limit_ms"] == 1500
        assert db.one("SELECT COUNT(*) AS n FROM problem_limits")["n"] == 0

    def test_units_are_optional_and_forgiving(self):
        plan = express.parse_limits(
            "limits x\nbase 2s 1gb\ncpp 1500 256\npython3 4000ms 320MiB")
        assert plan.base == (2000, 1024)
        assert plan.languages == {"cpp": (1500, 256), "python3": (4000, 320)}

    def test_a_block_that_sets_nothing_is_an_error(self):
        with pytest.raises(express.ExpressError):
            express.parse_limits("limits x")
