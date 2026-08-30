"""The parts of the frontend that Python can hold to account.

The LaTeX renderer is JavaScript with its own suite; this shells out to it so
that `pytest` still runs everything in one command. The rest guards the wiring
between `stroj/web/` and the two ways it gets served, which no JS test can see.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "stroj" / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_latex_renderer_suite():
    """Run tests/test_latex.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_latex.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_submission_view_suite():
    """Run tests/test_submission_view.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_submission_view.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_version_watch_suite():
    """Run tests/test_version_watch.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_version_watch.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_dmoj_table_suite():
    """Run tests/test_dmoj_table.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_dmoj_table.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_mentions_suite():
    """Run tests/test_mentions.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_mentions.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_activity_calendar_suite():
    """Run tests/test_activity_calendar.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_activity_calendar.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_rank_badge_suite():
    """Run tests/test_rank_badge.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_rank_badge.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_rating_graph_suite():
    """Run tests/test_rating_graph.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_rating_graph.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_problem_sort_suite():
    """Run tests/test_problem_sort.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_problem_sort.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_problem_contests_suite():
    """Run tests/test_problem_contests.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_problem_contests.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_every_script_parses():
    for script in sorted(WEB.glob("*.js")):
        result = subprocess.run(
            ["node", "--check", str(script)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


class TestTheDeployedFrontendIsComplete:
    """`build-static.sh` copies files by an explicit list, and the Vercel
    frontend is built from its output. A file added to `stroj/web/` but not to
    that list is served by the judge and missing from the deployed site — where
    it fails as a 404 for a script the page needs, not as a build error."""

    def build_script(self) -> str:
        return (ROOT / "scripts" / "build-static.sh").read_text()

    def test_every_asset_is_copied(self):
        script = self.build_script()
        for asset in sorted(WEB.glob("*.js")) + sorted(WEB.glob("*.css")):
            assert asset.name in script, (
                f"{asset.name} exists in stroj/web/ but build-static.sh never"
                " copies it, so the deployed frontend would 404 on it."
            )

    def test_every_script_the_page_loads_exists(self):
        html = (WEB / "index.html").read_text()
        for src in re.findall(r'<script src="/static/([^"]+)"', html):
            assert (WEB / src).is_file(), f"index.html loads missing {src}"

    def test_latex_loads_before_the_app_that_calls_it(self):
        """app.js calls renderMath at module scope-adjacent time; a plain
        <script> runs in document order, so the order here is load-bearing."""
        html = (WEB / "index.html").read_text()
        scripts = re.findall(r'<script src="/static/([^"]+)"', html)
        assert "latex.js" in scripts and "app.js" in scripts
        assert scripts.index("latex.js") < scripts.index("app.js")


class TestStatementsCanUseMath:
    def test_the_renderer_is_reachable_from_the_statement_pipeline(self):
        app = (WEB / "app.js").read_text()
        assert "renderMath(" in app, "markdown() never calls the LaTeX renderer"

    def test_display_math_is_not_styled_with_plain_block(self):
        """`display: block` on a <math> element replaces its inner `math`
        display type with `flow`, and every fraction and sum collapses into a
        vertical stack of ordinary boxes. Only the two-value form is safe."""
        css = (WEB / "style.css").read_text()
        rule = re.search(r"math\.math-display\s*\{([^}]*)\}", css)
        assert rule, "no .math-display rule found"
        declared = re.search(r"display:\s*([^;]+);", rule.group(1))
        assert declared, "display is not set on .math-display"
        assert declared.group(1).strip() == "block math", declared.group(1)


class TestVersionFilesStayFresh:
    """The update banner and the updating screen both read `/version.json`.
    A cached copy of it, or of the page itself, would strand a tab on an old
    build with no way to notice — or make the Refresh button a no-op."""

    def vercel(self) -> dict:
        import json
        return json.loads((ROOT / "vercel.json").read_text())

    def header_for(self, source: str) -> str:
        for block in self.vercel()["headers"]:
            if block["source"] == source:
                for header in block["headers"]:
                    if header["key"] == "Cache-Control":
                        return header["value"]
        return ""

    def test_version_json_is_never_cached(self):
        assert "no-store" in self.header_for("/version.json")

    def test_the_page_itself_revalidates(self):
        # Otherwise "Refresh" re-serves the same stale HTML from cache and the
        # banner comes straight back.
        assert "no-cache" in self.header_for("/")

    def test_scripts_revalidate(self):
        assert "no-cache" in self.header_for("/static/(.*)")

    def test_version_json_is_not_proxied_to_the_judge(self):
        """It describes the *frontend*; routing it to the judge would compare
        the backend against itself and never detect a mismatch."""
        proxied = [r["source"] for r in self.vercel()["rewrites"]]
        assert not any(p.startswith("/version.json") for p in proxied)

    def test_the_build_emits_it(self):
        assert "version.json" in (ROOT / "scripts" / "build-static.sh").read_text()

    def test_there_is_no_banner_to_ignore(self):
        """An update forces a refresh rather than offering one, so a tab is
        never left running a page whose endpoints may have changed shape."""
        html = (WEB / "index.html").read_text()
        app = (WEB / "app.js").read_text()
        assert "update-banner" not in html and "update-banner" not in app
        assert "showUpdateBanner" not in app
        assert "refreshForUpdate(" in app

    def test_the_forced_refresh_cannot_loop(self):
        """If a cached page kept coming back stale, an unguarded reload would
        spin forever and the site would be unusable."""
        app = (WEB / "app.js").read_text()
        fn = app[app.index("function refreshForUpdate("):]
        fn = fn[:fn.index("\n}")]
        assert "sessionStorage.getItem(RELOAD_MARK) === target" in fn
        assert "return false" in fn


def test_the_dmoj_table_disclaims_a_conversion():
    """The two judges score unrelated things. Presenting the table without
    saying so invites people to 'convert' a rating that does not travel."""
    app = (WEB / "app.js").read_text()
    block = app[app.index("function dmojTable("):]
    block = block[:block.index("\n}")]
    # Source wrapping splits phrases across lines, so compare on the text as
    # a reader would see it rather than as it is typed.
    flat = " ".join(block.split()).lower()
    assert "unrelated" in flat
    assert "no conversion between them" in flat
    assert "do not treat it as a formula" in flat


class TestBootWaitsForALiveJudge:
    """Refreshing during an update must never land on a half-dead page. The
    site loading and *then* discovering the judge is gone is exactly the
    'no judge connected' screen appearing during a routine deploy."""

    def app(self) -> str:
        return (WEB / "app.js").read_text()

    def boot(self) -> str:
        src = self.app()
        return src[src.index("async function boot()"):]

    def test_backend_down_is_its_own_state(self):
        """Folded into 'unknown', a restarting judge reads as 'nothing to
        compare' and the site loads anyway."""
        src = self.app()
        state = src[src.index("function versionState("):]
        state = state[:state.index("\n}")]
        assert "'backend-down'" in state
        # The old rule; if it comes back, so does the bug.
        assert "if (!frontend || !backend) return 'unknown'" not in state

    def test_boot_retries_rather_than_rendering(self):
        boot = self.boot()
        assert "backend-down" in boot, "boot must recognise a silent judge"
        assert "BOOT_RETRY_MS" in boot and "sleep(" in boot

    def test_boot_gives_up_eventually(self):
        """A judge that never comes back is a deployment problem, and the
        operator needs the diagnostic — not an indefinite waiting screen."""
        boot = self.boot()
        assert "deadline" in boot
        assert "No judge backend connected" in boot

    def test_a_mismatch_never_times_out(self):
        """Unlike a silent judge, a mismatched pair is two live halves that
        disagree. That resolves on its own, so waiting is correct."""
        boot = self.boot()
        head = boot[:boot.index("backend-down")]
        assert "'updating'" in head and "return;" in head

    def test_the_data_load_is_inside_the_retry(self):
        """The judge can go away between answering /api/version and answering
        the rest, so the load itself has to be retried, not just the gate."""
        boot = self.boot()
        loop = boot[boot.index("for (;;)"):]
        assert "api('/api/auth/me')" in loop
        assert "lastError" in loop

    def test_showing_the_screen_can_be_undone(self):
        assert "function hideUpdating(" in self.app()

    def test_the_watcher_stops_covering_the_diagnostic(self):
        """Once boot gives up, the background poll must not paint the waiting
        screen back over the diagnostic every 20 seconds — that flip-flops
        between two screens forever and hides the one that is useful."""
        app = self.app()
        assert "bootGaveUp" in app
        check = app[app.index("async function checkVersions("):]
        check = check[:check.index("\nfunction ")]
        assert "if (!bootGaveUp) showUpdating(" in check
        # ...but a judge that does come back must still be picked up.
        assert "blockedByUpdate || bootGaveUp" in check


