"""Email confirmation: signing up, the link, and the accounts that predate it.

The interesting half is the migration. Accounts created before this feature
have no address at all, and they must end up confirmed by the same door new
members walk through, without an admin touching the database.
"""

from __future__ import annotations

import pytest

from stroj import auth, config, db, mailer

#: Captured before any fixture swaps it out, so the "no mail server" tests can
#: put the real one back.
_REAL_SEND = mailer.send_verification


@pytest.fixture(autouse=True)
def outbox(monkeypatch):
    """Capture confirmation mail instead of sending it.

    Records the *token*, which is what the member would click; the plaintext
    exists nowhere else, since the database only keeps its digest.
    """
    sent: list[dict] = []

    def fake_send(to, username, token):
        sent.append({"to": to, "username": username, "token": token})
        return "sent"

    monkeypatch.setattr(mailer, "send_verification", fake_send)
    return sent


def signup(client, username="newbie", email=None, password="password123"):
    return client.post("/api/auth/register", json={
        "username": username, "password": password,
        "email": email or f"{username}@example.test"})


def make_problem(admin_client):
    body = {"slug": "a-plus-b", "title": "A + B", "statement": "Add them."}
    admin_client.post("/api/admin/problems", json=body).raise_for_status()
    admin_client.put("/api/admin/problems/a-plus-b/tests", json={
        "tests": [{"input": "1 2\n", "output": "3\n", "is_sample": 1, "points": 1}]
    }).raise_for_status()


class TestSigningUp:
    def test_registration_requires_an_address(self, client):
        response = client.post("/api/auth/register", json={
            "username": "noemail", "password": "password123"})
        assert response.status_code == 422

    @pytest.mark.parametrize("bad", ["nope", "a@b", "two@@at.com", "spaces @x.com"])
    def test_a_malformed_address_is_refused(self, client, bad):
        assert signup(client, "user1", email=bad).status_code == 400

    def test_an_account_starts_unconfirmed_and_gets_a_link(self, client, outbox):
        body = signup(client).json()
        assert body["user"]["email"] == "newbie@example.test"
        assert body["user"]["email_verified"] is False
        assert body["verification"] == "sent"
        assert [m["to"] for m in outbox] == ["newbie@example.test"]

    def test_the_domain_is_folded_but_the_mailbox_is_not(self, client):
        """`Ann@X.COM` and `ann@x.com` are the same host's business, not ours."""
        body = signup(client, "casey", email="Casey.Smith@Example.TEST").json()
        assert body["user"]["email"] == "Casey.Smith@example.test"

    def test_signing_up_still_signs_you_in(self, client):
        """The session exists so the next screen can be 'check your inbox'
        rather than 'now sign in again'. It just does not do anything yet."""
        signup(client)
        assert client.get("/api/auth/me").json()["user"]["username"] == "newbie"


class TestAnUnconfirmedAccountCannotAct:
    def test_it_cannot_submit(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        signup(client)
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(3)"})
        assert response.status_code == 403
        assert response.headers.get("X-Stroj-Reason") == "email-unverified"

    def test_the_refusal_says_which_step_is_missing(self, client, admin_client):
        """Two different states reach this gate — no address yet, and an
        address awaiting its link — and the fix differs."""
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        signup(client)
        waiting = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "x"})
        assert "link" in waiting.json()["detail"]

        db.execute("UPDATE users SET email = NULL WHERE username = 'newbie'")
        none_yet = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "x"})
        assert "Add an email" in none_yet.json()["detail"]

    def test_it_can_still_read_the_site(self, client, admin_client):
        """Refused at the action, not at the door: someone part-way through
        signing up can still see what they are signing up for."""
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        signup(client)
        assert client.get("/api/problems").status_code == 200
        assert client.get("/api/problems/a-plus-b").status_code == 200
        assert client.get("/api/auth/me").status_code == 200

    def test_an_unconfirmed_admin_is_still_refused(self, client):
        """Being an admin is not a way around it."""
        signup(client, "boss")
        db.execute("UPDATE users SET role = 'admin' WHERE username = 'boss'")
        response = client.post("/api/admin/problems", json={
            "slug": "x-y", "title": "X"})
        assert response.status_code == 403


