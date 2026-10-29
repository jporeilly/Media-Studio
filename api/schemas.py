"""Pydantic request/response models for the API layer."""

import re
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, StringConstraints, ValidationError,
    field_validator, model_validator,
)

from core.tone_adapter import get_available_tones
from core.translator import get_available_languages
from services.ai_slides import CUSTOM_TONE, MAX_ISSUE_CHARS, QA_CRITERIA
from services.captures import DOCUMENT_VERSION, MAX_SHAPES, MAX_TEXT_CHARS, MIN_CROP_SIDE
from services.narration import MAX_OFFSET_BATCH, MAX_OFFSET_SECONDS, MAX_SPEED, MAX_VOICE_CHARS, MIN_SPEED
from services.slides import MAX_NOTES_CHARS, MAX_PAUSE_SECONDS
from services import walkthroughs as walkthrough_rules

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
    courses_folder: str | None = None
    courses_export: StrictBool | None = None


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


# A kept-ranges list, `[[start, end], ...]` in source seconds.
Ranges = list[list[StrictFloat | StrictInt]]

# A clip's number: strict so a boolean is not coerced to 1.0; an int is still
# fine, as it is in ``Ranges`` (a JSON ``0`` is an int).
Number = StrictFloat | StrictInt


class MusicClipIn(BaseModel):
    """One clip of ``EditIn.music`` (spec §12.2): a library file placed on the
    OUTPUT axis. ``id`` is the client's own handle (``^[a-z0-9_-]{1,32}$``,
    unique in the list); ``file`` a name in the library; ``at`` where the
    clip starts in output seconds; ``in`` / ``out`` the slice of the FILE in
    its own seconds; ``gain`` a linear factor 0-1; ``fade_in`` / ``fade_out``
    seconds of linear ramp. Exactly these eight keys: an unknown one is a
    422 (``extra="forbid"``), and ``in`` - a Python keyword - is the field's
    alias, so the JSON says ``in`` and nothing else. Every bound - the id,
    the file's presence, the slice inside the file, the fades inside the
    clip - is ``services.edit.validate_music``'s to refuse, with a 400 that
    names the clip and the field.

    The list is sent whole, as a track's is: ``EditIn.music`` replaces the
    stored clips, and a body that does not name ``music`` leaves them alone.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    file: str
    at: Number
    in_: Number = Field(alias="in")
    out: Number
    gain: Number
    fade_in: Number
    fade_out: Number


class MarkerIn(BaseModel):
    """One marker of ``EditIn.markers`` (spec §13.2): a named moment of the
    PICTURE's source. ``id`` is the client's own handle (``^[a-z0-9_-]{1,32}$``,
    unique in the list); ``at`` where it sits in SOURCE seconds - never the
    output's, so a cut moves it with its frame and nothing rewrites it (trap
    39); ``name`` its text. Exactly these three keys: an unknown one is a
    422 (``extra="forbid"``), ``at`` is strict so a boolean is not coerced,
    and ``name`` is strict text so a number is not turned into a name.
    Every bound - the id, ``at`` inside the source, the name trimmed to 1-80
    characters - is ``services.edit.validate_markers``'s to refuse, with a
    400 that names the marker and the field.

    The list is sent whole, as the music is: ``EditIn.markers`` replaces the
    stored markers, and a body that does not name ``markers`` leaves them
    alone.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    at: Number
    name: StrictStr


class EditIn(BaseModel):
    """PUT /api/projects/{pid}/edit: the kept ranges of the source, in source
    seconds, one list per track - ``video`` and ``narration`` -, the music
    lane's clips, and the markers.

    **One rule for all four keys** (trap 32): a key the body does not name
    is UNCHANGED - whatever is stored stays -, ``null`` clears it (a track
    keeps everything again, the music is gone, the markers are gone), and a
    list replaces it; ``[]`` clears the music and the markers too, while an
    empty track list is a refusal. That is what lets a cut send its tracks
    and no ``music`` or ``markers`` without dropping either, a clip commit
    send ``music`` and no tracks without dropping the picture's cut, and a
    marker commit send ``markers`` alone. The route tells "not named" from
    "null" by ``model_fields_set``; a body that names nothing changes
    nothing.

    ``keep`` is the version-1 body and means BOTH tracks, for curl and any
    older client; the route refuses it beside ``video``, ``narration``,
    ``music`` or ``markers`` with a 400.

    The numbers are strict so a boolean is not coerced to 1.0 (an int is still
    fine), and an unknown key is a 422 - ``extra="forbid"``, the lesson
    ``TranscriptSegment`` learned, because pydantic's default would drop it
    silently. Everything else about a range - its order, an overlap, a bound
    past the source, NaN and infinity (which JSON lets through and a strict
    float accepts) - is ``services.edit.validate_keep``'s to refuse, with a
    400 that names the track and the range; everything about a clip beyond
    its shape is ``validate_music``'s, and about a marker
    ``validate_markers``'s.
    """

    model_config = ConfigDict(extra="forbid")

    keep: Ranges | None = None
    video: Ranges | None = None
    narration: Ranges | None = None
    music: list[MusicClipIn] | None = None
    markers: list[MarkerIn] | None = None


