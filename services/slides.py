"""Per-slide state of a deck or PDF project: the engine's own store, exposed.

The engine keeps every slide's state (notes, per-slide overrides, undo
history, rendered image and audio) in the INNER project of a Media Studio
project, ``data/projects/<pid>/<stem>_project/project.json``
(``core.project_manager.ProjectManager``) - but only created it when the first
generate job ran. This module is the accessor layer the slide editor's API
reads and writes it through:

- ``manager(pid)`` opens that inner project and MATERIALISES it when absent:
  a deck's notes through ``PPTXReader`` (no slide images are exported - that
  is ``ensure_images``, a job), a PDF through ``FileItem.load_pdf`` (its page
  images come with the import, as before). A project.json that exists but
  cannot be read is never recreated: ``ProjectManager.load`` raises
  ``ProjectStateError`` (the routes answer 409). The outer record gets its
  ``slide_count`` reconciled and ``engine_project_dir`` (the directory NAME
  under the project, never an absolute path).
- ``list_slides`` / ``update_slide`` / ``undo_slide`` / ``reset_slide`` /
  ``bulk_update`` go through the manager's own mutators (history, the
  ``needs_regeneration`` flag) under a per-project lock: ``project.json`` has
  none of its own and two requests may edit the same project at once. A job
  attached to the project (generate, render) is refused by the routes
  (``services.jobs.active_for``), since it holds its own copy of the state.
- ``ensure_images`` exports a deck's slide images behind ``SLIDE_EXPORT_LOCK``.
  PowerPoint COM is a single instance and ``services.jobs`` runs two workers,
  so every slide export in this process (here and the generate pipeline's,
  ``services.processing``) queues on this one lock and says so in its
  progress while it waits.
- ``image_path`` and ``export_notes_pptx`` derive their files from the
  project directory and the slide index; the absolute paths ``project.json``
  stores are never trusted for serving and never returned (``list_slides``
  carries no paths at all).

``images_source`` on the outer record says where the slide images came from:
``powerpoint`` (real renders), ``pillow`` (the title-only fallback - vision
features must skip it), ``pdf`` (page renders), or None when unknown.
"""

import math
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.project_manager import ProjectManager, get_project_dir
from services import projects as store
from services import studio_settings

# One slide export at a time, process-wide (PowerPoint COM is single-instance).
SLIDE_EXPORT_LOCK = threading.Lock()
WAITING_FOR_EXPORT = "Waiting for PowerPoint to finish another export…"

MAX_NOTES_CHARS = 20_000
MAX_PAUSE_SECONDS = 30.0
SLIDE_KINDS = ("deck", "pdf")
IMAGE_SOURCES = ("powerpoint", "pillow", "pdf")

# The exporters' file names by project kind, 1-based: PPTXExporter writes
# slide_NNN.png, PDFReader page_NNN.png. Derived from the index, never read
# back from the stored absolute string.
_IMAGE_NAME = {"deck": "slide_{n:03d}.png", "pdf": "page_{n:03d}.png"}

_UNSET = object()

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# The deck's own notes / title / body text per project, keyed on the source
# file's (mtime, size) so a re-read happens only when the file changes.
_deck_cache: dict[str, tuple[tuple[int, int], list]] = {}


class ProjectNotFound(LookupError):
    """No project with that id (the routes answer 404). A dedicated class, so
    an IndexError or KeyError from a malformed record is not mistaken for it."""


def project_lock(pid: str) -> threading.Lock:
    """The lock every read-modify-write of one project's inner project.json takes."""
    with _locks_guard:
        lock = _locks.get(pid)
        if lock is None:
            lock = _locks[pid] = threading.Lock()
        return lock


def forget(pid: str) -> None:
    """Drop the per-project lock and the deck cache (the project was deleted)."""
    with _locks_guard:
        _locks.pop(pid, None)
    _deck_cache.pop(pid, None)


