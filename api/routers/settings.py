"""Studio settings that admins change at runtime: the password policy and the
studio defaults (narration provider and voices, Whisper model, Ollama model,
output folder, pacing)."""

from fastapi import APIRouter, Depends, HTTPException

from api.deps import admin_only, current_user
from api.passwords import PasswordPolicy, describe_policy, load_policy, save_policy
from api.schemas import PasswordPolicyIn, StudioSettingsIn
from services import studio_settings

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


def _studio_payload() -> dict:
    return {"settings": studio_settings.get_settings(), "options": studio_settings.describe()}


@router.get("/studio")
def get_studio_settings(user: dict = Depends(current_user)):
    """The studio defaults plus the options to render them from. Every signed-in
    user reads them: the project pages preselect the narration provider and
    voice from here."""
    return _studio_payload()


@router.put("/studio")
def put_studio_settings(body: StudioSettingsIn, admin: dict = Depends(admin_only)):
    """Change some of the studio defaults (a partial body). They apply to the
    next job; a bad value is refused as a whole with a readable 400."""
    try:
        studio_settings.update_settings(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _studio_payload()
