"""FastAPI dependencies: session-cookie auth and role checks (minimal skeleton)."""

from fastapi import Depends, HTTPException, Request, status

from api.store import validate_session

COOKIE_NAME = "ms_session"


def cookie_flags(request: Request) -> dict:
    """Attributes for the session cookie, matched by login and logout.

    The cookie is marked Secure only when the request arrived over HTTPS, so a
    plain-HTTP dev install can still store it and sign in.
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    secure = forwarded == "https" or request.url.scheme == "https"
    return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}


def _token_from_request(request: Request) -> str | None:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def optional_user(request: Request) -> dict | None:
    """Current user from the session cookie / bearer token, or None."""
    return validate_session(_token_from_request(request) or "")


def current_user(request: Request) -> dict:
    user = optional_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


def require_roles(*roles: str):
    """Dependency factory: the current user must hold one of ``roles``."""

    def _dep(user: dict = Depends(current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _dep


admin_only = require_roles("admin")