class OffsetIn(BaseModel):
    """One sentence of the batch offsets PATCH: its index in the stored
    transcript and its new offset, null clearing the override. The index is
    strict (a bool or a string is a 422; whether it is IN the transcript is
    the service's to answer, naming it, with a 400); the offset is bounded
    and strict exactly as ``SegmentOverride.offset`` is."""

    model_config = ConfigDict(extra="forbid")

    index: StrictInt
    offset: StrictFloat | None = Field(None, ge=-MAX_OFFSET_SECONDS, le=MAX_OFFSET_SECONDS)


class OffsetsIn(BaseModel):
    """PATCH /api/projects/{pid}/narration/offsets: several sentences' offsets
    in ONE request - the timeline's drag, or its Reset timing - applied as one
    write under the project's lock (``services.narration.update_offsets``).
    At least one entry; the cap is a bound on abuse, not on use (a two-hour
    recording has about 1500 sentences)."""

    model_config = ConfigDict(extra="forbid")

    offsets: list[OffsetIn] = Field(min_length=1, max_length=MAX_OFFSET_BATCH)


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
    # The same 0.5-2 bound as a deck render's speed and a sentence's own
    # (``services.narration``): outside it the request is a 422, never a job.
    speed: float = Field(1.0, ge=MIN_SPEED, le=MAX_SPEED)
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


# ── Annotations on a still (T3b) ──────────────────────────────────────────────
# The document the editor saves beside a still (``services.captures``): a
# version, shapes in the still's own pixel coordinates, an optional crop. It is
# validated HERE, with the still's size passed as the validation context
# (``AnnotationDocument.model_validate(data, context={"width": W, "height": H})``),
# so one model answers both "is this well formed" and "does it fit this still":
# every number finite and within -size .. 2 x size of the still (a shape may
# hang off the edge; the renderer clips it), a crop inside the still and at
# least MIN_CROP_SIDE a side. The route answers a failure with 400 and the
# first error as a plain line; nothing is written then. The frontend's pure
# model (``frontend/src/lib/annotate.ts``) writes exactly this shape.

