"""Forgotten passwords: asking for a link, and choosing a new password with it.

The link only ever goes to a confirmed address, and the request is answered
the same way whatever it names, so the page cannot be used to find out which
accounts exist.
"""

from __future__ import annotations

import pytest

from stroj import config, db, mailer

_REAL_SEND = mailer.send_password_reset


@pytest.fixture(autouse=True)
def mail(monkeypatch):
    """Capture confirmation and reset mail instead of sending either."""
    sent: list[dict] = []

    def capture(kind):
        def fake(to, username, token):
            sent.append({"kind": kind, "to": to, "username": username, "token": token})
            return "sent"
        return fake

    monkeypatch.setattr(mailer, "send_verification", capture("verify"))
    monkeypatch.setattr(mailer, "send_password_reset", capture("reset"))
    return sent


def resets(mail):
    return [m for m in mail if m["kind"] == "reset"]


def confirmed_member(client, mail, username="member", email=None, password="old-password"):
    """Sign up, confirm the address, and sign out, as a member who later forgets."""
    client.post("/api/auth/register", json={
        "username": username, "password": password,
        "email": email or f"{username}@example.test"}).raise_for_status()
    token = [m for m in mail if m["kind"] == "verify"][-1]["token"]
    client.post("/api/auth/verify", json={"token": token}).raise_for_status()
    client.post("/api/auth/logout")


def forgot(client, login):
    return client.post("/api/auth/password/forgot", json={"login": login})


def reset(client, token, password="new-password"):
    return client.post("/api/auth/password/reset", json={"token": token, "password": password})


def signs_in(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).status_code == 200


