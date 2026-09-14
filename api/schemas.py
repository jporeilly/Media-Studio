"""Pydantic request/response models for the API layer."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StringConstraints

# A title-card text: stripped, so a whitespace-only value makes no card, and capped.
CardText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]

# The generation enums, as the engine spells them (core/video_creator.py). Keep in
# step with services.studio_settings.TRANSITIONS / WATERMARK_POSITIONS (a test does).
Transition = Literal[
    "none", "fade-to-black", "fade-to-white", "crossfade",
    "slide-left", "slide-right", "slide-up", "slide-down", "zoom-in",
]
WatermarkPosition = Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"]
SubtitleMode = Literal["none", "slide", "whisper"]


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
    slide_transition: str | None = None
    transition_duration: StrictFloat | None = None
    watermark_text: str | None = None
    watermark_position: str | None = None
    watermark_opacity: StrictFloat | None = None


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
    """POST /api/projects/{pid}/generate. Every field is optional and defaults
    to today's behaviour: the narration and the six render options that have a
    studio default (transition, pause, watermark) fall back to Settings › Studio
    as it is at request time when they are null or absent; the rest carry the
    engine's own defaults. An unknown key is a 422 rather than silently dropped.
    """

    model_config = ConfigDict(extra="forbid")

    provider: str | None = None  # edge_tts | kokoro; None = the configured provider (Settings › Studio)
    voice_id: str | None = None  # None/"" = the provider's configured default voice
    speed: float = Field(1.0, ge=0.5, le=2.0)
    preset: str = "youtube_1080p"

    # Slide transitions (None = the studio default).
    slide_transition: Transition | None = None
    transition_duration: float | None = Field(None, ge=0.1, le=2.0)
    transition_pause: float | None = Field(None, ge=0.0, le=5.0)

    # Intro / outro title cards ("" = no card). The outro takes no subtitle.
    intro_text: CardText = ""
    intro_subtitle: CardText = ""
    intro_duration: float = Field(3.0, ge=1.0, le=10.0)
    outro_text: CardText = ""
    outro_duration: float = Field(3.0, ge=1.0, le=10.0)

    # Text watermark (None = the studio default; "" = none for this render).
    watermark_text: str | None = None
    watermark_position: WatermarkPosition | None = None
    watermark_opacity: float | None = Field(None, ge=0.1, le=1.0)

    # slide = one cue per slide from the notes; whisper = a word-level Whisper
    # pass over the rendered MP4 (SRT + VTT), a second transcription.
    subtitles: SubtitleMode = "slide"

    export_webm: bool = False
    export_gif: bool = False  # the first 30 s
    export_audio_only: bool = False  # MP3

    # > 0 renders only the first N seconds to a separate "<stem>_preview.mp4".
    preview_seconds: float = Field(0, ge=0, le=120)


class RevoiceRequest(BaseModel):
    provider: str | None = None  # as GenerateRequest
    voice_id: str | None = None
    speed: float = 1.0
    language: str | None = None  # display name from /api/languages; None = keep original
