"""Project manager for saving and loading render state."""

import json
import os
import shutil
import time
import uuid
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import List, Optional
from datetime import datetime


class ProjectStateError(RuntimeError):
    """A project.json exists but cannot be read (bad JSON, the wrong shape, a
    field of the wrong type).

    Raised instead of returning None so that no caller mistakes an unreadable
    project for a missing one and recreates it from the deck - which would
    silently erase every edit (notes, overrides, undo history). The message
    names the folder, never an absolute path: it is shown to the user.
    """


# The narration settings a clip records (``SlideRenderState.audio_<name>``)
# and the project stamps (``ProjectState.generation_<name>``).
SETTING_NAMES = ("speed", "stability", "similarity_boost", "style")
# How far a clip's recorded setting may sit from the run's before the slide
# is redone (a float compared with a float).
SETTINGS_TOLERANCE = 0.01


@dataclass
class SlideRenderState:
    """State of a single slide's render."""
    index: int
    speaker_notes: str
    audio_path: Optional[str] = None
    image_path: Optional[str] = None
    video_path: Optional[str] = None  # MP4 clip for animated slides
    audio_duration: float = 0.0
    voice_id: str = ""
    # The narration settings this clip was made with, recorded by
    # ``ProjectManager.update_slide_audio`` as ``voice_id`` is: a clip records
    # its voice AND its speed (stability, similarity, style), so a slide whose
    # clip was made at other settings is redone whatever became of the run
    # that changed them - a preview that reached only its first slides, a
    # cancel, a failed slide. None on a record from before 0.14.1 means "made
    # at the project's stamped value", which was the oracle then
    # (``ProjectManager.clip_setting``); the stamp writes it in before it moves.
    audio_speed: Optional[float] = None
    audio_stability: Optional[float] = None
    audio_similarity_boost: Optional[float] = None
    audio_style: Optional[float] = None
    needs_regeneration: bool = False
    ai_enhanced: bool = False
    pause_override: Optional[float] = None
    has_animation: bool = False
    voice_override: Optional[str] = None  # Per-slide voice ID override
    alt_text: Optional[str] = None  # Accessibility alt text
    notes_history: Optional[List[str]] = None  # Undo history for speaker notes
    original_start_time: Optional[float] = None  # Video import: section start in source
    original_end_time: Optional[float] = None  # Video import: section end in source
    original_segments: Optional[List[dict]] = None  # Whisper segments: [{"start": f, "end": f, "text": s}]


@dataclass
class ProjectState:
    """Complete project render state."""
    pptx_path: str
    project_dir: str
    created_at: str
    last_modified: str
    voice_id: str
    slides: List[SlideRenderState] = None
    output_video_path: Optional[str] = None
    transition_pause: float = 1.0
    background_music_path: Optional[str] = None
    music_volume: float = 0.25
    generation_speed: float = 1.0
    generation_stability: float = 0.5
    generation_similarity_boost: float = 0.75
    generation_style: float = 0.0
    slide_order: Optional[List[int]] = None  # Custom slide ordering
    source_video_path: Optional[str] = None  # For video re-voicing: path to original video
    revoice_sync_mode: str = "synced"  # "synced" = match original timing, "free" = natural TTS pace

    def __post_init__(self):
        if self.slides is None:
            self.slides = []


