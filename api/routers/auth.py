"""Login / logout / me - minimal opaque-session auth (seeded admin/admin)."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.deps import COOKIE_NAME, cookie_flags, current_user
from api.schemas import LoginRequest
from api.store import authenticate, create_session, delete_session

router = APIRouter(prefix="/auth", tags=["auth"])

_COOKIE_MAX_AGE = 7 * 24 * 3600


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response):
    user = authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = create_session(user["id"])
    response.set_cookie(COOKIE_NAME, token, max_age=_COOKIE_MAX_AGE, **cookie_flags(request))
    return {"user": user, "token": token}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        delete_session(token)
    flags = cookie_flags(request)
    response.delete_cookie(COOKIE_NAME, path=flags["path"], samesite=flags["samesite"],
                           secure=flags["secure"], httponly=flags["httponly"])
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}
