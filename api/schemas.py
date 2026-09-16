"""Pydantic request/response models for the API layer."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StringConstraints, field_validator

from core.tone_adapter import get_available_tones
from core.translator import get_available_languages
from services.ai_slides import CUSTOM_TONE, MAX_ISSUE_CHARS, QA_CRITERIA
from services.narration import MAX_OFFSET_SECONDS, MAX_SPEED, MAX_VOICE_CHARS, MIN_SPEED
from services.slides import MAX_NOTES_CHARS, MAX_PAUSE_SECONDS

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
    """One sentence of the whole-list transcript PATCH. This model is a TEXT
    editor and nothing else: the per-sentence timing overrides (offset, mute,
    voice, provider, speed) are NOT fields here, so a client cannot move a
    sentence by re-posting the list.

    ``extra="forbid"`` is what makes that safe. Pydantic's default is
    ``extra="ignore"``, which would drop the override keys silently on the way
    in - and ``set_transcript`` writes the list back wholesale, so every Save
    of the words would have quietly deleted every adjustment. The keys are
    carried across by index instead (``services.projects.set_transcript``):
    editing the words never moves the sentences.
    """

    model_config = ConfigDict(extra="forbid")

    start: float
    end: float
    text: str


class TranscriptUpdate(BaseModel):
    transcript: list[TranscriptSegment]


class SegmentOverride(BaseModel):
    """PATCH /api/projects/{pid}/transcript/{index}: one sentence's narration
    adjustment. A field left out is left alone; an explicit null clears it back
    to the default (as ``SlideUpdate``).

    ``offset`` is seconds added to where the sentence is pinned - positive
    pushes it later, negative earlier, and the UI must say that the pin is a
    floor rather than a position: a sentence can only be pulled earlier as far
    as the previous one's synthesised audio actually ends. ``muted`` leaves it
    out of the narration. ``voice`` / ``provider`` name a per-sentence voice
    and which provider it belongs to, exactly as ``SlideUpdate`` does.
    ``speed`` is an explicit TTS speed that bypasses both the per-sentence rule
    and the post-synthesis squeeze - the user's number wins.

    An unknown key is a 422, and the numbers are strict so a boolean is not
    coerced to 1.0 (an int is still fine), the same reason
    ``SlideUpdate.pause_override`` is strict.
    """

    model_config = ConfigDict(extra="forbid")

    offset: StrictFloat | None = Field(None, ge=-MAX_OFFSET_SECONDS, le=MAX_OFFSET_SECONDS)
    muted: bool | None = None
    voice: str | None = Field(None, max_length=MAX_VOICE_CHARS)
    provider: str | None = None  # validated with check_voice_for_provider
    speed: StrictFloat | None = Field(None, ge=MIN_SPEED, le=MAX_SPEED)


class EditIn(BaseModel):
    """PUT /api/projects/{pid}/edit: the whole list of kept ranges of the
    source, in source seconds, replacing whatever was stored (the list is
    small; a partial PATCH would buy nothing).

    The numbers are strict so a boolean is not coerced to 1.0 (an int is still
    fine), and an unknown key is a 422 - ``extra="forbid"``, the lesson
    ``TranscriptSegment`` learned, because pydantic's default would drop it
    silently. Everything else about a range - its order, an overlap, a bound
    past the source, NaN and infinity (which JSON lets through and a strict
    float accepts) - is ``services.edit.validate_keep``'s to refuse, with a
    400 that names the range.
    """

    model_config = ConfigDict(extra="forbid")

    keep: list[list[StrictFloat | StrictInt]]


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


class SlideUpdate(BaseModel):
    """PATCH /api/projects/{pid}/slides/{index}. Every field is optional: a
    field left out is left alone, an explicit null clears that override
    (voice, pause, alt text) back to the studio default; notes are text only.
    ``provider`` names the narration provider a ``voice_override`` belongs to
    (edge_tts | kokoro; null = the studio default), so an id from the other
    provider is refused now, as generate refuses its voice, rather than being
    dropped silently at render time. An unknown key is a 422, and the pause
    is strict so a boolean is not coerced to 1.0 (an int is still fine).
    """

    model_config = ConfigDict(extra="forbid")

    speaker_notes: str | None = Field(None, max_length=MAX_NOTES_CHARS)
    voice_override: str | None = Field(None, max_length=200)
    pause_override: StrictFloat | None = Field(None, ge=0, le=MAX_PAUSE_SECONDS)
    alt_text: str | None = Field(None, max_length=2000)
    provider: str | None = None


class SlideNotesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: StrictInt = Field(ge=0)
    speaker_notes: str = Field(max_length=MAX_NOTES_CHARS)


class SlidesBulkUpdate(BaseModel):
    """PATCH /api/projects/{pid}/slides: the notes of several slides at once."""

    model_config = ConfigDict(extra="forbid")

    slides: list[SlideNotesIn] = Field(max_length=2000)


class RenderRequest(BaseModel):
    """POST /api/projects/{pid}/slides/render: ``force`` exports the previews
    again even when every image exists (previews rendered before their source
    was recorded cannot be used by vision until they are)."""

    model_config = ConfigDict(extra="forbid")

    force: bool = False


# -- the AI assistant (api/routers/ai.py, services/ai_slides.py) -------------

QaCriterion = Literal[QA_CRITERIA]  # grammar | tone | flow | transitions


class AiNotesRequest(BaseModel):
    """POST /ai/notes and /ai/enhance: which slides (``empty`` = those without
    notes, the default; ``all``), optionally narrowed to ``slide_indexes``,
    and whether to show the model the slide images (honoured only when the
    project's previews are real renders - see /ai/status)."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["empty", "all"] = "empty"
    slide_indexes: list[Annotated[StrictInt, Field(ge=0)]] | None = Field(None, max_length=2000)
    use_vision: bool = False


class AiEnhanceRequest(AiNotesRequest):
    """POST /ai/enhance: as /ai/notes, but every slide by default (``all``)."""

    scope: Literal["empty", "all"] = "all"


class AiToneRequest(BaseModel):
    """POST /ai/tone: a preset from core.tone_adapter, or Custom with its instruction."""

    model_config = ConfigDict(extra="forbid")

    tone: str = Field(max_length=40)
    custom_prompt: str | None = Field(None, max_length=2000)

    @field_validator("tone")
    @classmethod
    def _known_tone(cls, value: str) -> str:
        names = list(get_available_tones()) + [CUSTOM_TONE]
        if value not in names:
            raise ValueError(f"Unknown tone '{value}'. Choose one of: {', '.join(names)}.")
        return value


class AiTranslateRequest(BaseModel):
    """POST /ai/translate: a display name from /api/languages; ``match_voice``
    asks for a voice of ``provider`` (edge_tts | kokoro; null = the configured
    one) in that language in the job's result."""

    model_config = ConfigDict(extra="forbid")

    language: str = Field(max_length=40)
    match_voice: bool = True
    provider: str | None = None

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        names = list(get_available_languages())
        if value not in names:
            raise ValueError(f"Unknown language '{value}'. Choose one of: {', '.join(names)}.")
        return value


class AiPacingRequest(BaseModel):
    """POST /ai/pacing: the rules alone (instant, no job) or the model per slide (a job)."""

    model_config = ConfigDict(extra="forbid")

    use_ai: bool = False


class AiQaDocRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    num_questions: StrictInt = Field(10, ge=1, le=50)


class AiEnhanceOneRequest(BaseModel):
    """POST /slides/{i}/ai/enhance: ``notes`` is the editor's current text
    (an unsaved draft included); null = the saved notes."""

    model_config = ConfigDict(extra="forbid")

    use_vision: bool = False
    notes: str | None = Field(None, max_length=MAX_NOTES_CHARS)


class AiQaFixRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    criterion: QaCriterion
    issue: str = Field(max_length=MAX_ISSUE_CHARS)
