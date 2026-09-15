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
"""

import json
import re
import shutil
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


def _valid_pid(pid: str) -> bool:
    return bool(_PID_RE.fullmatch(pid or ""))


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

    # The slide editor keeps a per-project lock and a cache of the deck's own
    # notes; neither has a reason to outlive the project. Imported here: that
    # module imports this one.
    from services import slides

    slides.forget(pid)
    return True


def save_project(record: dict) -> None:
    """Persist a full project record back to its project.json."""
    _meta_path(record["id"]).write_text(json.dumps(record, indent=2), encoding="utf-8")


def set_transcript(pid: str, transcript: list[dict]) -> dict | None:
    """Replace a project's transcript segments. Returns the updated record, or None."""
    record = get_project(pid)
    if record is None:
        return None
    record["transcript"] = transcript
    save_project(record)
    return record