class TestTheLink:
    def test_it_confirms_the_address(self, client, outbox):
        signup(client)
        token = outbox[-1]["token"]
        body = client.post("/api/auth/verify", json={"token": token}).json()
        assert body["user"]["email_verified"] is True
        assert client.get("/api/auth/me").json()["user"]["email_verified"] is True

    def test_the_account_works_afterwards(self, client, admin_client, outbox):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        signup(client)
        client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(3)"})
        assert response.status_code == 200, response.text

    def test_it_works_from_a_browser_that_never_signed_in(self, client, outbox):
        """Mail is usually opened somewhere else. A valid link both confirms
        the address and signs that account in."""
        signup(client)
        token = outbox[-1]["token"]
        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").json()["user"] is None
        client.post("/api/auth/verify", json={"token": token})
        assert client.get("/api/auth/me").json()["user"]["username"] == "newbie"

    def test_it_only_works_once(self, client, outbox):
        signup(client)
        token = outbox[-1]["token"]
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
        assert client.post("/api/auth/verify", json={"token": token}).status_code == 400

    def test_an_expired_link_is_refused(self, client, outbox):
        signup(client)
        db.execute("UPDATE email_tokens SET expires_at = '2020-01-01T00:00:00.000Z'")
        response = client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        assert response.status_code == 400
        assert "no longer valid" in response.json()["detail"]

    def test_a_made_up_token_is_refused(self, client):
        assert client.post(
            "/api/auth/verify", json={"token": "not-a-real-token"}).status_code == 400

    def test_only_the_digest_is_stored(self, client, outbox):
        """A stolen database must not hand over anybody's confirmation link."""
        signup(client)
        stored = db.one("SELECT token_hash FROM email_tokens")["token_hash"]
        assert stored != outbox[-1]["token"]
        assert len(stored) == 64

    def test_correcting_a_typo_kills_the_old_link(self, client, outbox):
        signup(client, "typo", email="wrogn@example.test")
        stale = outbox[-1]["token"]
        client.post("/api/auth/email", json={"email": "right@example.test"})
        assert client.post("/api/auth/verify", json={"token": stale}).status_code == 400
        fresh = outbox[-1]["token"]
        assert client.post("/api/auth/verify", json={"token": fresh}).status_code == 200
        assert db.one("SELECT email FROM users WHERE username = 'typo'")["email"] \
            == "right@example.test"


class TestAccountsThatPredateVerification:
    """The migration path: sign in as always, get asked for an address, confirm."""

    def old_account(self, client, username="veteran"):
        auth.create_user(username, "password123")
        db.execute("UPDATE users SET email = NULL, email_verified = 0"
                   " WHERE username = ?", (username,))
        client.post("/api/auth/login",
                    json={"username": username, "password": "password123"}).raise_for_status()

    def test_they_can_still_sign_in(self, client):
        self.old_account(client)
        me = client.get("/api/auth/me").json()["user"]
        assert me["username"] == "veteran"
        assert me["email"] is None and me["email_verified"] is False

    def test_they_are_stopped_at_the_first_action(self, client, admin_client):
        make_problem(admin_client)
        admin_client.post("/api/auth/logout")
        self.old_account(client)
        response = client.post("/api/submissions", json={
            "problem": "a-plus-b", "language": "python3", "source": "print(3)"})
        assert response.status_code == 403
        assert response.headers.get("X-Stroj-Reason") == "email-unverified"

    def test_they_can_name_an_address_and_confirm_it(self, client, outbox):
        self.old_account(client)
        result = client.post("/api/auth/email",
                             json={"email": "vet@example.test"}).json()
        assert result["email"] == "vet@example.test"
        assert outbox[-1]["to"] == "vet@example.test"

        client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        assert client.get("/api/auth/me").json()["user"]["email_verified"] is True

    def test_nothing_they_owned_is_disturbed(self, client):
        """Confirming an address is not a new account — rating, role and
        history stay exactly where they were."""
        auth.create_user("champ", "password123", role="admin")
        db.execute("UPDATE users SET email = NULL, email_verified = 0,"
                   " rating = 1337, rated_contests = 4 WHERE username = 'champ'")
        client.post("/api/auth/login",
                    json={"username": "champ", "password": "password123"})
        client.post("/api/auth/email", json={"email": "champ@example.test"})
        db.execute("UPDATE users SET email_verified = 1 WHERE username = 'champ'")
        row = db.one("SELECT * FROM users WHERE username = 'champ'")
        assert (row["rating"], row["rated_contests"], row["role"]) == (1337, 4, "admin")