def _replace_file(src: Path, dst: Path, attempts: int = 10, delay: float = 0.05) -> None:
    """``os.replace`` with a short retry: on Windows the move is refused while
    another thread still has the target open for reading (a few milliseconds
    for a project.json), and a save must not fail for that."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def _known_fields(cls, data: dict) -> dict:
    """``data`` restricted to the fields ``cls`` declares.

    ``save()`` writes every field, so a project.json written by a newer edition
    may carry a key this one does not know; ignoring it keeps the project
    readable instead of turning the whole file into a load error (which the
    generate path would answer by recreating the project from the deck and
    losing every edit).
    """
    names = {f.name for f in fields(cls)}
    return {key: value for key, value in data.items() if key in names}


class ProjectManager:
    """Manages project state for incremental rendering.

    Each PowerPoint file gets its own project directory containing:
    - project.json — serialized ProjectState with per-slide metadata
    - audio/       — generated and mixed audio files
    - images/      — exported slide images
    """

    PROJECT_FILE = "project.json"

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir = self.project_dir / "audio"
        self.images_dir = self.project_dir / "images"
        self.audio_dir.mkdir(exist_ok=True)
        self.images_dir.mkdir(exist_ok=True)
        self.state: Optional[ProjectState] = None

    @property
    def project_file(self) -> Path:
        return self.project_dir / self.PROJECT_FILE

    def create_project(
        self,
        pptx_path: Path,
        slide_notes: List[str],
        voice_id: str,
        transition_pause: float = 1.0,
        background_music_path: Optional[Path] = None,
        music_volume: float = 0.25
    ) -> ProjectState:
        """Create a new project from a PowerPoint file."""
        now = datetime.now().isoformat()

        slides = [
            SlideRenderState(
                index=i,
                speaker_notes=notes,
                voice_id=voice_id,
                needs_regeneration=True
            )
            for i, notes in enumerate(slide_notes)
        ]

        self.state = ProjectState(
            pptx_path=str(pptx_path),
            project_dir=str(self.project_dir),
            created_at=now,
            last_modified=now,
            voice_id=voice_id,
            slides=slides,
            transition_pause=transition_pause,
            background_music_path=str(background_music_path) if background_music_path else None,
            music_volume=music_volume
        )

        self.save()
        return self.state

    def load(self) -> Optional[ProjectState]:
        """Load project state from file.

        Returns None when there is no project.json. A file that exists but
        cannot be read raises :class:`ProjectStateError` - never None, so no
        caller recreates the project over the user's edits. Read-only: the
        predecessor's "one-time cleanup" that cleared ``needs_regeneration`` on
        slides with cached audio is gone; here ``load()`` runs on every request
        and it un-flagged every edit before the next generate could see it.
        """
        if not self.project_file.exists():
            return None

        try:
            with open(self.project_file, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ProjectStateError(self._unreadable(str(e))) from e

        slides_data = data.get("slides", []) if isinstance(data, dict) else None
        if (not isinstance(data, dict) or not isinstance(slides_data, list)
                or any(not isinstance(s, dict) for s in slides_data)):
            raise ProjectStateError(self._unreadable("it does not have the shape of a saved project"))

        try:
            data = dict(data)
            data.pop("slides", None)
            slides = [SlideRenderState(**_known_fields(SlideRenderState, s)) for s in slides_data]
            self.state = ProjectState(**_known_fields(ProjectState, data), slides=slides)
        except TypeError as e:  # a required field missing
            raise ProjectStateError(self._unreadable(str(e))) from e

        return self.state

    def _unreadable(self, reason: str) -> str:
        return (
            f"The saved state of this project ({self.PROJECT_FILE} in {self.project_dir.name}) "
            f"could not be read: {reason}. Nothing was changed - restore the file from a backup, "
            "or delete that folder to start the project over from the deck."
        )

    def save(self):
        """Save project state to file.

        Every dataclass field is written (``asdict`` recurses into the slides),
        so a field added to ``ProjectState`` or ``SlideRenderState`` is
        persisted without touching this method - the hand-kept key list this
        replaced silently dropped anything it did not name. Today's fields
        come out under the same keys as before, so ``load()`` reads old and
        new files alike.

        The JSON is written to a temporary file beside project.json and moved
        over it in one step (``os.replace``), so a reader never sees a
        truncated or half-written file and a failed write leaves the previous
        state intact.
        """
        if self.state is None:
            return

        self.state.last_modified = datetime.now().isoformat()

        tmp = self.project_dir / f".{self.PROJECT_FILE}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(asdict(self.state), f, indent=2)
            _replace_file(tmp, self.project_file)
        finally:
            tmp.unlink(missing_ok=True)

    def update_slide_audio(
        self,
        slide_index: int,
        audio_path: Path,
        duration: float,
        voice_id: str,
        speed: Optional[float] = None,
        stability: Optional[float] = None,
        similarity_boost: Optional[float] = None,
        style: Optional[float] = None,
    ):
        """Record a slide's new clip: its file, its length, its voice and the
        narration settings it was made with. A setting not given is recorded
        as the project's stamped one - what a caller that names none is
        working at - so a clip always says what it was made with."""
        if self.state is None or slide_index >= len(self.state.slides):
            return

        slide = self.state.slides[slide_index]
        slide.audio_path = str(audio_path)
        slide.audio_duration = duration
        slide.voice_id = voice_id
        given = {"speed": speed, "stability": stability, "similarity_boost": similarity_boost, "style": style}
        for name in SETTING_NAMES:
            value = given[name]
            setattr(slide, f"audio_{name}", value if value is not None else getattr(self.state, f"generation_{name}"))
        slide.needs_regeneration = False
        self.save()

    def clip_setting(self, slide: SlideRenderState, name: str) -> float:
        """The value of narration setting ``name`` ("speed", "stability",
        "similarity_boost", "style") that ``slide``'s clip was made with: the
        one recorded on the clip, or - on a record from before per-clip
        settings - the project's stamped one, which was the oracle then."""
        recorded = getattr(slide, f"audio_{name}", None)
        return recorded if recorded is not None else getattr(self.state, f"generation_{name}")

    def update_slide_image(self, slide_index: int, image_path: Path):
        """Update image for a specific slide."""
        if self.state is None or slide_index >= len(self.state.slides):
            return

        self.state.slides[slide_index].image_path = str(image_path)
        self.save()

    def update_slide_video(self, slide_index: int, video_path: Path, has_animation: bool = True):
        """Update video clip path for an animated slide."""
        if self.state is None or slide_index >= len(self.state.slides):
            return

        self.state.slides[slide_index].video_path = str(video_path)
        self.state.slides[slide_index].has_animation = has_animation
        self.save()

    def mark_slide_for_regeneration(self, slide_index: int):
        """Mark a slide's audio for regeneration."""
        if self.state is None or slide_index >= len(self.state.slides):
            return

        self.state.slides[slide_index].needs_regeneration = True
        self.save()

    def update_slide_notes(self, slide_index: int, new_notes: str):
        """Update speaker notes for a slide (marks for regeneration).

        Pushes old notes onto the undo history stack (max 20 entries).
        """
        if self.state is None or slide_index >= len(self.state.slides):
            return

        slide = self.state.slides[slide_index]
        if slide.speaker_notes != new_notes:
            # Push old notes to history for undo
            if slide.notes_history is None:
                slide.notes_history = []
            slide.notes_history.append(slide.speaker_notes)
            # Cap history at 20 entries
            if len(slide.notes_history) > 20:
                slide.notes_history = slide.notes_history[-20:]
            slide.speaker_notes = new_notes
            slide.needs_regeneration = True
            self.save()

    def undo_slide_notes(self, slide_index: int) -> Optional[str]:
        """Undo the last notes edit for a slide. Returns the restored text or None."""
        if self.state is None or slide_index >= len(self.state.slides):
            return None
        slide = self.state.slides[slide_index]
        if not slide.notes_history:
            return None
        slide.speaker_notes = slide.notes_history.pop()
        slide.needs_regeneration = True
        self.save()
        return slide.speaker_notes

    def update_generation_settings(
        self, speed: float, voice_id: str = None,
        stability: float = None, similarity_boost: float = None, style: float = None,
    ):
        """Stamp the project with the narration settings a completed stage
        ran at. The stamp is kept as that record (and as the value a clip
        made before per-clip settings counts as made at); it is no longer
        what tells the next render which slides to redo - each clip's own
        settings are (:meth:`get_slides_needing_regeneration`)."""
        if self.state is None:
            return
        # A clip from before per-clip settings (None) was made under the
        # stamp as it stands NOW - the stamp was the oracle then. Written in
        # before the stamp moves, so a preview or a cancelled run that stamps
        # new settings can never make an old clip pass as made at them.
        for slide in self.state.slides:
            if slide.audio_path:
                for name in SETTING_NAMES:
                    if getattr(slide, f"audio_{name}") is None:
                        setattr(slide, f"audio_{name}", getattr(self.state, f"generation_{name}"))
        self.state.generation_speed = speed
        if voice_id is not None:
            self.state.voice_id = voice_id
        if stability is not None:
            self.state.generation_stability = stability
        if similarity_boost is not None:
            self.state.generation_similarity_boost = similarity_boost
        if style is not None:
            self.state.generation_style = style
        self.save()

    # Keep backward compat alias
    def update_generation_speed(self, speed: float):
        self.update_generation_settings(speed=speed)

    def get_slides_needing_regeneration(
        self, current_speed: float = None, current_voice_id: str = None,
        current_stability: float = None, current_similarity_boost: float = None,
        current_style: float = None,
    ) -> List[int]:
        """The slides whose narration must be (re)made for a run at the given
        settings: those marked, those with no clip, and those whose CLIP
        records another voice, or other settings than the run's (beyond
        :data:`SETTINGS_TOLERANCE`) - judged clip by clip
        (:meth:`clip_setting`), never by the project-level stamp, which says
        what the last completed stage ran at and is kept for that. The stamp
        used to be the oracle, and every run that stamped it without narrating
        every slide - a preview inside its budget, a cancel, a slide whose
        narration failed - left slides at the old speed that nothing redid,
        because a clip recorded its voice but not its speed. A setting not
        given (None) is not compared. A slide with a voice override keeps its
        clip on a global voice change, as before.
        """
        if self.state is None:
            return []

        current = {"speed": current_speed, "stability": current_stability,
                   "similarity_boost": current_similarity_boost, "style": current_style}
        needing = []
        for s in self.state.slides:
            if s.needs_regeneration or not s.audio_path:
                needing.append(s.index)
            elif (current_voice_id is not None and s.voice_id and s.voice_id != current_voice_id
                    and not getattr(s, 'voice_override', None)):
                needing.append(s.index)
            elif s.speaker_notes.strip() and any(
                value is not None and abs(value - self.clip_setting(s, name)) > SETTINGS_TOLERANCE
                for name, value in current.items()
            ):
                needing.append(s.index)
        return needing

    def all_audio_ready(self) -> bool:
        """Check if all slides have audio generated."""
        if self.state is None:
            return False

        return all(
            s.audio_path and Path(s.audio_path).exists() and not s.needs_regeneration
            for s in self.state.slides
            if s.speaker_notes.strip()  # Only check slides with notes
        )

    def all_images_ready(self) -> bool:
        """Check if all slides have images exported."""
        if self.state is None:
            return False

        return all(
            s.image_path and Path(s.image_path).exists()
            for s in self.state.slides
        )

    def update_slide_pause(self, slide_index: int, pause: Optional[float]):
        """Set per-slide pause override (None to use global default)."""
        if self.state is None or slide_index >= len(self.state.slides):
            return
        self.state.slides[slide_index].pause_override = pause
        self.save()

    def set_output_video(self, video_path: Path):
        """Set the output video path."""
        if self.state:
            self.state.output_video_path = str(video_path)
            self.save()

    def delete_project(self) -> list:
        """Delete all project data: audio, images, project.json, and the project directory.

        Returns a list of paths that were deleted.
        """
        deleted = []
        if self.project_dir.exists():
            # Collect what we're deleting for the log
            for item in self.project_dir.rglob("*"):
                if item.is_file():
                    deleted.append(str(item))
            shutil.rmtree(self.project_dir, ignore_errors=True)
            deleted.append(str(self.project_dir))
        self.state = None
        return deleted


