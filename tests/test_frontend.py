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

    def test_the_update_banner_cannot_be_dismissed(self):
        """The warning describes a live hazard — this tab may call an endpoint
        that has changed shape — so it must not be closable while it applies."""
        html = (WEB / "index.html").read_text()
        app = (WEB / "app.js").read_text()
        banner = html[html.index('id="update-banner"'):]
        banner = banner[:banner.index("</div>")]
        assert "dismiss" not in banner.lower()
        assert "Refresh" in banner, "the one action offered should still be there"
        assert "bannerDismissed" not in app