def test_samples_come_before_subtasks_on_a_problem_page():
    """Samples are what a solver checks their understanding against, so they
    should not sit below the scoring breakdown."""
    app = (WEB / "app.js").read_text()
    page = app[app.index("async function viewProblem("):]
    page = page[:page.index("Submit</h2>")]
    assert page.index("Samples</h2>") < page.index("Subtasks</h2>")
    assert page.index("Samples</h2>") < page.index("Partial scoring</h2>")


class TestMentionsRenderEverywhere:
    """A `@name` should behave the same in a bio, a post, a statement and in
    the live preview of each. That only holds if every render path shares one
    roster instead of each call site being wired up by hand."""

    def app(self) -> str:
        return (WEB / "app.js").read_text()

    def test_markdown_falls_back_to_the_roster(self):
        app = self.app()
        assert "function markdown(source, mentions = mentionRoster)" in app

    def test_no_render_site_passes_its_own_map(self):
        """A call site with a hand-rolled map is one that will fall behind."""
        app = self.app()
        calls = re.findall(r"markdown\((?!source)([^)]*)\)", app)
        # `markdown(x)` is fine; `markdown(x, somethingElse)` is the smell.
        with_map = [c for c in calls if "," in c and "mentions = mentionRoster" not in c]
        assert not with_map, f"these pass their own mention map: {with_map}"

    def test_the_roster_is_loaded_at_boot(self):
        boot = self.app()
        boot = boot[boot.index("async function boot()"):]
        assert "/api/mentions" in boot
        # A roster that fails to load must not stop the site coming up.
        assert "api('/api/mentions').catch(" in boot

    def test_saving_a_bio_re_renders_with_mentions(self):
        """The bug this fixes: the saved bio was rendered with no map, so a new
        mention only appeared after navigating away and back."""
        app = self.app()
        save = app[app.index("$('#bio-save').onclick"):]
        save = save[:save.index("};")]
        assert "markdown(saved.bio)" in save
        assert "u.mentions" not in save


