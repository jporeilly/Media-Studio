"""Pydantic request/response models for the API layer."""

from pydantic import BaseModel, ConfigDict, Field, StrictFloat


class PasswordPolicyIn(BaseModel):
    """Settings › Password policy. The bounds mirror ``api/passwords.py``."""

    min_length: int = Field(ge=4, le=64)
    require_upper: bool = False
    require_digit: bool = False
    require_symbol: bool = False
    forbid_username: bool = True
    forbid_common: bool = True


class StudioSettingsIn(BaseModel):
    """Settings › Studio (``services/studio_settings.py``). Every field is
    optional: a PUT sends only what changes, and a null leaves a field alone.
    An unknown key is a 422 rather than silently dropped, and the numbers are
    strict so a boolean is not coerced to 1.0 (an int is still fine)."""

    model_config = ConfigDict(extra="forbid")

    tts_provider: str | None = None
    edge_tts_voice: str | None = None
    kokoro_voice: str | None = None
    kokoro_lang: str | None = None
    whisper_model: str | None = None
    ollama_model: str | None = None
    output_folder: str | None = None
    transition_pause: StrictFloat | None = None
    music_volume: StrictFloat | None = None


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
    provider: str | None = None  # edge_tts | kokoro; None = the configured provider (Settings › Studio)
    voice_id: str | None = None  # None/"" = the provider's configured default voice
    speed: float = 1.0
    preset: str = "youtube_1080p"


class RevoiceRequest(BaseModel):
    provider: str | None = None  # as GenerateRequest
    voice_id: str | None = None
    speed: float = 1.0
    language: str | None = None  # display name from /api/languages; None = keep original
