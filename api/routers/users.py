"""Accounts (admins only): list, create, edit, deactivate / reactivate, reset a password.

There is no delete: an account that must go is deactivated, which refuses its
sign-in and ends its sessions while keeping the row. The password policy lives
in ``api.passwords`` and is applied here and by the change-password endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException

from api.audit import USER_CREATE, USER_RESET_PASSWORD, USER_UPDATE, audit, changed_keys
from api.deps import admin_only
from api.passwords import validate_password
from api.schemas import ResetPasswordIn, UserIn, UserPatch
from api.store import (
    ROLES,
    change_password,
    count_active_admins,
    create_user,
    delete_sessions_for_user,
    get_user,
    list_users,
    update_user,
)

router = APIRouter(prefix="/users", tags=["users"])


def _check_policy(password: str, username: str | None) -> None:
    problem = validate_password(password, username)
    if problem:
        raise HTTPException(status_code=400, detail=problem)


@router.get("")
def index(admin: dict = Depends(admin_only)):
    """Every account, active or not, without password hashes."""
    return {"users": list_users()}


@router.post("", status_code=201)
def create(body: UserIn, admin: dict = Depends(admin_only)):
    """Create an account; it must change its password at first login."""
    username = body.username.strip()
    display_name = body.display_name.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    if not display_name:
        raise HTTPException(status_code=400, detail="Display name is required")
    if body.role not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role")
    _check_policy(body.password, username)
    try:
        created = create_user(username, body.password, display_name, role=body.role, must_change_password=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit(USER_CREATE, user=admin, entity="user", entity_id=created["id"],
          detail=f"{created['username']} ({created['role']})")
    return created


@router.patch("/{user_id}")
def patch(user_id: str, body: UserPatch, admin: dict = Depends(admin_only)):
    """Edit display name / role / active flag. Deactivating ends the user's sessions.

    Two guards keep the app administrable: the last active admin can be neither
    deactivated nor demoted, and an admin cannot deactivate or demote themselves.
    """
    target = get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    changes = body.model_dump(exclude_none=True)
    if "display_name" in changes:
        changes["display_name"] = changes["display_name"].strip()
        if not changes["display_name"]:
            raise HTTPException(status_code=400, detail="Display name is required")
    if "role" in changes and changes["role"] not in ROLES:
        raise HTTPException(status_code=400, detail="Unknown role")

    deactivating = changes.get("is_active") is False
    demoting = "role" in changes and changes["role"] != "admin"
    if (deactivating or demoting) and target["role"] == "admin" and target["is_active"] and count_active_admins() <= 1:
        raise HTTPException(status_code=400, detail="This is the last active admin account; it cannot be deactivated or demoted")
    if user_id == admin["id"]:
        if deactivating:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
        if demoting:
            raise HTTPException(status_code=400, detail="You cannot remove your own admin role")

    updated = update_user(user_id, **changes)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    audit(USER_UPDATE, user=admin, entity="user", entity_id=user_id,
          detail=f"{updated['username']}: {changed_keys(changes)}")
    return updated


@router.post("/{user_id}/reset-password")
def reset_password(user_id: str, body: ResetPasswordIn, admin: dict = Depends(admin_only)):
    """Set a temporary password the user must replace at next login; their sessions end now."""
    target = get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    _check_policy(body.new_password, target["username"])
    change_password(user_id, body.new_password, must_change=True)
    delete_sessions_for_user(user_id)
    # Whose password was reset - never the temporary password itself.
    audit(USER_RESET_PASSWORD, user=admin, entity="user", entity_id=user_id,
          detail=target["username"])
    return {"ok": True}