class TestOneAddressOneAccount:
    def test_two_accounts_cannot_both_confirm_it(self, client, outbox):
        signup(client, "first", email="shared@example.test")
        client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        client.post("/api/auth/logout")

        signup(client, "second", email="shared@example.test")
        response = client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        assert response.status_code == 400
        assert "already in use" in response.json()["detail"]

    def test_an_unconfirmed_claim_does_not_reserve_it(self, client, outbox):
        """Otherwise typing someone else's address into the signup form would
        lock the real owner out of their own email."""
        signup(client, "squatter", email="real@example.test")
        client.post("/api/auth/logout")

        signup(client, "owner", email="real@example.test")
        assert client.post(
            "/api/auth/verify", json={"token": outbox[-1]["token"]}).status_code == 200

    def test_case_does_not_create_a_second_claim(self, client, outbox):
        signup(client, "first", email="Person@example.test")
        client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        client.post("/api/auth/logout")
        signup(client, "second", email="person@EXAMPLE.test")
        assert client.post(
            "/api/auth/verify", json={"token": outbox[-1]["token"]}).status_code == 400


class TestSendingAgain:
    def test_it_sends_a_fresh_link(self, client, outbox):
        signup(client)
        first = outbox[-1]["token"]
        assert client.post("/api/auth/verify/resend").status_code == 200
        assert outbox[-1]["token"] != first
        # The replaced link stops working, so a resend is never a second key.
        assert client.post("/api/auth/verify", json={"token": first}).status_code == 400

    def test_it_is_rate_limited(self, client):
        """The resend button is a way to mailbomb an address through us."""
        signup(client)
        codes = [client.post("/api/auth/verify/resend").status_code
                 for _ in range(config.VERIFY_SEND_LIMIT + 2)]
        assert codes[0] == 200 and 429 in codes

    def test_a_confirmed_account_is_not_sent_more(self, client, outbox):
        signup(client)
        client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        sent = len(outbox)
        assert client.post("/api/auth/verify/resend").json()["verification"] \
            == "already-verified"
        assert len(outbox) == sent

    def test_a_confirmed_account_cannot_silently_change_address(self, client, outbox):
        signup(client)
        client.post("/api/auth/verify", json={"token": outbox[-1]["token"]})
        response = client.post("/api/auth/email", json={"email": "new@example.test"})
        assert response.status_code == 400


class TestWithoutAMailServer:
    """A club on a laptop has no SMTP. The judge still has to work."""

    def test_the_link_is_logged_instead(self, client, monkeypatch, caplog):
        monkeypatch.setattr(config, "SMTP_HOST", "")
        monkeypatch.setattr(mailer, "send_verification", _REAL_SEND)
        import logging
        with caplog.at_level(logging.WARNING, logger="stroj.mail"):
            body = signup(client).json()
        assert body["verification"] == "logged"
        assert "#/verify?token=" in caplog.text

    def test_the_account_is_still_unconfirmed(self, client, monkeypatch):
        """The fallback is a delivery route, not a way around the check."""
        monkeypatch.setattr(config, "SMTP_HOST", "")
        monkeypatch.setattr(mailer, "send_verification", _REAL_SEND)
        signup(client)
        assert client.get("/api/auth/me").json()["user"]["email_verified"] is False