class TestNothingIsLostAcrossAnUpdate:
    """An update now forces a refresh instead of offering one, so every editor
    has to survive being reloaded from under someone mid-sentence."""

    def app(self) -> str:
        return (WEB / "app.js").read_text()

    def test_every_editor_keeps_a_draft(self):
        """Two surfaces hold long-form text: the profile bio, and the one admin
        editor that every create and edit page is built from."""
        app = self.app()
        assert "keepDraft(`bio:" in app
        assert "keepDraft(adminDraftName(kind, slug)" in app

    def test_create_and_edit_drafts_cannot_collide(self):
        """A half-written new post and a half-written edit of an existing one
        are different work; one key for both would silently overwrite."""
        app = self.app()
        line = next(x for x in app.splitlines() if "const adminDraftName" in x)
        assert "`${kind}:${slug}`" in line and "`new-${kind}`" in line, line

    def test_a_draft_is_written_on_every_keystroke(self):
        """Saving on blur would lose the sentence being typed when the reload
        lands, which is exactly the moment this has to work."""
        fn = self.app()
        fn = fn[fn.index("function keepDraft("):]
        fn = fn[:fn.index("\n}")]
        assert "addEventListener('input'" in fn
        assert "addEventListener('change'" in fn

    def test_a_draft_is_dropped_once_it_saves(self):
        """Otherwise the next visit restores work that is already committed."""
        app = self.app()
        assert "bioDraft.clear()" in app
        assert "draft.clear()" in app

    def test_a_corrupt_draft_does_not_break_the_page(self):
        fn = self.app()
        fn = fn[fn.index("function keepDraft("):]
        fn = fn[:fn.index("\n}")]
        assert "catch" in fn

    def test_solution_drafts_still_persist(self):
        """These predate the change and are the most valuable of the lot."""
        app = self.app()
        assert "localStorage.setItem(draftKey(" in app


class TestUpdatesAreNotDeferredForContests:
    """A patch pushed mid-contest is almost always the fix for something
    breaking that contest, so waiting until the contest ends is backwards."""

    def script(self) -> str:
        return (ROOT / "scripts" / "auto-update.sh").read_text()

    def test_the_updater_no_longer_checks_for_a_live_contest(self):
        body = self.script()
        assert "deferring: a contest is running" not in body
        assert "contest.RUNNING" not in body

    def test_it_still_skips_when_already_current(self):
        """Removing the deferral must not turn every poll into a redeploy."""
        assert 'if [ "$deployed" = "$remote_rev" ]; then' in self.script()


def test_admin_problems_suite():
    """Run tests/test_admin_problems.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_admin_problems.js")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
def test_auth_dialog_suite():
    """Run tests/test_auth_dialog.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_auth_dialog.js")],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_type_migrate_suite():
    """Run tests/test_type_migrate.js and surface its output on failure."""
    result = subprocess.run(
        ["node", str(Path(__file__).parent / "test_type_migrate.js")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
