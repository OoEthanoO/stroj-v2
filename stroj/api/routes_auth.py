"""Registration, login, logout, whoami."""

from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, config, mailer
from ..ratelimit import RateLimiter, client_key
from .deps import current_user, require_account, user_public

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Keyed by account, not by address: the forwarded header is client-supplied and
# an attacker can rotate it freely, but they cannot rotate whose password they
# are guessing.
_login_by_account = RateLimiter(config.LOGIN_ATTEMPTS, config.LOGIN_WINDOW_S)
_login_by_client = RateLimiter(config.LOGIN_ATTEMPTS * 3, config.LOGIN_WINDOW_S)
_register_by_client = RateLimiter(config.REGISTER_LIMIT, config.REGISTER_WINDOW_S)
# Confirmation mail is the one thing here that costs somebody else money and
# reputation — an unlimited resend button is a way to mailbomb an address
# through us. Keyed by account, since that is what the sender has to own.
_verify_by_account = RateLimiter(config.VERIFY_SEND_LIMIT, config.VERIFY_SEND_WINDOW_S)


def _enforce(limiter: RateLimiter, key: str, what: str) -> None:
    wait = limiter.check(key)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Too many {what}. Try again in {int(wait) + 1}s.",
            headers={"Retry-After": str(int(wait) + 1)},
        )


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Registration(Credentials):
    email: str = Field(min_length=1, max_length=254)
    invite: str | None = None


class EmailBody(BaseModel):
    email: str = Field(min_length=1, max_length=254)


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=256)


def _send_confirmation(user_id: int, username: str, email: str) -> str:
    """Mint a link and hand it to the mailer. Returns how it was delivered."""
    token = auth.issue_email_token(user_id, email)
    return mailer.send_verification(email, username, token)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=config.SECURE_COOKIES,
        samesite="lax",
        path="/",
    )


@router.post("/register")
def register(body: Registration, request: Request, response: Response):
    _enforce(_register_by_client, client_key(request), "accounts created")
    _register_by_client.hit(client_key(request))

    mode = config.registration_mode()
    if mode == "closed":
        raise HTTPException(
            status_code=403,
            detail="Registration is closed. Ask an organiser for an account.",
        )
    if mode == "invite" and not hmac.compare_digest(
        (body.invite or "").strip(), config.INVITE_CODE
    ):
        raise HTTPException(status_code=403, detail="That invite code is not valid.")

    try:
        email = auth.clean_email(body.email)
        user_id = auth.create_user(body.username, body.password, email=email)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    # Signed in immediately, but unconfirmed: the session exists so the next
    # screen can be "check your inbox" rather than "now sign in again", and the
    # session grants nothing until the address is confirmed.
    _set_session_cookie(response, auth.create_session(user_id))
    delivery = _send_confirmation(user_id, body.username, email)
    return {
        "user": {
            "id": user_id,
            "username": body.username,
            "role": "user",
            "is_admin": False,
            "email": email,
            "email_verified": False,
        },
        "verification": delivery,
    }


@router.post("/login")
def login(body: Credentials, request: Request, response: Response):
    account = body.username.strip().lower()
    client = client_key(request)
    _enforce(_login_by_account, account, "failed sign-in attempts for that account")
    _enforce(_login_by_client, client, "failed sign-in attempts")

    try:
        user = auth.authenticate(body.username, body.password)
    except auth.AuthError as exc:
        # Only failures count, so a busy legitimate user is never locked out.
        _login_by_account.hit(account)
        _login_by_client.hit(client)
        raise HTTPException(status_code=401, detail=str(exc)) from None

    _login_by_account.reset(account)
    _set_session_cookie(response, auth.create_session(user["id"]))
    return {"user": user_public(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    auth.destroy_session(request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    return {"user": user_public(current_user(request))}


@router.post("/email")
def set_email(body: EmailBody, request: Request):
    """Name (or correct) the address on this account, and send it a link.

    This is also the migration path. Accounts that predate verification have no
    address at all; they sign in as they always did, land on the confirmation
    page, and this is what they post from it.
    """
    user = require_account(request)
    if auth.is_verified(user):
        raise HTTPException(
            status_code=400,
            detail="This account's address is already confirmed.",
        )
    _enforce(_verify_by_account, str(user["id"]), "confirmation emails requested")
    _verify_by_account.hit(str(user["id"]))

    try:
        email = auth.set_email(user["id"], body.email)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {
        "email": email,
        "verification": _send_confirmation(user["id"], user["username"], email),
    }


@router.post("/verify/resend")
def resend_verification(request: Request):
    user = require_account(request)
    if auth.is_verified(user):
        return {"email": user["email"], "verification": "already-verified"}
    if not user["email"]:
        raise HTTPException(
            status_code=400, detail="Add an email address to this account first."
        )
    _enforce(_verify_by_account, str(user["id"]), "confirmation emails requested")
    _verify_by_account.hit(str(user["id"]))
    return {
        "email": user["email"],
        "verification": _send_confirmation(
            user["id"], user["username"], user["email"]
        ),
    }


@router.post("/verify")
def verify(body: TokenBody, request: Request, response: Response):
    """Confirm an address from the link in the email.

    A POST rather than a GET on purpose: mail clients and link scanners fetch
    every URL in a message, and a GET here would have them burn the token
    before the member ever clicks it. The page reads the token out of the URL
    and posts it.

    Signing in is not required — the link may well be opened in a different
    browser from the one that signed up — so a valid token both confirms the
    address and issues a session for that account, which is what the person
    holding the link came for.
    """
    try:
        user = auth.confirm_email_token(body.token.strip())
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    current = current_user(request)
    if current is None or current["id"] != user["id"]:
        _set_session_cookie(response, auth.create_session(user["id"]))
    return {"user": user_public(user)}
