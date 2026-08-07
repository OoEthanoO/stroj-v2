"""Registration, login, logout, whoami."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, config
from .deps import current_user, user_public

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.post("/register")
def register(body: Credentials, response: Response):
    try:
        user_id = auth.create_user(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _set_session_cookie(response, auth.create_session(user_id))
    return {
        "user": {
            "id": user_id,
            "username": body.username,
            "role": "user",
            "is_admin": False,
        }
    }


@router.post("/login")
def login(body: Credentials, response: Response):
    try:
        user = auth.authenticate(body.username, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
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
