"""deploy/judge/site.env: the deployment's public site, read by bootstrap.

The email links are built from STROJ_BASE_URL. auto-update.sh carries the
running container's value across each deploy, so the repository's value has to
win over it for a push to change it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from stroj import config, mailer

ROOT = Path(__file__).resolve().parent.parent
SITE_ENV = ROOT / "deploy" / "judge" / "site.env"
BOOTSTRAP = ROOT / "scripts" / "bootstrap-judge.sh"
HOME_CADDYFILE = ROOT / "deploy" / "home" / "Caddyfile"


def _sourced_base_url(carried: str) -> str:
    # The same steps bootstrap takes, under its strict mode, starting from a
    # carried value the way auto-update.sh leaves it.
    script = 'set -euo pipefail; set -a; . "$1"; set +a; printf %s "$STROJ_BASE_URL"'
    result = subprocess.run(
        ["bash", "-c", script, "bash", str(SITE_ENV)],
        env={"PATH": "/usr/bin:/bin", "STROJ_BASE_URL": carried},
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def test_site_env_wins_over_the_carried_value():
    assert _sourced_base_url("https://judge.ethanyanxu.com") == "https://stroj.ethanyanxu.com"


def test_base_url_is_a_site_the_home_proxy_serves():
    host = urlsplit(_sourced_base_url("")).hostname
    site_lines = re.findall(r"^([a-z0-9.-]+(?:, *[a-z0-9.-]+)*) *\{",
                            HOME_CADDYFILE.read_text(), re.M)
    served = {name.strip() for line in site_lines for name in line.split(",")}
    assert host in served


def test_bootstrap_reads_site_env_before_starting_the_judge():
    text = BOOTSTRAP.read_text()
    sourced = text.index(". deploy/judge/site.env")
    started = text.index("docker run -d")
    assert sourced < started


def test_reset_link_opens_on_the_public_site(monkeypatch):
    monkeypatch.setattr(config, "BASE_URL", _sourced_base_url("https://judge.ethanyanxu.com"))
    assert mailer.reset_link("abc").startswith("https://stroj.ethanyanxu.com/#/reset?token=")
    assert mailer.verification_link("abc").startswith("https://stroj.ethanyanxu.com/#/verify?token=")
