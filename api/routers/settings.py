"""Studio settings that admins change at runtime. Today: the password policy."""

from fastapi import APIRouter, Depends

from api.deps import admin_only, current_user
from api.passwords import PasswordPolicy, describe_policy, load_policy, save_policy
from api.schemas import PasswordPolicyIn

router = APIRouter(prefix="/settings", tags=["settings"])


def _payload(policy: PasswordPolicy) -> dict:
    return {"policy": policy.to_dict(), "description": describe_policy(policy)}


@router.get("/password-policy")
def get_password_policy(user: dict = Depends(current_user)):
    """The rules a new password must meet — every signed-in user sees them beside
    the password fields, so this is not admin-only."""
    return _payload(load_policy())


@router.put("/password-policy")
def put_password_policy(body: PasswordPolicyIn, admin: dict = Depends(admin_only)):
    """Replace the policy. It applies from the next password set anywhere."""
    return _payload(save_policy(PasswordPolicy(**body.model_dump())))