class TestTheWayBackIn:
    def test_the_bootstrap_admin_is_already_confirmed(self, client):
        """It exists before any mail server does; locking it out would leave
        the judge with no administrator and no way to make one."""
        row = db.one("SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1")
        assert row["email_verified"] == 1

    def test_the_cli_can_confirm_an_account_by_hand(self, client):
        from stroj.__main__ import cmd_verify
        import argparse

        auth.create_user("stuck", "password123")
        args = argparse.Namespace(username="stuck", email="stuck@example.test")
        assert cmd_verify(args) == 0
        row = db.one("SELECT * FROM users WHERE username = 'stuck'")
        assert row["email_verified"] == 1 and row["email"] == "stuck@example.test"

    def test_the_cli_refuses_to_steal_a_confirmed_address(self, client):
        from stroj.__main__ import cmd_verify
        import argparse

        auth.create_user("owner", "password123", email="taken@example.test",
                         email_verified=True)
        auth.create_user("thief", "password123")
        args = argparse.Namespace(username="thief", email="taken@example.test")
        assert cmd_verify(args) == 1
        assert db.one("SELECT email_verified FROM users WHERE username = 'thief'"
                      )["email_verified"] == 0


class TestAnUnconfirmedAccountIsNobody:
    """The gate is not "cannot act", it is "is not signed in".

    Privilege is read off `current_user` in fourteen places, most of them
    reads. A check that only guarded the endpoints which write left every one
    of those reads answering as though the account were fully signed in — an
    unconfirmed admin could list hidden problems, which on this judge means
    next week's contest.
    """

    def admin_but_unconfirmed(self, client):
        auth.create_user("ghost", "password123", role="admin")
        db.execute("UPDATE users SET email = NULL, email_verified = 0"
                   " WHERE username = 'ghost'")
        client.post("/api/auth/login",
                    json={"username": "ghost", "password": "password123"}).raise_for_status()

    def test_it_cannot_see_hidden_problems(self, client, admin_client):
        admin_client.post("/api/admin/problems", json={
            "slug": "next-week", "title": "Next Week", "visible": False}).raise_for_status()
        admin_client.post("/api/auth/logout")

        self.admin_but_unconfirmed(client)
        listed = [p["slug"] for p in client.get("/api/problems").json()["problems"]]
        assert "next-week" not in listed
        assert client.get("/api/problems/next-week").status_code == 404

    def test_it_cannot_read_unpublished_posts(self, client, admin_client):
        admin_client.post("/api/admin/posts", json={
            "slug": "draft", "title": "Draft", "published": False}).raise_for_status()
        admin_client.post("/api/auth/logout")

        self.admin_but_unconfirmed(client)
        assert [p["slug"] for p in client.get("/api/posts").json()["posts"]] == []
        assert client.get("/api/posts/draft").status_code == 404

    def test_the_site_answers_it_exactly_as_it_answers_a_stranger(self, client, admin_client):
        admin_client.post("/api/admin/problems", json={
            "slug": "shown", "title": "Shown"}).raise_for_status()
        admin_client.post("/api/admin/problems", json={
            "slug": "hidden-one", "title": "Hidden", "visible": False}).raise_for_status()
        admin_client.post("/api/auth/logout")

        stranger = client.get("/api/problems").json()
        self.admin_but_unconfirmed(client)
        assert client.get("/api/problems").json() == stranger

    def test_it_is_not_offered_the_admin_page(self, client):
        """`is_admin` means "may act as one now", so the page does not offer a
        door the server will shut."""
        self.admin_but_unconfirmed(client)
        me = client.get("/api/auth/me").json()["user"]
        assert me["role"] == "admin" and me["is_admin"] is False

    def test_but_it_still_knows_whose_account_is_waiting(self, client):
        """Otherwise the confirmation page could not name the address it is
        waiting on, or offer to send the link again."""
        self.admin_but_unconfirmed(client)
        me = client.get("/api/auth/me").json()["user"]
        assert me["username"] == "ghost" and me["email_verified"] is False