@contextmanager
def slide_export_lock(progress=None, fraction: float = 0.0, label: str = ""):
    """Hold ``SLIDE_EXPORT_LOCK`` for a slide export.

    When another export holds it the wait is reported through ``progress``
    first (``WAITING_FOR_EXPORT``, prefixed with ``label`` when given), so a
    queued job does not look stuck.
    """
    if not SLIDE_EXPORT_LOCK.acquire(blocking=False):
        if progress:
            progress(fraction, f"{label}: {WAITING_FOR_EXPORT}" if label else WAITING_FOR_EXPORT)
        SLIDE_EXPORT_LOCK.acquire()
    try:
        yield
    finally:
        SLIDE_EXPORT_LOCK.release()


# -- opening the inner project --------------------------------------------

def _record(pid: str) -> dict:
    """The outer record of a deck or PDF project. ``ProjectNotFound`` when
    there is no such project, ``ValueError`` for a video project."""
    record = store.get_project(pid)
    if not record:
        raise ProjectNotFound("Project not found.")
    if record.get("kind") not in SLIDE_KINDS:
        raise ValueError("Only deck and PDF projects have slides.")
    return record


def _source(pid: str, record: dict) -> Path:
    return store.PROJECTS_DIR / pid / record["source_filename"]


def _materialise(pid: str, record: dict, source: Path, pm: ProjectManager) -> ProjectManager:
    """Create the inner project from the source file, the way the generate
    path would, but without exporting a deck's images. Only ever called when
    there is no project.json (an unreadable one raised before this)."""
    if record["kind"] == "pdf":
        # The PDF path renders the page images as part of reading the file
        # (there is nothing else to render them from) and creates the project.
        from services.file_item import FileItem

        item = FileItem(source, projects_base=store.PROJECTS_DIR / pid)
        if not item.load_pdf() or item.project_manager is None or item.project_manager.state is None:
            raise ValueError("The PDF could not be read.")
        record_images_source(pid, "pdf")
        return item.project_manager

    from core.pptx_reader import PPTXReader

    reader = PPTXReader(source)
    if not reader.load():
        raise ValueError("The deck could not be read.")
    pm.create_project(
        pptx_path=source,
        slide_notes=[slide.speaker_notes for slide in reader.slides],
        voice_id="",
    )
    return pm


def _reconcile(pid: str, record: dict, pm: ProjectManager) -> None:
    """Keep the outer record's ``slide_count`` and ``engine_project_dir`` in
    step with the inner project (saved onto the record as it is now)."""
    count = len(pm.state.slides)
    inner = pm.project_dir.name
    if record.get("slide_count") == count and record.get("engine_project_dir") == inner:
        return
    current = store.get_project(pid) or record
    current["slide_count"] = count
    current["engine_project_dir"] = inner
    store.save_project(current)
    record.update(current)


def _open(pid: str) -> tuple[dict, ProjectManager]:
    """The outer record and the loaded (or freshly materialised) inner project.
    Callers hold ``project_lock(pid)``. Raises ``ProjectStateError`` for a
    project.json that exists but cannot be read - it is never recreated."""
    record = _record(pid)
    source = _source(pid, record)
    if not source.is_file():
        raise ValueError("The project's source file is missing.")
    pm = ProjectManager(get_project_dir(source, store.PROJECTS_DIR / pid))
    if pm.project_file.exists():
        pm.load()
    if pm.state is None:
        pm = _materialise(pid, record, source, pm)
    _reconcile(pid, record, pm)
    _adopt_images(record, pm)
    return record, pm


def manager(pid: str) -> ProjectManager:
    """The inner project of a deck or PDF project, materialised if absent."""
    with project_lock(pid):
        return _open(pid)[1]


# -- reading ---------------------------------------------------------------

def _deck_info(pid: str, record: dict) -> list:
    """The deck's own slides (``SlideInfo``: notes, title, body text), read
    fresh when the source file changed and cached otherwise. [] for a PDF."""
    if record["kind"] != "deck":
        return []
    source = _source(pid, record)
    stat = source.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _deck_cache.get(pid)
    if cached and cached[0] == key:
        return cached[1]

    from core.pptx_reader import PPTXReader

    reader = PPTXReader(source)
    info = list(reader.slides) if reader.load() else []
    _deck_cache[pid] = (key, info)
    return info


