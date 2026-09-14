"""Pydantic request/response models for the API layer."""

from pydantic import BaseModel, Field


class PasswordPolicyIn(BaseModel):
    """Settings › Password policy. The bounds mirror ``api/passwords.py``."""

    min_length: int = Field(ge=4, le=64)
    require_upper: bool = False
    require_digit: bool = False
    require_symbol: bool = False
    forbid_username: bool = True
    forbid_common: bool = True


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class UserIn(BaseModel):
    username: str
    password: str
    display_name: str
    role: str = "editor"


class UserPatch(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ResetPasswordIn(BaseModel):
    new_password: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class TranscribeRequest(BaseModel):
    model: str | None = None  # Whisper model size; None = recommended default


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


class TranscriptUpdate(BaseModel):
    transcript: list[TranscriptSegment]


class GenerateRequest(BaseModel):
    voice_id: str
    speed: float = 1.0
    preset: str = "youtube_1080p"


class RevoiceRequest(BaseModel):
    voice_id: str
    speed: float = 1.0
    language: str | None = None  # display name from /api/languages; None = keep original