Stroke = Literal["thin", "medium", "thick"]
TextSize = Literal["small", "medium", "large"]
#: A colour as the palette spells it.
Colour = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
ShapeId = Annotated[str, StringConstraints(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")]
#: A coordinate or a length: a finite number (JSON's ``NaN`` and ``Infinity`` are refused).
Coord = Annotated[float, Field(allow_inf_nan=False)]
Length = Annotated[float, Field(ge=1, allow_inf_nan=False)]


def _still_size(info) -> tuple[int, int] | None:
    context = getattr(info, "context", None) or {}
    width, height = context.get("width"), context.get("height")
    if width is None or height is None:
        return None
    return int(width), int(height)


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ShapeId
    colour: Colour
    stroke: Stroke = "medium"

    #: The fields measured along the still's width and its height. Mirrored field for field by the
    #: page's pure model (``frontend/src/lib/annotate.ts`` X_FIELDS / Y_FIELDS, whose ``clampShape`` keeps
    #: every one of them inside this range), so the page never hands the server a document it refuses.
    X_FIELDS: ClassVar[tuple[str, ...]] = ()
    Y_FIELDS: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _within_the_still(self, info):
        size = _still_size(info)
        if size is None:
            return self
        width, height = size
        for names, extent in ((self.X_FIELDS, width), (self.Y_FIELDS, height)):
            for name in names:
                value = getattr(self, name)
                if not -extent <= value <= 2 * extent:
                    raise ValueError(f"{self.type} {name} is outside the still (-{extent} to {2 * extent}).")
        return self


class SegmentShape(_Shape):
    """An arrow (its head at x2, y2) or a line."""

    type: Literal["arrow", "line"]
    x1: Coord
    y1: Coord
    x2: Coord
    y2: Coord
    X_FIELDS: ClassVar[tuple[str, ...]] = ("x1", "x2")
    Y_FIELDS: ClassVar[tuple[str, ...]] = ("y1", "y2")


class BoxShape(_Shape):
    """A rectangle or an ellipse outline, a blur (a mosaic of the pixels under
    it) or a highlight (translucent, multiply; the stroke is ignored)."""

    type: Literal["rect", "ellipse", "blur", "highlight"]
    x: Coord
    y: Coord
    width: Length
    height: Length
    X_FIELDS: ClassVar[tuple[str, ...]] = ("x", "width")
    Y_FIELDS: ClassVar[tuple[str, ...]] = ("y", "height")


class TextShape(_Shape):
    """A text box: the text wraps to the box's width."""

    type: Literal["text"]
    x: Coord
    y: Coord
    width: Length
    height: Length
    text: str = Field(max_length=MAX_TEXT_CHARS)
    text_size: TextSize = "medium"
    X_FIELDS: ClassVar[tuple[str, ...]] = ("x", "width")
    Y_FIELDS: ClassVar[tuple[str, ...]] = ("y", "height")


class CalloutShape(_Shape):
    """A filled, rounded text box with a tail to (tail_x, tail_y)."""

    type: Literal["callout"]
    x: Coord
    y: Coord
    width: Length
    height: Length
    tail_x: Coord
    tail_y: Coord
    text: str = Field(max_length=MAX_TEXT_CHARS)
    text_size: TextSize = "medium"
    X_FIELDS: ClassVar[tuple[str, ...]] = ("x", "width", "tail_x")
    Y_FIELDS: ClassVar[tuple[str, ...]] = ("y", "height", "tail_y")


class StepShape(_Shape):
    """A numbered step: the number is not stored - it is the step's place
    among the steps of its ``sequence`` in document order (Restart numbering
    starts a new sequence), so deleting one renumbers the rest."""

    type: Literal["step"]
    x: Coord
    y: Coord
    sequence: int = Field(ge=0, le=10_000)
    X_FIELDS: ClassVar[tuple[str, ...]] = ("x",)
    Y_FIELDS: ClassVar[tuple[str, ...]] = ("y",)


class MagnifyShape(_Shape):
    """A lens: the source rectangle, and where its 2x or 3x copy sits."""

    type: Literal["magnify"]
    x: Coord
    y: Coord
    width: Length
    height: Length
    lens_x: Coord
    lens_y: Coord
    factor: Literal[2, 3] = 2
    X_FIELDS: ClassVar[tuple[str, ...]] = ("x", "width", "lens_x")
    Y_FIELDS: ClassVar[tuple[str, ...]] = ("y", "height", "lens_y")


Shape = Annotated[
    SegmentShape | BoxShape | TextShape | CalloutShape | StepShape | MagnifyShape,
    Field(discriminator="type"),
]


class CropIn(BaseModel):
    """The one crop of a still, applied at export: whole pixels, inside the
    still, at least MIN_CROP_SIDE a side."""

    model_config = ConfigDict(extra="forbid")

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=MIN_CROP_SIDE)
    height: int = Field(ge=MIN_CROP_SIDE)

    @model_validator(mode="after")
    def _inside_the_still(self, info):
        size = _still_size(info)
        if size is None:
            return self
        width, height = size
        if self.x + self.width > width or self.y + self.height > height:
            raise ValueError(f"The crop runs past the still's edge ({width}x{height}).")
        return self


class AnnotationDocument(BaseModel):
    """PUT /captures/{cid}/annotations: the ``document`` field, as JSON."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[DOCUMENT_VERSION]
    shapes: list[Shape] = Field(default_factory=list, max_length=MAX_SHAPES)
    crop: CropIn | None = None


# ── Walkthroughs (W1) ─────────────────────────────────────────────────────────
# The whole document the builder saves (``PUT /api/walkthroughs/{wid}``),
# validated HERE with the pictures the walkthrough's folder holds passed as the
# context (``WalkthroughIn.model_validate(data, context={"pictures": {name:
# (W, H)}})``, from ``services.walkthroughs.held_pictures``), so one model
# answers "is this well formed" and "does it fit this walkthrough": every step
# names a picture that is there, at the size it really is, the hotspot inside
# it and at least MIN_HOTSPOT_SIDE a side, the texts within their limits, the
# ids and the pictures unique. The route answers a failure with 400 and the
# first error as a plain line; nothing is written then. The step cap is read
# at validation time from ``services.walkthroughs.MAX_STEPS`` (a test can
# lower it) and says what it is.

WalkthroughKind = Literal[walkthrough_rules.KINDS]
WalkthroughPlacement = Literal[walkthrough_rules.PLACEMENTS]
#: F3, F4: the real click the learner makes at a step, and how the player draws the hotspot (the one rule is the service's).
WalkthroughClick = Literal[walkthrough_rules.CLICKS]
WalkthroughMarker = Literal[walkthrough_rules.MARKERS]
#: A walkthrough's or a step's id: twelve hex characters, as a capture's.
Hex12 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]
#: A step's picture, by its file name in the walkthrough's folder.
StepImageName = Annotated[str, StringConstraints(pattern=r"^step-[0-9a-f]{12}\.png$")]
#: A control character a text may not carry (a step's text keeps its line breaks and tabs; a name keeps none). The
#: step's rule is ``services.walkthroughs.TEXT_CONTROL_RE`` - the one place; the workshop parser strips by it.
_TEXT_CONTROL_RE = walkthrough_rules.TEXT_CONTROL_RE
_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _no_control_characters(value: str) -> str:
    """The one rule for a NAME (a walkthrough's, a chapter's): no control character at all. On the type, so
    every route that takes the name - the create and the save - refuses the same thing."""
    if _NAME_CONTROL_RE.search(value):
        raise ValueError("A name may not contain control characters.")
    return value


#: The walkthrough's name: stripped, 1 to MAX_NAME_CHARS characters, no control characters.
WalkthroughName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=walkthrough_rules.MAX_NAME_CHARS), AfterValidator(_no_control_characters),
]
#: A chapter's name: stripped first, then at most MAX_CHAPTER_CHARS characters (the bound counts what is stored), no control characters.
ChapterName = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=walkthrough_rules.MAX_CHAPTER_CHARS), AfterValidator(_no_control_characters),
]


def _pictures(info) -> dict | None:
    context = getattr(info, "context", None) or {}
    return context.get("pictures")


def plain_step_text(value: str) -> str:
    """The one rule for a step's TEXT (its text, instruction or note): plain text with line breaks - CRLF normalised
    to LF, no other control character (a tab is allowed). ``StepIn`` applies it on a save; the add-step body a
    recording sends (``api.routers.walkthroughs.AddStepIn``) applies the same."""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if _TEXT_CONTROL_RE.search(value):
        raise ValueError("A step's text may not contain control characters.")
    return value


class HotspotIn(BaseModel):
    """The rectangle the viewer clicks, in the picture's pixels: whole pixels,
    inside it (the step checks that), at least MIN_HOTSPOT_SIDE a side."""

    model_config = ConfigDict(extra="forbid")

    x: StrictInt = Field(ge=0)
    y: StrictInt = Field(ge=0)
    width: StrictInt = Field(ge=walkthrough_rules.MIN_HOTSPOT_SIDE)
    height: StrictInt = Field(ge=walkthrough_rules.MIN_HOTSPOT_SIDE)


class StepSourceIn(BaseModel):
    """Where a step's picture came from - a still: informational only, never read back."""

    model_config = ConfigDict(extra="forbid")

    capture_id: Hex12
    annotated: bool = False


class WorkshopSourceIn(BaseModel):
    """Where an imported step came from (W2) - a course workshop: the course, the slug, the guide image
    (None when the guide showed none), its alt text and the launcher's file. Informational only."""

    model_config = ConfigDict(extra="forbid")

    course: str = Field(max_length=200)
    slug: str = Field(max_length=200)
    image: str | None = Field(None, max_length=walkthrough_rules.MAX_SOURCE_PATH_CHARS)
    alt: str = Field("", max_length=walkthrough_rules.MAX_ALT_CHARS)
    launcher: str | None = Field(None, max_length=walkthrough_rules.MAX_SOURCE_PATH_CHARS)


class WorkshopCaptureSourceIn(WorkshopSourceIn):
    """An imported step whose picture a capture replaced (W2c): the workshop's fields KEPT - so the step still
    says where in the guide it came from - with the capture's merged in (``services.walkthroughs.captured_source``;
    ``image`` is then null: the picture is no longer the guide's). Informational only."""

    capture_id: Hex12
    annotated: bool = False


class AnchorIn(BaseModel):
    """Where an imported step is in its guide (W2c): the key (its section and its first sentence, normalised -
    ``services.workshops.anchor_key``) and the ordinal among the guide's steps with that key. Set by the import,
    kept by the server through every save; Export to course places the step's figure by it."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=walkthrough_rules.MAX_ANCHOR_KEY_CHARS)
    ordinal: StrictInt = Field(ge=1, le=walkthrough_rules.MAX_ANCHOR_ORDINAL)

    @field_validator("key")
    @classmethod
    def _one_line(cls, value: str) -> str:
        if _NAME_CONTROL_RE.search(value):
            raise ValueError("An anchor's key may not contain control characters.")
        return value


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Hex12
    #: None for a step without a picture yet (W2): then the size is 0 x 0 and there is no hotspot.
    image: StepImageName | None
    width: StrictInt = Field(ge=0)
    height: StrictInt = Field(ge=0)
    hotspot: HotspotIn | None = None
    kind: WalkthroughKind = "tooltip"
    text: str = Field("", max_length=walkthrough_rules.MAX_STEP_TEXT)
    instruction: str = Field("", max_length=walkthrough_rules.MAX_INSTRUCTION_CHARS)
    placement: WalkthroughPlacement = "auto"
    chapter: ChapterName = ""
    note: str = Field("", max_length=walkthrough_rules.MAX_NOTE_CHARS)
    source: StepSourceIn | WorkshopCaptureSourceIn | WorkshopSourceIn | None = None
    #: W2c: where an imported step is in its guide; None for any other step (the save keeps the record's own).
    anchor: AnchorIn | None = None
    #: F3, F4: the real click the learner makes, and how the hotspot is drawn - a body from before the fields is "left" and "box".
    click: WalkthroughClick = "left"
    marker: WalkthroughMarker = "box"

    @field_validator("text", "instruction", "note")
    @classmethod
    def _plain_text(cls, value: str) -> str:
        """Plain text with line breaks: CRLF normalised to LF, no other control character (a tab is allowed)."""
        return plain_step_text(value)

    @model_validator(mode="after")
    def _image_named_by_id(self):
        """A step's picture is ``step-<id>.png`` and nothing else: ``replace_picture`` writes by the step's id,
        so a document that names another step's file would leave two steps on one file and an orphan. A step
        without a picture has no size and no hotspot: the hotspot comes with the capture."""
        if self.image is None:
            if self.width or self.height:
                raise ValueError(f"step {self.id} has no picture, so its size must be 0 x 0.")
            if self.hotspot is not None:
                raise ValueError(f"step {self.id} has no picture, so it cannot have a hotspot.")
            return self
        if self.width < 1 or self.height < 1:
            raise ValueError(f"step {self.id}: a picture's size is at least 1 x 1.")
        expected = walkthrough_rules.step_image_name(self.id)
        if self.image != expected:
            raise ValueError(f"step {self.id}'s picture must be {expected}, not {self.image}.")
        return self

    @model_validator(mode="after")
    def _against_the_pictures(self, info):
        pictures = _pictures(info)
        if pictures is None or self.image is None:
            return self
        size = pictures.get(self.image)
        if size is None:
            raise ValueError(f"step {self.id} names a picture the walkthrough does not hold ({self.image}).")
        width, height = size
        if (self.width, self.height) != (width, height):
            raise ValueError(f"step {self.id}: the picture is {width}x{height}, not {self.width}x{self.height}.")
        h = self.hotspot
        if h is not None and (h.x + h.width > width or h.y + h.height > height):
            raise ValueError(f"step {self.id}: the hotspot runs past the picture's edge ({width}x{height}).")
        return self


class WalkthroughIn(BaseModel):
    """PUT /walkthroughs/{wid}: the whole document, with the revision it was loaded at."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[walkthrough_rules.DOCUMENT_VERSION] = walkthrough_rules.DOCUMENT_VERSION
    name: WalkthroughName
    revision: StrictInt = Field(ge=1)
    steps: list[StepIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_and_bounded(self):
        """At most MAX_STEPS steps, every id unique (and so, since a step's picture is named by its id, every
        picture unique too)."""
        if len(self.steps) > walkthrough_rules.MAX_STEPS:
            raise ValueError(f"A walkthrough can hold at most {walkthrough_rules.MAX_STEPS} steps.")
        ids = [s.id for s in self.steps]
        if len(set(ids)) != len(ids):
            raise ValueError("Two steps carry the same id.")
        return self


def plain_validation_error(exc: ValidationError) -> str:
    """The first problem of a failed validation as one line a user can read:
    the field's path and pydantic's message, without the "Value error, "
    prefix a validator's own message gets."""
    first = exc.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ()))
    message = first.get("msg", "invalid")
    if message.startswith("Value error, "):
        message = message[len("Value error, "):]
    return f"{where}: {message}" if where else message