class TestAskingForALink:
    def test_by_username(self, client, mail):
        confirmed_member(client, mail)
        response = forgot(client, "member")
        assert response.status_code == 200
        assert [(m["to"], m["username"]) for m in resets(mail)] == [
            ("member@example.test", "member")]

    def test_by_email_address_in_any_case(self, client, mail):
        confirmed_member(client, mail, email="Member@Example.test")
        forgot(client, "member@EXAMPLE.TEST")
        assert [m["username"] for m in resets(mail)] == ["member"]

    def test_by_username_in_another_case(self, client, mail):
        confirmed_member(client, mail, username="Leo")
        forgot(client, "leo")
        assert [m["username"] for m in resets(mail)] == ["Leo"]

    def test_the_answer_never_says_whether_the_account_exists(self, client, mail):
        """Unknown names, unconfirmed addresses and accounts with no address at
        all get exactly the answer a real account gets, and no mail."""
        confirmed_member(client, mail)
        client.post("/api/auth/register", json={
            "username": "pending", "password": "password123",
            "email": "pending@example.test"}).raise_for_status()
        client.post("/api/auth/logout")
        db.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES"
            " ('legacy', 'x', '2026-01-01T00:00:00.000Z')"
        )
        real = forgot(client, "member")
        assert len(resets(mail)) == 1
        for login in ("nobody", "nobody@example.test", "pending",
                      "pending@example.test", "legacy", "not an address@"):
            response = forgot(client, login)
            assert (response.status_code, response.json()) == (real.status_code, real.json()), login
        assert len(resets(mail)) == 1

    def test_it_says_whether_this_judge_can_send_mail(self, client, mail, monkeypatch):
        monkeypatch.setattr(config, "MAIL_TRANSPORT", "log")
        assert forgot(client, "anyone").json() == {"mail": False}
        monkeypatch.setattr(config, "MAIL_TRANSPORT", "spool")
        assert forgot(client, "anyone").json() == {"mail": True}

    def test_an_account_is_sent_only_a_few_and_the_limit_is_silent(self, client, mail):
        """A 429 would tell the asker the account exists, so past the limit
        the answer stays the same and the mail simply stops."""
        confirmed_member(client, mail)
        answers = {forgot(client, "member").status_code for _ in range(config.RESET_SEND_LIMIT + 2)}
        assert answers == {200}
        assert len(resets(mail)) == config.RESET_SEND_LIMIT

    def test_a_new_link_replaces_the_old_one(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        forgot(client, "member")
        first, second = (m["token"] for m in resets(mail))
        assert reset(client, first).status_code == 400
        assert reset(client, second).status_code == 200

    def test_only_the_digest_is_stored(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        stored = db.one("SELECT token_hash FROM password_resets")["token_hash"]
        assert stored != resets(mail)[-1]["token"]
        assert len(stored) == 64


class TestTheLink:
    def test_it_sets_the_new_password(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        assert reset(client, resets(mail)[-1]["token"]).status_code == 200
        client.post("/api/auth/logout")
        assert not signs_in(client, "member", "old-password")
        assert signs_in(client, "member", "new-password")

    def test_it_signs_this_browser_in(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        body = reset(client, resets(mail)[-1]["token"]).json()
        assert body["user"]["username"] == "member"
        assert client.get("/api/auth/me").json()["user"]["username"] == "member"

    def test_it_ends_every_other_session(self, client, mail):
        """Whoever forgot the password may not be the only one who knew it."""
        from fastapi.testclient import TestClient

        from stroj import main

        confirmed_member(client, mail)
        with TestClient(main.app) as elsewhere:
            assert signs_in(elsewhere, "member", "old-password")
            assert elsewhere.get("/api/auth/me").json()["user"] is not None
            forgot(client, "member")
            reset(client, resets(mail)[-1]["token"])
            assert elsewhere.get("/api/auth/me").json()["user"] is None

    def test_it_only_works_once(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        token = resets(mail)[-1]["token"]
        assert reset(client, token).status_code == 200
        response = reset(client, token, "another-password")
        assert response.status_code == 400
        assert "no longer valid" in response.json()["detail"]

    def test_an_expired_link_is_refused(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        db.execute("UPDATE password_resets SET expires_at = '2020-01-01T00:00:00.000Z'")
        assert reset(client, resets(mail)[-1]["token"]).status_code == 400
        assert signs_in(client, "member", "old-password")

    def test_a_made_up_token_is_refused(self, client):
        assert reset(client, "not-a-real-token").status_code == 400

    def test_a_short_password_is_refused_without_spending_the_link(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        token = resets(mail)[-1]["token"]
        response = reset(client, token, "short")
        assert response.status_code == 400
        assert "at least 8" in response.json()["detail"]
        assert reset(client, token).status_code == 200

    def test_it_stops_working_if_the_confirmed_address_changes(self, client, mail):
        confirmed_member(client, mail)
        forgot(client, "member")
        db.execute("UPDATE users SET email = 'someone-else@example.test' WHERE username = 'member'")
        assert reset(client, resets(mail)[-1]["token"]).status_code == 400

    def test_it_does_not_disturb_a_confirmation_link(self, client, mail):
        """The two kinds of link live apart: asking for one never kills the other."""
        confirmed_member(client, mail)
        forgot(client, "member")
        assert db.one("SELECT COUNT(*) AS n FROM password_resets")["n"] == 1
        client.post("/api/auth/register", json={
            "username": "second", "password": "password123",
            "email": "second@example.test"}).raise_for_status()
        assert db.one("SELECT COUNT(*) AS n FROM password_resets")["n"] == 1


class TestTheEmail:
    def test_the_spooled_message_holds_a_working_link(self, client, mail, monkeypatch, tmp_path):
        import email
        import email.policy
        import re

        confirmed_member(client, mail)
        monkeypatch.setattr(mailer, "send_password_reset", _REAL_SEND)
        monkeypatch.setattr(config, "MAIL_TRANSPORT", "spool")
        monkeypatch.setattr(config, "MAIL_SPOOL", tmp_path / "outbox")
        monkeypatch.setattr(config, "BASE_URL", "https://judge.example.org")
        forgot(client, "member")

        spooled = list((tmp_path / "outbox").glob("*.eml"))
        assert len(spooled) == 1
        assert spooled[0].stat().st_mode & 0o777 == 0o600
        message = email.message_from_bytes(spooled[0].read_bytes(), policy=email.policy.default)
        assert message["To"] == "member@example.test"
        assert message["Subject"].startswith("Reset your")
        body = message.get_content()
        token = re.search(r"https://judge\.example\.org/#/reset\?token=(\S+)", body).group(1)
        assert reset(client, token).status_code == 200
