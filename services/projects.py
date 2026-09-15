"""Project store: imported decks, PDFs and videos — one directory per project.

A deliberately small filesystem store (``data/projects/<id>/`` holding the
uploaded source file plus ``project.json``). The render/transcribe pipeline
attaches to a project id.

**Ownership** lives on the record (``owner_id`` + the denormalised
``owner_name``), not in SQLite: a project stays "a directory you can zip", and
a database row would rot the moment somebody moved or deleted a directory
by hand. This module only *records* the owner — who is allowed to see or touch
a project is an authorisation question and is answered in one place,
``api.deps.may_access_project`` / ``require_project``, which knows about roles.
``list_projects()`` returns everything on disk; the list endpoint filters it.

**Concurrency.** ``project.json`` is one file with several writers, and more than
one of them is now a read-modify-write rather than a blind overwrite: the
transcript Save harvests the per-sentence adjustments off the stored record
before replacing it (``set_transcript``) and the narration editor changes one
sentence on it (``services.narration``). Two of those at once - which the UI
produces directly, since an offset commits on blur and the Save click follows -
read the same record and the later write wins, losing whatever the other made,
with both requests answering 200 and nothing logged.

So the lock lives HERE, beside the file it guards, and not in whichever feature
module needed it first: ``project_lock(pid)`` is the lock every read-modify-write
of one project's outer ``project.json`` takes, and every future writer of this
record takes it from this module rather than importing one from a sibling that
imports this one back. (``services.slides.project_lock`` is a different lock for
a different file - the engine's INNER project.json.)

``save_project`` is atomic - a temp file and ``os.replace`` - because the same
collision used to tear the file, and ``get_project`` answers None for a record it
cannot parse, so a torn write made a project 404 forever with its files still on
disk. Nothing partial is ever visible at the real path now.
"""

import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS_DIR = ROOT / "data" / "projects"

DECK_SUFFIXES = {".pptx"}
PDF_SUFFIXES = {".pdf"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ALLOWED_SUFFIXES = DECK_SUFFIXES | PDF_SUFFIXES | VIDEO_SUFFIXES

# Project ids are minted as uuid4().hex[:12]. Validate any caller-supplied id
# against that shape BEFORE joining it to a path, so a crafted id (e.g. ".." or
# "%2e%2e") can never escape PROJECTS_DIR (a delete would otherwise rmtree data/).
_PID_RE = re.compile(r"^[0-9a-f]{12}$")

# One lock per project id, guarding the outer project.json (see the module
# docstring). In-process only, like every other lock here: this is the
# single-process edition.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _valid_pid(pid: str) -> bool:
    return bool(_PID_RE.fullmatch(pid or ""))


def project_lock(pid: str) -> threading.Lock:
    """The lock every read-modify-write of one project's OUTER project.json takes.

    Take it around read-then-write, never around a bare ``save_project`` of a
    record you did not just read under it - that would serialise writers without
    making any of them correct.
    """
    with _locks_guard:
        lock = _locks.get(pid)
        if lock is None:
            lock = _locks[pid] = threading.Lock()
        return lock


def forget(pid: str) -> None:
    """Drop the per-project lock (the project was deleted)."""
    with _locks_guard:
        _locks.pop(pid, None)


def kind_for_suffix(suffix: str) -> str | None:
    """Map a file extension to a project kind, or None if unsupported."""
    s = suffix.lower()
    if s in DECK_SUFFIXES:
        return "deck"
    if s in PDF_SUFFIXES:
        return "pdf"
    if s in VIDEO_SUFFIXES:
        return "video"
    return None


def _meta_path(pid: str) -> Path:
    return PROJECTS_DIR / pid / "project.json"


def _slide_count(kind: str, path: Path) -> int | None:
    """Slide count for a deck; None for other kinds or on any read failure."""
    if kind != "deck":
        return None
    try:
        from core.pptx_reader import PPTXReader

        reader = PPTXReader(path)
        # slide_count is a @property on PPTXReader — not a method.
        return reader.slide_count if reader.load() else None
    except Exception:
        return None


def list_projects() -> list[dict]:
    """All project records on disk, newest first (skipping any unreadable
    project.json) — every owner's.

    Filtering by owner is the API's job, not the store's: see
    ``api.deps.may_access_project``, which is the one place that knows an admin
    sees everything and a legacy record with no owner is admin-owned.
    """
    if not PROJECTS_DIR.exists():
        return []
    out: list[dict] = []
    for d in PROJECTS_DIR.iterdir():
        meta = d / "project.json"
        if meta.is_file():
            try:
                out.append(json.loads(meta.read_text(encoding="utf-8")))
            except Exception:
                continue
    out.sort(key=lambda p: p.get("created_at", ""), reverse=True)
    return out


def get_project(pid: str) -> dict | None:
    if not _valid_pid(pid):
        return None
    meta = _meta_path(pid)
    if not meta.is_file():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None


def import_upload(filename: str, data: bytes, *,
                  owner_id: str | None = None, owner_name: str | None = None) -> dict:
    """Create a project from an uploaded file. Raises ValueError on a bad type.

    ``owner_id`` is the importing user's id and ``owner_name`` their display
    name, kept on the record so a project still says who made it after that
    account is gone. Both are omitted only by callers with no user to name (the
    store's own tests); such a record is treated as admin-owned on read.
    """
    name = Path(filename).name or "upload"
    kind = kind_for_suffix(Path(name).suffix)
    if kind is None:
        allowed = ", ".join(sorted(ALLOWED_SUFFIXES))
        raise ValueError(f"Unsupported file type '{Path(name).suffix or name}'. Allowed: {allowed}")

    pid = uuid.uuid4().hex[:12]
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)
    dest = pdir / name
    dest.write_bytes(data)

    record = {
        "id": pid,
        "name": Path(name).stem,
        "kind": kind,
        "source_filename": name,
        "size_bytes": len(data),
        "slide_count": _slide_count(kind, dest),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner_id": owner_id,
        "owner_name": owner_name,
    }
    _meta_path(pid).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


