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
import uuid
from datetime import datetime, timezone
from pathlib import Path

from utils.helpers import replace_with_retry
from utils.logger import get_logger

log = get_logger("PROJECTS")

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
        # Only a directory named like a project id is a project. A delete
        # renames the directory out of the id space as its first step (see
        # ``delete_project``), so a renamed directory still holding a
        # project.json must not come back as a listed project - and a stray
        # folder someone dropped in here never was one.
        if not _valid_pid(d.name):
            continue
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


# A delete's FIRST move is to rename the project's directory to this pattern,
# taking it out of the id space ``list_projects`` recognises. Only then are its
# files removed - and if that removal is interrupted, the next delete sweeps
# up what was left.
DELETING_SUFFIX = ".deleting-"


def _files_in_use(pdir: Path) -> list[str]:
    """The names of the files under ``pdir`` that another process holds open,
    so a refused delete can say which one.

    Windows only, and free of side effects: each file is opened with NO
    sharing, which fails with a sharing violation exactly when any other
    handle is open on it - the same condition the delete's rename fails on,
    asked one file at a time. Elsewhere there is no such lock to detect and
    the list is empty.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    invalid = wintypes.HANDLE(-1).value
    generic_read, no_sharing, open_existing, normal = 0x80000000, 0, 3, 0x80
    sharing_violation = 32

    held: list[str] = []
    for path in sorted(p for p in pdir.rglob("*") if p.is_file()):
        handle = kernel32.CreateFileW(str(path), generic_read, no_sharing, None, open_existing, normal, None)
        if handle == invalid:
            if ctypes.get_last_error() == sharing_violation:
                held.append(path.name)
        else:
            kernel32.CloseHandle(handle)
    return held


def _remove_deleted(path: Path) -> bool:
    """Remove a directory a delete has already renamed out of the store. A
    failure here leaves an invisible, unreferenced folder behind rather than a
    half-deleted project, and the sweep on the next delete tries again."""
    try:
        shutil.rmtree(path)
        return True
    except OSError as exc:
        log.warning("A deleted project's files could not all be removed yet (%s: %s); "
                    "the next delete will try again", path.name, exc.strerror or exc)
        return False


def _sweep_deleted() -> None:
    """Finish any earlier delete that renamed its directory out of the store
    but could not remove it. Best effort, run at the start of every delete."""
    if not PROJECTS_DIR.exists():
        return
    for d in PROJECTS_DIR.iterdir():
        if DELETING_SUFFIX in d.name and d.is_dir():
            _remove_deleted(d)


def delete_project(pid: str) -> bool:
    """Delete a project and its files. Returns False if it did not exist.

    Raises ``ProjectDeleteError`` when the project is in use - and then has
    changed NOTHING: the project is still listed, still whole, and can be
    tried again. That is the guarantee the previous version claimed and did
    not have. It removed every file it could before discovering one was
    locked, then raised a 409 saying "nothing was half-removed": a real project
    lost its extracted audio, its re-voiced video and its waveform cache to a
    delete that promised the opposite, and was left listed with a record
    naming files that were gone.

    The first move is now to RENAME the project's directory out of the id
    space (``<pid>.deleting-<random>``). On Windows a directory cannot be
    renamed while any file inside it is open, and the rename is one call that
    either moves the whole project or fails whole - so the files are touched
    only once the directory is provably nobody's. When the rename is refused,
    the files in use are named (``_files_in_use``) so the message says which,
    as it always did.

    After the rename the project is already gone from the store:
    ``list_projects`` skips a directory whose name is not a project id, and
    ``get_project`` looks only at the original path. Removing the renamed
    directory is cleanup, and if it cannot finish (a handle opened between the
    rename and the removal) it is retried by the sweep at the top of the next
    delete rather than reported as a failed delete of a project that is, in
    every way a user can see, gone.
    """
    if not _valid_pid(pid):
        return False
    pdir = PROJECTS_DIR / pid
    if not pdir.exists():
        return False

    _sweep_deleted()

    # Under the record's own lock: a transcript Save or a narration adjustment
    # that read the record just before the delete must not write it back into
    # a directory that is being renamed away.
    with project_lock(pid):
        if not pdir.exists():
            return False
        gone = PROJECTS_DIR / f"{pid}{DELETING_SUFFIX}{uuid.uuid4().hex[:8]}"
        try:
            pdir.rename(gone)
        except OSError as exc:
            held = _files_in_use(pdir)
            what = ", ".join(held) if held else f"{pdir.name} ({exc.strerror or exc})"
            raise ProjectDeleteError(
                f"Could not delete this project: {what}. Something is still using it - "
                "close the video if it is open, then try again."
            ) from exc

    _remove_deleted(gone)

    # This project's own lock, the slide editor's lock plus its cache of the
    # deck's notes, and the narration editor's memoised speaking rates: none has
    # a reason to outlive the project. Imported here: both modules import this
    # one.
    from services import narration, slides

    forget(pid)
    slides.forget(pid)
    narration.forget_baseline(pid)
    return True


# The repo's ONE write-a-temp-then-rename idiom, re-exported so every existing
# caller and every test that patches ``store.replace_with_retry`` keeps working.
# It moved to ``utils.helpers`` when the TTS generators needed it too: they live
# in ``core``, which is the layer UNDERNEATH this one and must not import it.
# ``save_project`` below publishes project.json with it, ``services.narration``
# publishes a finished preview clip onto its cache path with it, and
# ``utils.helpers.publish_to_cache`` publishes the render's clips with it.
__all_reexports__ = ("replace_with_retry",)


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
        replace_with_retry(tmp, path)
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
    from services.narration import OVERRIDE_KEYS, forget_baseline

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

    # The words decide which three sentences the narration timeline's speaking
    # rate was measured from, so a Save makes that measurement stale. Outside
    # the record's lock: it guards a different thing.
    forget_baseline(pid)
    return record, dropped
