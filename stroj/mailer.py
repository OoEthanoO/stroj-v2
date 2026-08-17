"""Sending the one kind of mail this judge sends: "confirm your address".

Three ways out, because the recommended deployment makes the obvious one
impossible. A judge set up by `bootstrap-judge.sh` runs with no DNS and a
blanket egress drop on its bridge — that is what denies submissions the
network — so it cannot open a connection to a mail server either.

``smtp``   talk to a mail server directly. Fine on a laptop or behind a relay
           reachable on the local network; needs DNS and egress otherwise.
``spool``  write the message to a directory and let something on the host send
           it. The container needs no network at all and never holds the mail
           credentials, which is why this is the one DEPLOY.md recommends.
``log``    write the link to the log. Not a stub: an organiser can pass it on,
           and the account stays unconfirmed until the link is used.

Delivery failures are never raised. An account that exists but whose mail
bounced is recoverable — the member presses "send it again" — while a signup
that fails half-way has already created the account and then told the member
it did not.
"""

from __future__ import annotations

import logging
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from urllib.parse import quote

from . import config

log = logging.getLogger("stroj.mail")


class MailError(Exception):
    """The message could not be handed over for delivery."""


def transport() -> str:
    """Which of the three routes is in force, resolving ``auto``."""
    chosen = config.MAIL_TRANSPORT
    if chosen in ("smtp", "spool", "log"):
        return chosen
    return "smtp" if config.SMTP_HOST else "log"


def configured() -> bool:
    """Whether mail actually goes anywhere a member can read."""
    return transport() in ("smtp", "spool")


def describe() -> str:
    """One line for `stroj doctor`, so the state is visible from outside."""
    route = transport()
    if route == "smtp":
        return f"smtp to {config.SMTP_HOST}:{config.SMTP_PORT}"
    if route == "spool":
        return f"spooled to {config.MAIL_SPOOL} for the host to send"
    return "not configured — confirmation links are written to the log"


def verification_link(token: str) -> str:
    return f"{config.BASE_URL.rstrip('/')}/#/verify?token={quote(token)}"


def _message(to: str, username: str, token: str) -> EmailMessage:
    link = verification_link(token)
    message = EmailMessage()
    message["From"] = config.MAIL_FROM
    message["To"] = to
    message["Subject"] = f"Confirm your email for {config.SITE_NAME}"
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=config.MAIL_FROM.split("@")[-1])
    message.set_content(
        f"Hi {username},\n\n"
        f"Confirm this address to finish setting up your {config.SITE_NAME} "
        "account:\n\n"
        f"    {link}\n\n"
        f"The link is good for {config.EMAIL_TOKEN_HOURS} hours. If you did not "
        "ask for this, you can ignore it — nothing happens until the link is "
        "used, and the address is not attached to anyone until then.\n"
    )
    return message


def send_verification(to: str, username: str, token: str) -> str:
    """Hand the confirmation link over for delivery.

    Returns how it went: ``sent``, ``spooled``, ``logged`` or ``failed``. The
    caller passes that back to the page, which says something different for
    each — "check your inbox" is a lie if nothing left the building.
    """
    route = transport()
    if route == "log":
        log.warning(
            "no mail transport configured — verification link for %s (%s):\n    %s",
            username, to, verification_link(token),
        )
        return "logged"

    message = _message(to, username, token)
    try:
        if route == "spool":
            path = _spool(message)
            log.info("queued confirmation mail for %s at %s", to, path.name)
            return "spooled"
        _deliver(message)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("could not send verification mail to %s: %s", to, exc)
        return "failed"
    return "sent"


def _spool(message: EmailMessage) -> Path:
    """Write one message into the outbox, atomically.

    Written to a temporary name and renamed into place: a sender running on the
    host reads this directory on a timer, and a half-written file would go out
    truncated exactly once and then be deleted.
    """
    directory = config.MAIL_SPOOL
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)

    # Sortable so the host sends in the order queued, unique so two signups in
    # the same millisecond cannot collide, and revealing nothing: the recipient
    # lives inside the file, which is 0600, not in a name a listing would show.
    name = f"{int(time.time() * 1000):013d}-{secrets.token_hex(6)}.eml"
    temporary = directory / f".{name}.tmp"
    final = directory / name

    temporary.write_bytes(message.as_bytes())
    os.chmod(temporary, 0o600)
    os.replace(temporary, final)
    return final


def _deliver(message: EmailMessage) -> None:
    timeout = config.SMTP_TIMEOUT_S
    if config.SMTP_SSL:
        server = smtplib.SMTP_SSL(
            config.SMTP_HOST, config.SMTP_PORT, timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=timeout)
    with server:
        if config.SMTP_STARTTLS and not config.SMTP_SSL:
            server.starttls(context=ssl.create_default_context())
        if config.SMTP_USER:
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(message)
