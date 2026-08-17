"""Sending the one kind of mail this judge sends: "confirm your address".

SMTP is optional. A club running this on a laptop has no mail server, and a
judge that refused to create accounts without one would be useless — so when
SMTP is unconfigured the message is written to the log instead, verification
link and all, and an organiser can paste it to the member. That is a real
fallback, not a stub: the account is still unverified until the link is used.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import quote

from . import config

log = logging.getLogger("stroj.mail")


class MailError(Exception):
    """The message could not be handed to a mail server."""


def configured() -> bool:
    return bool(config.SMTP_HOST)


def verification_link(token: str) -> str:
    return f"{config.BASE_URL.rstrip('/')}/#/verify?token={quote(token)}"


def _body(username: str, link: str) -> tuple[str, str]:
    subject = f"Confirm your email for {config.SITE_NAME}"
    text = (
        f"Hi {username},\n\n"
        f"Confirm this address to finish setting up your {config.SITE_NAME} "
        "account:\n\n"
        f"    {link}\n\n"
        f"The link is good for {config.EMAIL_TOKEN_HOURS} hours. If you did not "
        "ask for this, you can ignore it — nothing happens until the link is "
        "used, and the address is not attached to anyone until then.\n"
    )
    return subject, text


def send_verification(to: str, username: str, token: str) -> str:
    """Send (or log) the confirmation link. Returns how it was delivered.

    Never raises for a delivery failure: an account that exists but whose mail
    bounced is recoverable — the member presses "send it again" — while a
    signup that 500s in the middle has already created the account and told the
    member it failed.
    """
    subject, text = _body(username, verification_link(token))

    if not configured():
        log.warning(
            "no SMTP configured — verification link for %s (%s):\n    %s",
            username, to, verification_link(token),
        )
        return "logged"

    message = EmailMessage()
    message["From"] = config.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)

    try:
        _deliver(message)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("could not send verification mail to %s: %s", to, exc)
        return "failed"
    return "sent"


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