class ProjectDeleteError(RuntimeError):
    """A project could not be fully deleted. It is still listed, so it can be
    retried once whatever held the file has let go."""


def delete_project(pid: str) -> bool:
    """Delete a project and its files. Returns False if it did not exist.

    Raises ``ProjectDeleteError`` when something could not be removed, and
    leaves the project intact and visible rather than half-gone.

    This used to be ``shutil.rmtree(pdir, ignore_errors=True)`` followed by an
    unconditional ``return True``, which is a bad combination on Windows, where
    a file a player or an encoder still has open cannot be unlinked. The tree
    walk would delete ``project.json`` early, fail on the video, swallow the
    error and report success: the project vanished from the list - the list is
    built from ``project.json`` - while its largest file stayed on disk forever,
    invisible and unreferenced. One real case left a 76 MB orphan behind.

    So the record goes LAST. Everything else is removed first and every failure
    is collected; if anything survives, ``project.json`` is untouched, the
    project is still there to try again, and the caller is told. Nothing is
    reported as deleted that is not gone.
    """
    if not _valid_pid(pid):
        return False
    pdir = PROJECTS_DIR / pid
    meta = _meta_path(pid)
    if not pdir.exists():
        return False

    # Under the record's own lock: a transcript Save or a narration adjustment
    # that read the record just before the delete would otherwise write it back
    # after project.json was unlinked, resurrecting a record for files that are
    # gone and failing the rmdir below.
    with project_lock(pid):
        if not pdir.exists():
            return False

        failures: list[str] = []
        for child in sorted(pdir.iterdir()):
            if child == meta:
                continue
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                failures.append(f"{child.name} ({exc.strerror or exc})")

        if failures:
            raise ProjectDeleteError(
                "Could not delete this project: " + ", ".join(failures)
                + ". Something is still using it - close the video if it is open, then try again."
            )

        meta.unlink(missing_ok=True)
        try:
            pdir.rmdir()
        except OSError as exc:
            raise ProjectDeleteError(
                f"Could not remove the project folder: {exc.strerror or exc}"
            ) from exc

    # This project's own lock, and the slide editor's lock plus its cache of the
    # deck's notes: none has a reason to outlive the project. Imported here:
    # that module imports this one.
    from services import slides

    forget(pid)
    slides.forget(pid)
    return True


