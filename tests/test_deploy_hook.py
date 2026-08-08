"""The webhook receiver is the one thing standing between the public internet
and a deploy, so its signature check gets tested directly."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import pathlib

import pytest

_spec = importlib.util.spec_from_file_location(
    "deploy_hook", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "deploy-hook.py"
)
hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hook)

SECRET = b"correct-horse-battery-staple"


def sign(body: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


class TestSignature:
    def test_accepts_a_correct_signature(self):
        body = b'{"ref":"refs/heads/main"}'
        assert hook.valid_signature(body, sign(body), SECRET)

    def test_rejects_a_missing_header(self):
        assert not hook.valid_signature(b"{}", None, SECRET)

    def test_rejects_an_empty_header(self):
        assert not hook.valid_signature(b"{}", "", SECRET)

    def test_rejects_the_wrong_secret(self):
        body = b'{"ref":"refs/heads/main"}'
        assert not hook.valid_signature(body, sign(body, b"guessed"), SECRET)

    def test_rejects_a_tampered_body(self):
        signature = sign(b'{"ref":"refs/heads/main"}')
        assert not hook.valid_signature(b'{"ref":"refs/heads/evil"}', signature, SECRET)

    def test_rejects_an_unprefixed_digest(self):
        """A bare hex digest must not be accepted just because it matches."""
        body = b"{}"
        bare = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        assert not hook.valid_signature(body, bare, SECRET)

    def test_rejects_sha1_style_headers(self):
        assert not hook.valid_signature(b"{}", "sha1=abcdef", SECRET)

    def test_an_unset_secret_rejects_everything(self):
        """Misconfiguration must fail closed, never open."""
        body = b"{}"
        assert not hook.valid_signature(body, sign(body, b""), b"")
        assert not hook.valid_signature(body, "sha256=anything", b"")


class TestDeployDecision:
    BRANCH = "refs/heads/main"

    def test_push_to_main_deploys(self):
        go, _ = hook.should_deploy("push", {"ref": self.BRANCH}, self.BRANCH)
        assert go

    def test_push_to_another_branch_is_ignored(self):
        go, why = hook.should_deploy("push", {"ref": "refs/heads/wip"}, self.BRANCH)
        assert not go and "wip" in why

    def test_branch_deletion_is_ignored(self):
        go, _ = hook.should_deploy(
            "push", {"ref": self.BRANCH, "deleted": True}, self.BRANCH
        )
        assert not go

    def test_ping_is_answered_not_deployed(self):
        go, why = hook.should_deploy("ping", {}, self.BRANCH)
        assert not go and why == "pong"

    @pytest.mark.parametrize("event", ["issues", "star", "pull_request", None])
    def test_other_events_are_ignored(self, event):
        go, _ = hook.should_deploy(event, {"ref": self.BRANCH}, self.BRANCH)
        assert not go

    def test_a_tag_push_is_ignored(self):
        go, _ = hook.should_deploy("push", {"ref": "refs/tags/v1"}, self.BRANCH)
        assert not go
