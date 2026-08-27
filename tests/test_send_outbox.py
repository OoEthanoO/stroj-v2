"""The host-side drainer: what turns a spooled message into a sent one.

`spool` is the transport DEPLOY.md recommends, so this script is the last step
of every confirmation mail a real deployment sends — and the only step outside
the judge's own suite. It is exercised against a fake sendmail: what matters is
that a message leaves once, that a refused one is kept for the next pass, and
that a watcher notices an arrival without waiting for a clock.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DRAINER = ROOT / "scripts" / "send-outbox.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or os.name != "posix", reason="needs a POSIX shell"
)


@pytest.fixture
def outbox(tmp_path):
    box = tmp_path / "outbox"
    box.mkdir()
    return box


def sendmail(tmp_path, name: str, *, script: str) -> str:
    """A stand-in for msmtp, so nothing here talks to a mail server."""
    path = tmp_path / name
    path.write_text("#!/usr/bin/env bash\n" + script)
    path.chmod(0o755)
    return str(path)


def accepting(tmp_path) -> str:
    """Appends each message it is handed to `delivered`, one line per message."""
    return sendmail(
        tmp_path, "ok-sendmail",
        script=f'cat > /dev/null\necho sent >> "{tmp_path / "delivered"}"\n',
    )


def refusing(tmp_path) -> str:
    return sendmail(tmp_path, "bad-sendmail", script="cat > /dev/null\nexit 1\n")


def spool(box: Path, name: str) -> Path:
    """Write a message the way `mailer._spool` does — final name, not a temp."""
    path = box / f"{int(time.time() * 1000):013d}-{name}.eml"
    path.write_text(f"Subject: {name}\n\nbody\n")
    return path


def delivered(tmp_path) -> int:
    receipt = tmp_path / "delivered"
    return len(receipt.read_text().split()) if receipt.exists() else 0


def drain(box: Path, sender: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(DRAINER), *args],
        env={**os.environ, "OUTBOX": str(box), "SENDMAIL": sender, "POLL_SECS": "1"},
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )


def test_a_pass_sends_everything_and_clears_it(outbox, tmp_path):
    spool(outbox, "one")
    spool(outbox, "two")
    result = drain(outbox, accepting(tmp_path))
    assert result.returncode == 0, result.stderr
    assert delivered(tmp_path) == 2
    assert list(outbox.glob("*.eml")) == []
    assert "sent 2 message(s)" in result.stdout


def test_a_refused_message_is_kept_for_the_next_pass(outbox, tmp_path):
    spool(outbox, "held")
    result = drain(outbox, refusing(tmp_path))
    assert result.returncode == 1
    assert len(list(outbox.glob("*.eml"))) == 1

    # The relay comes back; the same message goes out without being re-spooled.
    assert drain(outbox, accepting(tmp_path)).returncode == 0
    assert delivered(tmp_path) == 1


def test_an_empty_outbox_says_nothing(outbox, tmp_path):
    result = drain(outbox, accepting(tmp_path))
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_a_watcher_sends_what_lands_after_it_started(outbox, tmp_path):
    """The point of `--watch`: no timer, so no minute of waiting."""
    watcher = subprocess.Popen(
        ["bash", str(DRAINER), "--watch"],
        env={**os.environ, "OUTBOX": str(outbox),
             "SENDMAIL": accepting(tmp_path), "POLL_SECS": "1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    )
    try:
        time.sleep(1)
        spool(outbox, "fresh")
        deadline = time.time() + 20
        while time.time() < deadline and not delivered(tmp_path):
            time.sleep(0.2)
        assert delivered(tmp_path) == 1
        assert list(outbox.glob("*.eml")) == []
    finally:
        watcher.terminate()
        watcher.wait(timeout=10)


def test_a_second_drainer_cannot_send_the_same_message_twice(outbox, tmp_path):
    """A watcher holds the lock, so the old timer can stay installed beside it."""
    sender = accepting(tmp_path)
    watcher = subprocess.Popen(
        ["bash", str(DRAINER), "--watch"],
        env={**os.environ, "OUTBOX": str(outbox), "SENDMAIL": sender, "POLL_SECS": "1"},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT,
    )
    try:
        time.sleep(1)
        spool(outbox, "contended")
        drain(outbox, sender)
        deadline = time.time() + 20
        while time.time() < deadline and not delivered(tmp_path):
            time.sleep(0.2)
        time.sleep(2)
        assert delivered(tmp_path) == 1
    finally:
        watcher.terminate()
        watcher.wait(timeout=10)