class TestTheOutbox:
    """The judge cannot reach a mail server from inside its own container, so
    it writes messages to a directory and something on the host sends them."""

    def spooling(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mailer, "send_verification", _REAL_SEND)
        monkeypatch.setattr(config, "MAIL_TRANSPORT", "spool")
        monkeypatch.setattr(config, "MAIL_SPOOL", tmp_path / "outbox")
        monkeypatch.setattr(config, "BASE_URL", "https://judge.example.org")
        return tmp_path / "outbox"

    def test_a_signup_leaves_a_message_in_the_outbox(self, client, monkeypatch, tmp_path):
        outbox = self.spooling(monkeypatch, tmp_path)
        assert signup(client).json()["verification"] == "spooled"
        assert len(list(outbox.glob("*.eml"))) == 1

    def test_the_message_is_a_real_email_with_a_working_link(
            self, client, monkeypatch, tmp_path):
        """Long URLs get quoted-printable encoded, so the link has to survive
        being decoded — a check on the raw bytes would pass a broken one."""
        import email
        import email.policy

        outbox = self.spooling(monkeypatch, tmp_path)
        signup(client, "posty", email="posty@example.org")
        raw = next(outbox.glob("*.eml")).read_bytes()
        message = email.message_from_bytes(raw, policy=email.policy.default)

        assert message["To"] == "posty@example.org"
        assert message["Subject"].startswith("Confirm your email")
        assert message["Message-ID"] and message["Date"]
        body = message.get_content()
        assert "https://judge.example.org/#/verify?token=" in body

    def test_the_link_in_it_actually_confirms_the_account(
            self, client, monkeypatch, tmp_path):
        import email
        import email.policy
        import re

        outbox = self.spooling(monkeypatch, tmp_path)
        signup(client, "posted", email="posted@example.org")
        body = email.message_from_bytes(
            next(outbox.glob("*.eml")).read_bytes(), policy=email.policy.default
        ).get_content()
        token = re.search(r"verify\?token=(\S+)", body).group(1)

        assert client.post("/api/auth/verify", json={"token": token}).status_code == 200
        assert client.get("/api/auth/me").json()["user"]["email_verified"] is True

    def test_a_queued_message_is_not_world_readable(
            self, client, monkeypatch, tmp_path):
        """It holds a confirmation link, which *is* the credential it protects."""
        outbox = self.spooling(monkeypatch, tmp_path)
        signup(client)
        spooled = next(outbox.glob("*.eml"))
        assert spooled.stat().st_mode & 0o777 == 0o600
        assert outbox.stat().st_mode & 0o777 == 0o700

    def test_nothing_half_written_is_left_where_a_sender_would_find_it(
            self, client, monkeypatch, tmp_path):
        """The host drains this on a timer; a partial file would go out
        truncated exactly once and then be deleted."""
        outbox = self.spooling(monkeypatch, tmp_path)
        signup(client)
        assert not list(outbox.glob("*.tmp"))
        assert not [p for p in outbox.iterdir() if p.name.startswith(".")]

    def test_the_transport_is_reported_for_doctor(self, monkeypatch, tmp_path):
        self.spooling(monkeypatch, tmp_path)
        assert mailer.transport() == "spool" and mailer.configured()
        assert "spooled to" in mailer.describe()

        monkeypatch.setattr(config, "MAIL_TRANSPORT", "auto")
        monkeypatch.setattr(config, "SMTP_HOST", "")
        assert mailer.transport() == "log" and not mailer.configured()
        assert "written to the log" in mailer.describe()
