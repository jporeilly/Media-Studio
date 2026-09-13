"""Project store: imported decks, PDFs and videos — one directory per project.

A deliberately small filesystem store (``data/projects/<id>/`` holding the
uploaded source file plus ``project.json``). Single-user for now; when auth grows
users, namespace this under the user id. The render/transcribe pipeline attaches
to a project id.
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
        return reader.slide_count() if reader.load() else None
    except Exception:
        return None


def list_projects() -> list[dict]:
    """All project records, newest first. Skips any unreadable project.json."""
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


def import_upload(filename: str, data: bytes) -> dict:
    """Create a project from an uploaded file. Raises ValueError on a bad type."""
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
    }
    _meta_path(pid).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def delete_project(pid: str) -> bool:
    """Delete a project and its files. Returns False if it did not exist."""
    if not _valid_pid(pid):
        return False
    pdir = PROJECTS_DIR / pid
    if not pdir.exists():
        return False
    shutil.rmtree(pdir, ignore_errors=True)
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