# The rename in ``save_project``, retried on a transient Windows refusal.
# Worst case ~0.6 s before the save really fails.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF_SECONDS = 0.02


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """``os.replace(tmp, path)``, retried briefly on a Windows sharing refusal.

    On Windows the rename fails with ERROR_ACCESS_DENIED (WinError 5) whenever
    anything else holds a handle to either file for the instant it takes: a
    real-time virus scanner opening the file we have just written (IObit and
    Defender both do - see ``core.audio_mixer._load_audio_with_retry``, which
    exists for the same reason), the search indexer, or a reader that opened
    project.json without FILE_SHARE_DELETE. It is transient and uncommon - 2 in
    60 in a concurrent loop on the development machine - and it is not a reason
    to fail a save: a few milliseconds later it succeeds. POSIX never takes
    this path.

    Retrying exposes nothing partial: the destination is either the old record
    or the new one throughout, and only the rename is repeated.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def save_project(record: dict) -> None:
    """Persist a full project record back to its project.json, ATOMICALLY.

    Written to a uniquely named temp file beside it and moved into place with
    ``os.replace``, which is atomic on both Windows and POSIX. A direct write
    is not: two writers landing together tore the file, and ``get_project``
    answers None for a record it cannot parse - so a half-written project.json
    made the project vanish from the list and 404 on every route, with its
    video still on disk and nothing referencing it. Readers now see either the
    record as it was or the record as it now is, never a fragment. The temp
    name carries a random suffix so two writers cannot share one.

    This makes each write indivisible; it does NOT make a read-then-write
    correct. For that take ``project_lock(pid)`` around both.
    """
    path = _meta_path(record["id"])
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _same_sentence(old, new) -> bool:
    """Whether two transcript entries are the same sentence's window.

    Length alone is not enough: a Save that deletes one sentence and adds
    another keeps the count and would carry every adjustment down a row onto a
    sentence it was never meant for - the silent corruption an index-keyed map
    was rejected for in the first place. The words are what a Save is allowed
    to change, so the WINDOW is the identity: same ``start``, same ``end``.
    """
    return (
        isinstance(old, dict) and isinstance(new, dict)
        and old.get("start") == new.get("start")
        and old.get("end") == new.get("end")
    )


def set_transcript(pid: str, transcript: list[dict]) -> tuple[dict, int] | None:
    """Replace a project's transcript segments, KEEPING their per-sentence
    narration overrides. Returns ``(record, overrides_dropped)``, or None when
    there is no such project.

    The incoming segments are the words only: ``api.schemas.TranscriptSegment``
    forbids the override keys, so a whole-list Save can never move a sentence.
    They are carried across BY INDEX from the stored transcript, and only onto a
    segment that is still the same sentence's window (``_same_sentence``) -
    editing the words never moves the sentences, and changing WHICH sentences
    there are discards their adjustments rather than sliding them onto their
    neighbours. The window rather than the list's length is the test, because
    length is not identity: a save that deletes one sentence and adds another
    keeps the count, and an index-only rule would move every adjustment down a
    row. A sentence still present at its own index and window keeps its
    adjustment however many sentences came or went after it. The count that lost
    one is returned so the caller can say so rather than lose them silently (a
    re-transcribe legitimately discards them; a client that resent the wrong
    list should not do it quietly).

    Read-modify-write - it harvests the overrides off the stored record - so it
    runs under ``project_lock(pid)``, the same lock ``services.narration`` takes
    to change one sentence. Without it a Save landing beside an adjustment
    silently destroyed it, and both requests still answered 200.

    ``services.narration`` owns the vocabulary and is imported here rather than
    at module scope: that module imports this one.
    """
    from services.narration import OVERRIDE_KEYS

    with project_lock(pid):
        record = get_project(pid)
        if record is None:
            return None

        previous = record.get("transcript")
        previous = previous if isinstance(previous, list) else []
        segments = [dict(seg) for seg in transcript]
        dropped = 0
        for i, old in enumerate(previous):
            carried = {k: old[k] for k in OVERRIDE_KEYS if isinstance(old, dict) and k in old}
            if not carried:
                continue
            if i < len(segments) and _same_sentence(old, segments[i]):
                segments[i].update(carried)
            else:
                dropped += 1

        record["transcript"] = segments
        save_project(record)
        return record, dropped
