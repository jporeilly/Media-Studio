"""The Captures list (T3): stills of the screen the desktop shell takes.

A capture is a directory, ``data/captures/<id>/``, holding ``still.png`` -
the region of the screen as gdigrab copied it, pixel for pixel, at the
screen's physical size, with the pointer when asked - and ``capture.json``:
the id, when, whose (``owner_id`` / ``owner_name``, read by the same rule as
a project's), a name (the window's title, or "Screen capture <date time>"),
the kind (``region`` / ``window`` / ``screen``), the rectangle it came from
and whether the pointer is in it. Ownership is answered where projects'
is: ``api.deps.may_access_project`` works on this record too.

A capture is kept until deleted; **Add to deck** copies it into a deck
project as a new last slide (``services.slides.append_image_slide``) and
leaves the capture where it is.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import capture
from utils.config import CONFIG_DIR, TEMP_DIR
from utils.helpers import replace_with_retry

CAPTURES_DIR = CONFIG_DIR / "captures"
STILL_NAME = "still.png"
MAX_NAME_CHARS = 120
_CID_RE = re.compile(r"^[0-9a-f]{12}$")


def _valid(cid: str) -> bool:
    return bool(_CID_RE.fullmatch(cid or ""))


def default_name(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"Screen capture {now.strftime('%Y-%m-%d %H-%M-%S')}"


def _meta_path(cid: str) -> Path:
    return CAPTURES_DIR / cid / "capture.json"


def get_capture(cid: str) -> dict | None:
    if not _valid(cid):
        return None
    path = _meta_path(cid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_captures() -> list[dict]:
    """Every capture on disk, newest first; the API filters by owner."""
    if not CAPTURES_DIR.is_dir():
        return []
    out = []
    for d in CAPTURES_DIR.iterdir():
        if _valid(d.name):
            rec = get_capture(d.name)
            if rec:
                out.append(rec)
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def image_path(cid: str) -> Path | None:
    """The still of a capture, proven to lie under its directory."""
    rec = get_capture(cid)
    if rec is None:
        return None
    base = (CAPTURES_DIR / cid).resolve()
    path = (base / STILL_NAME).resolve()
    if base not in path.parents or not path.is_file():
        return None
    return path


def take(*, x: int, y: int, width: int, height: int, cursor: bool, kind: str, name: str,
         owner_id: str | None, owner_name: str | None) -> dict:
    """Grab the region now (``services.capture.take_still``) into a new
    capture and return its record. Raises ``ValueError`` for a bad rectangle
    and ``capture.CaptureError`` when the grab fails."""
    grabbed = capture.take_still(x, y, width, height, cursor=cursor, out_dir=TEMP_DIR / "capture")
    cid = uuid.uuid4().hex[:12]
    cdir = CAPTURES_DIR / cid
    cdir.mkdir(parents=True, exist_ok=True)
    still = cdir / STILL_NAME
    shutil.move(str(grabbed), str(still))
    record = {
        "id": cid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner_id": owner_id,
        "owner_name": owner_name,
        "name": (name or "").strip()[:MAX_NAME_CHARS] or default_name(),
        "kind": kind,
        "x": x, "y": y, "width": width, "height": height,
        "cursor": bool(cursor),
        "size_bytes": still.stat().st_size,
    }
    tmp = cdir / f"capture.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    replace_with_retry(tmp, _meta_path(cid))
    return record


def delete_capture(cid: str) -> bool:
    if not _valid(cid):
        return False
    d = CAPTURES_DIR / cid
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True
