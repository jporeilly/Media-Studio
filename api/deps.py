"""FastAPI dependencies: session-cookie auth, role checks, project ownership.

One module owns "who may do what": the session (``current_user``), the roles
(``require_roles`` / ``admin_only``) and, since a project has an owner, who may
see and touch a project (``may_access_project`` / ``require_project``). The
project routers, the slide editor and the AI assistant all fetch a project
through ``require_project`` rather than each repeating ``get_project`` + a 404.
"""

from fastapi import Depends, HTTPException, Request, status

from api.store import validate_session
from services import projects as project_store
from utils.logger import get_logger

log = get_logger("AUTH")

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


# What an account may still call while it must change its password: the auth
# routes (to change it, see who it is, or leave) and a read of the password
# policy (the gate page shows the rules beside the field). Everything else is
# refused until the change is made, so a seeded or admin-issued password can
# never be used for real work - from the UI or from a script.
PASSWORD_CHANGE_REQUIRED = "password_change_required"
_ALLOWED_PREFIXES_WHILE_PENDING = ("/api/auth/",)
_ALLOWED_GETS_WHILE_PENDING = ("/api/settings/password-policy",)


def _allowed_while_password_change_pending(request: Request) -> bool:
    path = request.url.path
    if path.startswith(_ALLOWED_PREFIXES_WHILE_PENDING):
        return True
    return request.method == "GET" and path in _ALLOWED_GETS_WHILE_PENDING


def current_user(request: Request) -> dict:
    user = optional_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if user.get("must_change_password") and not _allowed_while_password_change_pending(request):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PASSWORD_CHANGE_REQUIRED)
    return user


def require_roles(*roles: str):
    """Dependency factory: the current user must hold one of ``roles``."""

    def _dep(user: dict = Depends(current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return _dep


admin_only = require_roles("admin")


# ── project ownership ────────────────────────────────────────────────────────
# A project records its owner in its own project.json (services/projects.py), so
# "a project is a directory you can zip" stays true and no database row can rot
# against a deleted directory. One rule, written once, used by the list filter
# and by every project-scoped route.

PROJECT_FORBIDDEN = "Only an admin or the project's owner can do this."

# Projects imported before ownership existed carry no owner_id. They are treated
# as ADMIN-OWNED - never as visible to everyone, which would hand every editor
# every legacy project the moment this shipped. Said once per process, not once
# per record, so a store full of legacy projects does not fill the log.
_legacy_notice_logged = False


def _note_legacy_project() -> None:
    global _legacy_notice_logged
    if _legacy_notice_logged:
        return
    _legacy_notice_logged = True
    log.info(
        "Some projects have no owner_id (imported before project ownership). "
        "They are treated as admin-owned; an admin can see them and re-import "
        "or hand them over as needed."
    )


def may_access_project(record: dict, user: dict) -> bool:
    """Whether ``user`` may see and act on the project ``record``.

    Admins may act on every project; everyone else only on their own. A record
    with no ``owner_id`` is admin-owned (see above).
    """
    if user.get("role") == "admin":
        return True
    owner_id = record.get("owner_id")
    if not owner_id:
        _note_legacy_project()
        return False
    return owner_id == user.get("id")


def require_project(pid: str, user: dict) -> dict:
    """The project record for ``pid``, or refuse: 404 when it does not exist,
    403 when it is not this user's to touch.

    The single "fetch this project or refuse" helper - every project-scoped
    route in ``routers/projects.py``, ``routers/slides.py`` and ``routers/ai.py``
    goes through it, so ownership cannot be enforced in eighteen slightly
    different ways.

    NOT for the services the jobs call (``services/ai_slides.py``,
    ``services/revoice.py``, ``services/slides.py``,
    ``services/transcription.py``): they run on a worker thread with no request
    and no user, and the route that queued the job has already authorised the
    caller.
    """
    record = project_store.get_project(pid)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
    if not may_access_project(record, user):
        # Deliberately NOT the jobs rule (api/routers/jobs.py): a job with no
        # user_id is one nobody claimed, so anyone may cancel it; a project with
        # no owner_id predates ownership and belongs to the admins.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=PROJECT_FORBIDDEN)
    return record