def _image_file(record: dict, pm: ProjectManager, index: int) -> Path:
    return pm.images_dir / _IMAGE_NAME[record["kind"]].format(n=index + 1)


def _images_ready(record: dict, pm: ProjectManager) -> bool:
    slides = pm.state.slides
    return bool(slides) and all(_image_file(record, pm, s.index).is_file() for s in slides)


def _adopt_images(record: dict, pm: ProjectManager) -> None:
    """Point a slide's stored ``image_path`` at the derived file when that file
    exists and the stored path does not (a data directory moved between
    machines), so the generate pipeline's own "images already present" check
    agrees with ``slides_ready`` instead of exporting again. Saves only when
    something changed."""
    changed = False
    for slide in pm.state.slides:
        if slide.image_path and Path(slide.image_path).is_file():
            continue
        derived = _image_file(record, pm, slide.index)
        if derived.is_file() and slide.image_path != str(derived):
            slide.image_path = str(derived)
            changed = True
    if changed:
        pm.save()


def _serialise(pid: str, record: dict, pm: ProjectManager) -> list[dict]:
    info = _deck_info(pid, record)
    out = []
    for slide in pm.state.slides:
        deck = info[slide.index] if slide.index < len(info) else None
        out.append({
            "index": slide.index,
            "speaker_notes": slide.speaker_notes or "",
            "original_notes": deck.speaker_notes if deck else "",
            "title": deck.title if deck else None,
            "body_text": deck.body_text if deck else None,
            "has_image": _image_file(record, pm, slide.index).is_file(),
            "has_audio": bool(slide.audio_path) and Path(slide.audio_path).is_file(),
            "ai_enhanced": bool(slide.ai_enhanced),
            "needs_regeneration": bool(slide.needs_regeneration),
            "voice_override": slide.voice_override or None,
            "pause_override": slide.pause_override,
            "alt_text": slide.alt_text or None,
            "notes_history_depth": len(slide.notes_history or []),
            "has_animation": bool(slide.has_animation),
        })
    return out


def list_slides(pid: str) -> list[dict]:
    """Every slide's state merged with the deck's title / body text and the
    deck's own notes (``original_notes``). No paths, absolute or otherwise."""
    with project_lock(pid):
        record, pm = _open(pid)
        return _serialise(pid, record, pm)


def slides_payload(pid: str) -> dict:
    """What ``GET /slides`` answers: the slides plus where the images came from
    (``powerpoint`` / ``pillow`` / ``pdf``, None = unknown) and whether every
    slide has one."""
    with project_lock(pid):
        record, pm = _open(pid)
        source = record.get("images_source")
        return {
            "slides": _serialise(pid, record, pm),
            "images_source": source if source in IMAGE_SOURCES else None,
            "images_rendered_at": record.get("images_rendered_at"),
            "slides_ready": _images_ready(record, pm),
        }


def images_ready(pid: str) -> bool:
    with project_lock(pid):
        record, pm = _open(pid)
        return _images_ready(record, pm)


# -- editing ----------------------------------------------------------------

def _slide(pm: ProjectManager, index: int):
    count = len(pm.state.slides)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
        raise ValueError(f"No slide with index {index!r}: the project has {count} slides (0 to {count - 1}).")
    return pm.state.slides[index]