def get_project_dir(pptx_path: Path, base_dir: Path) -> Path:
    """Generate a project directory path for a PowerPoint file."""
    return base_dir / f"{pptx_path.stem}_project"


def project_has_edits(project_dir: Path) -> bool:
    """Return True if the project at ``project_dir`` holds user edits worth warning about.

    "Edits worth warning about" means the saved ``project.json`` contains at least one
    slide with non-empty ``speaker_notes``, an ``ai_enhanced`` flag, a per-slide
    voice/pause override, or non-empty ``notes_history``. These are the fields a user
    cannot regenerate from the source PPTX, so re-uploading would silently lose them.

    Never raises: a missing or corrupt ``project.json`` returns False.
    """
    project_dir = Path(project_dir)
    project_file = project_dir / ProjectManager.PROJECT_FILE
    if not project_file.exists():
        return False
    try:
        with open(project_file, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False

    slides = data.get("slides") or []
    if not isinstance(slides, list):
        return False
    for s in slides:
        if not isinstance(s, dict):
            continue
        if (s.get("speaker_notes") or "").strip():
            return True
        if s.get("ai_enhanced"):
            return True
        if s.get("voice_override") is not None:
            return True
        if s.get("pause_override") is not None:
            return True
        if s.get("notes_history"):
            return True
    return False


def saved_slide_count(project_dir: Path) -> Optional[int]:
    """Return the number of slides recorded in the saved ``project.json``.

    Returns None if the file is missing or unreadable. Used to warn when a
    re-uploaded deck's slide count differs from the saved one (notes misalignment).
    """
    project_dir = Path(project_dir)
    project_file = project_dir / ProjectManager.PROJECT_FILE
    if not project_file.exists():
        return None
    try:
        with open(project_file, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    slides = data.get("slides")
    if isinstance(slides, list):
        return len(slides)
    return None


def overwrite_reset_project(project_dir: Path) -> None:
    """Remove the entire project directory (project.json, audio, images).

    Used by the "Overwrite — new version" upload path: the new PPTX becomes the
    source of truth and all saved edits are discarded. No error if already absent.
    """
    project_dir = Path(project_dir)
    if project_dir.exists():
        shutil.rmtree(project_dir, ignore_errors=True)


def refresh_keep_edits(project_dir: Path) -> None:
    """Clear only the regenerable image cache, preserving ``project.json``.

    Used by the "Keep my edits & refresh" upload path: the cached PPTX is replaced
    but speaker notes / overrides / history in ``project.json`` are kept. Only the
    ``images/`` dir is removed so slide images re-export from the new deck.
    """
    project_dir = Path(project_dir)
    images_dir = project_dir / "images"
    if images_dir.exists():
        shutil.rmtree(images_dir, ignore_errors=True)
