"""Login / logout / me / change password - opaque-session auth over ``api.store``."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.audit import AUTH_LOGIN, AUTH_LOGIN_FAILED, AUTH_LOGOUT, AUTH_PASSWORD_CHANGE, audit
from api.deps import COOKIE_NAME, cookie_flags, current_user, optional_user
from api.passwords import validate_password
from api.schemas import LoginRequest, PasswordChangeIn
from api.store import (
    authenticate,
    change_password,
    create_session,
    delete_session,
    get_user,
    touch_last_login,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = 7 * 24 * 3600


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    username = body.username.strip()
    user = authenticate(username, body.password)
    if not user:
        # The failure is recorded too, and with the username that was TRIED -
        # there is no session to take a user from, so the row carries the name
        # and a NULL user_id. That is what the denormalised column is for.
        audit(AUTH_LOGIN_FAILED, username=username, entity="auth")
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user["last_login"] = touch_last_login(user["id"])
    token = create_session(user["id"])
    response.set_cookie(COOKIE_NAME, token, max_age=_COOKIE_MAX_AGE, **cookie_flags(request))
    audit(AUTH_LOGIN, user=user, entity="auth", entity_id=user["id"])
    return {"user": user, "token": token}


@router.post("/logout")
def logout(request: Request, response: Response):
    # Who is leaving, read before the session goes. ``optional_user`` and not
    # ``current_user``: logging out is allowed with a dead or missing session
    # (and while a password change is pending), it just records no actor.
    user = optional_user(request)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(token)
    flags = cookie_flags(request)
    response.delete_cookie(COOKIE_NAME, path=flags["path"], samesite=flags["samesite"],
                           secure=flags["secure"], httponly=flags["httponly"])
    if user:
        audit(AUTH_LOGOUT, user=user, entity="auth", entity_id=user["id"])
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    # Re-read the row so must_change_password (and the rest) reflect anything
    # changed since the session was issued - an admin's reset, the user's own change.
    return {"user": get_user(user["id"]) or user}


@router.post("/change-password")
def change_pw(body: PasswordChangeIn, user: dict = Depends(current_user)):
    """Set a new password for the signed-in user; the session making the call stays valid."""
    if not verify_password(user["id"], body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    problem = validate_password(body.new_password, user.get("username"))
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    change_password(user["id"], body.new_password, must_change=False)
    # The event, never the password (nor its length, nor any hint of it).
    audit(AUTH_PASSWORD_CHANGE, user=user, entity="user", entity_id=user["id"])
    return {"ok": True}