def _notes(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Speaker notes must be text.")
    if len(value) > MAX_NOTES_CHARS:
        raise ValueError(f"Speaker notes are limited to {MAX_NOTES_CHARS:,} characters.")
    return value


def _pause(value):
    """A pause override in seconds (0 to 30), or None for the studio default."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("The pause after a slide must be a number of seconds between 0 and 30, or empty for the default.")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise ValueError("The pause after a slide must be a number of seconds between 0 and 30, or empty for the default.") from None
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_PAUSE_SECONDS:
        raise ValueError("The pause after a slide must be a number of seconds between 0 and 30, or empty for the default.")
    return round(seconds, 3)


def _voice(value, provider) -> str | None:
    """A per-slide voice id checked against ``provider`` (None = the studio's
    configured provider) the way generate checks its voice, so an id from the
    other provider is refused now rather than silently dropped at render
    time (``core.tts_provider.effective_voice``). None/"" clears the override."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("The voice override must be a voice id.")
    if not value.strip():
        return None
    return studio_settings.check_voice_for_provider(value, studio_settings.resolve_provider(provider))


def _text_or_none(value, what: str, limit: int = 2000):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{what} must be text.")
    if len(value) > limit:
        raise ValueError(f"{what} is limited to {limit:,} characters.")
    return value.strip() or None


def update_slide(
    pid: str, index: int, *,
    speaker_notes=_UNSET, voice_override=_UNSET, pause_override=_UNSET, alt_text=_UNSET,
    provider: str | None = None,
) -> dict:
    """Change one slide. A keyword left out is left alone; ``None`` clears an
    override (voice, pause, alt text) - notes are text only. Notes go through
    ``update_slide_notes`` (undo history, ``needs_regeneration``). Raises
    ``ValueError`` for a bad index or value; nothing is written then."""
    with project_lock(pid):
        record, pm = _open(pid)
        slide = _slide(pm, index)
        # Validate everything before the first write.
        notes = _notes(speaker_notes) if speaker_notes is not _UNSET and speaker_notes is not None else _UNSET
        voice = _voice(voice_override, provider) if voice_override is not _UNSET else _UNSET
        pause = _pause(pause_override) if pause_override is not _UNSET else _UNSET
        alt = _text_or_none(alt_text, "The alt text") if alt_text is not _UNSET else _UNSET

        if notes is not _UNSET:
            pm.update_slide_notes(index, notes)
        if pause is not _UNSET:
            pm.update_slide_pause(index, pause)
        if voice is not _UNSET and slide.voice_override != voice:
            slide.voice_override = voice
            slide.needs_regeneration = True
            pm.save()
        if alt is not _UNSET and slide.alt_text != alt:
            slide.alt_text = alt
            pm.save()
        return _serialise(pid, record, pm)[index]


def undo_slide(pid: str, index: int) -> dict | None:
    """Restore the notes before the last edit. None when there is nothing to undo."""
    with project_lock(pid):
        record, pm = _open(pid)
        _slide(pm, index)
        if pm.undo_slide_notes(index) is None:
            return None
        return _serialise(pid, record, pm)[index]


def reset_slide(pid: str, index: int) -> dict:
    """Back to the deck's own notes ("" for a PDF); the current text goes onto
    the undo history like any edit."""
    with project_lock(pid):
        record, pm = _open(pid)
        _slide(pm, index)
        info = _deck_info(pid, record)
        original = info[index].speaker_notes if index < len(info) else ""
        pm.update_slide_notes(index, original)
        return _serialise(pid, record, pm)[index]


def bulk_update(pid: str, items: list[dict]) -> list[dict]:
    """Save the notes of several slides (``[{index, speaker_notes}]``) at once.
    Every item is checked before the first write, so a bad one refuses the
    whole batch. Returns every slide afterwards."""
    with project_lock(pid):
        record, pm = _open(pid)
        checked = []
        for item in items:
            index = item.get("index")
            _slide(pm, index)
            checked.append((index, _notes(item.get("speaker_notes"))))
        for index, notes in checked:
            pm.update_slide_notes(index, notes)
        return _serialise(pid, record, pm)


# -- images ------------------------------------------------------------------

def record_images_source(pid: str, source: str) -> None:
    """Note where a project's slide images came from (``powerpoint``,
    ``pillow`` or ``pdf``) and when, onto the outer record as it is now. The
    generate route calls this after a render that exported slides; the
    editor's notice and (later) the vision features read it."""
    if source not in IMAGE_SOURCES:
        raise ValueError(f"Unknown image source '{source}'.")
    current = store.get_project(pid)
    if current is None:
        return
    current["images_source"] = source
    current["images_rendered_at"] = datetime.now(timezone.utc).isoformat()
    store.save_project(current)


def ensure_images(pid: str, progress=None) -> dict:
    """Render every slide's image: a deck through ``PPTXExporter`` (PowerPoint,
    else the title-only Pillow fallback) behind ``SLIDE_EXPORT_LOCK``, a PDF
    through its page renderer. Idempotent: ``{"cached": True}`` when every
    image is already there - checked again once the export lock is held, in
    case another export of the same deck just finished. Records
    ``images_source`` and ``images_rendered_at``. Meant to run in a job."""

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(fraction, message)

    def cached_or_none():
        record, pm = _open(pid)
        if _images_ready(record, pm):
            return {"cached": True, "images_source": record.get("images_source")}
        return None

    with project_lock(pid):
        cached = cached_or_none()
        if cached:
            return cached
        record, pm = _open(pid)
        source = _source(pid, record)
        kind = record["kind"]
        if kind == "pdf":
            # Fast, no COM, and it rewrites project.json (image paths), so it
            # stays under the project lock like any other write.
            report(0.1, "Rendering the PDF pages…")
            from services.file_item import FileItem

            item = FileItem(source, projects_base=store.PROJECTS_DIR / pid)
            if not item.load_pdf():
                raise RuntimeError("The PDF pages could not be rendered.")
            record_images_source(pid, "pdf")
            report(1.0, "Slide previews ready")
            return {"cached": False, "images_source": "pdf", "slides": item.slide_count}

    # The export itself runs outside the project lock - a minute or two for a
    # large deck, and notes saves must not queue behind it - and inside the
    # process-wide export lock, since PowerPoint takes one export at a time.
    with slide_export_lock(progress, 0.05):
        with project_lock(pid):
            cached = cached_or_none()
        if cached:
            return cached
        report(0.1, "Exporting slides…")
        from core.pptx_exporter import PPTXExporter

        exporter = PPTXExporter(source, pm.images_dir)
        exported = exporter.export_slides_as_images(
            progress_callback=lambda done, total: report(
                0.1 + 0.8 * done / max(total, 1), f"Exporting slide {done}/{total}…",
            ),
        )
        images_source = "powerpoint" if exporter.backend == "powerpoint" else "pillow"

    with project_lock(pid):
        # Re-read: notes may have been saved while the export ran.
        record, pm = _open(pid)
        for exp in exported:
            pm.update_slide_image(exp.index, exp.image_path)
            if exp.video_path:
                pm.update_slide_video(exp.index, exp.video_path, exp.has_animation)
        record_images_source(pid, images_source)
    report(1.0, "Slide previews ready")
    return {"cached": False, "images_source": images_source, "slides": len(exported)}


def image_path(pid: str, index: int) -> Path | None:
    """The rendered image of one slide, derived from the project directory and
    the index and proven to lie under the project; None when not rendered or
    out of range."""
    with project_lock(pid):
        record, pm = _open(pid)
        if not isinstance(index, int) or not 0 <= index < len(pm.state.slides):
            return None
        base = (store.PROJECTS_DIR / pid).resolve()
        path = _image_file(record, pm, index).resolve()
        if base not in path.parents or not path.is_file():
            return None
        return path


# -- export -------------------------------------------------------------------

def export_notes_pptx(pid: str) -> Path:
    """A copy of the deck with every slide's notes replaced by the edited ones,
    at ``<project>/exports/<name>-notes.pptx`` (index-aligned, all slides).
    Decks only: a PDF has no deck to write into."""
    with project_lock(pid):
        record, pm = _open(pid)
        if record["kind"] != "deck":
            raise ValueError("Only a deck can be exported as a .pptx with notes.")
        from core.pptx_reader import PPTXReader

        notes = [slide.speaker_notes or "" for slide in pm.state.slides]
        base = (store.PROJECTS_DIR / pid).resolve()
        target = (base / "exports" / f"{record['name']}-notes.pptx").resolve()
        # The name comes from the record: a tampered one must not write outside the project.
        if base not in target.parents:
            raise ValueError("The project name is not a valid file name.")
        return PPTXReader.export_with_updated_notes(_source(pid, record), notes, target)
