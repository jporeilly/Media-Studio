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

**Annotations (T3b).** A still can be annotated in the page's editor. The
annotations are a DOCUMENT - ``annotations`` on the record: a version, a
list of shapes in the still's own pixel coordinates and an optional crop,
validated by ``api.schemas.AnnotationDocument`` - and the annotated picture
is a PNG the page's own renderer exported, ``annotated.png`` beside the
still. The backend stores both and never re-renders: what the page showed
is what is saved. ``still.png`` is never touched; **Revert** deletes the
document and the export. The record also carries ``annotated`` (bool),
``annotated_at`` and ``output_width`` / ``output_height`` (the export's
size: the crop's when there is one, else the still's), so a list can show
a badge and the editor can size itself without opening the picture.

**One oracle for "which picture":** :func:`image_path` answers the
annotated picture when the record says ``annotated`` (and the original
when it says so but the file is gone - logged, never a 500), and the still
with ``original=True``. The image, download and add-to-deck routes all ask
it and nothing else.
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import capture
from utils.config import CONFIG_DIR, TEMP_DIR
from utils.helpers import HEX12_RE, file_under, is_hex12, replace_with_retry
from utils.logger import get_logger

log = get_logger("CAPTURES")

CAPTURES_DIR = CONFIG_DIR / "captures"
STILL_NAME = "still.png"
#: The annotated picture, exported by the page's renderer; beside the still.
ANNOTATED_NAME = "annotated.png"
MAX_NAME_CHARS = 120
#: A capture id: the twelve-hex shape every record folder shares (``utils.helpers.HEX12_RE``).
_CID_RE = HEX12_RE

#: The annotation document's version this release reads and writes.
DOCUMENT_VERSION = 1
#: Bounds on an annotation document (``api.schemas.AnnotationDocument``
#: enforces them): shapes per still, characters of text per shape.
MAX_SHAPES = 500
MAX_TEXT_CHARS = 500
#: The exported PNG: refused with 413 as the body passes it. A bound on
#: abuse: the export of a whole 4K desktop is a few MB as a PNG, and three
#: 4K monitors side by side are still well inside this.
MAX_ANNOTATED_BYTES = 64 * 1024 * 1024
#: The JSON document itself, in the same multipart body (500 shapes with 500
#: characters each is well under this).
MAX_DOCUMENT_BYTES = 1024 * 1024
#: A crop smaller than this a side is a mistake, not a picture.
MIN_CROP_SIDE = 16
#: The picture a save may carry (the crop's size, else the still's): the bound
#: the editor applies before it opens a still (``frontend/src/lib/annotate.ts``
#: MAX_ANNOTATE_PIXELS, the same number), held here too so the decode in
#: ``check_png`` is bounded whoever the client is.
MAX_ANNOTATE_PIXELS = 100_000_000
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class TooLarge(ValueError):
    """The exported picture is over ``MAX_ANNOTATED_BYTES`` (the route answers 413)."""


def _valid(cid: str) -> bool:
    return is_hex12(cid)


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


def _under(cid: str, name: str) -> Path | None:
    """``name`` inside the capture's directory, proven to lie under it and to
    exist; None otherwise (``utils.helpers.file_under``, the idiom the
    walkthroughs share)."""
    return file_under(CAPTURES_DIR / cid, name)


def image_path(cid: str, original: bool = False) -> Path | None:
    """THE oracle for which picture a capture shows: ``annotated.png`` when
    the record says ``annotated``, else ``still.png``; ``original=True`` is
    always the still. A record that claims ``annotated`` while the export is
    missing is answered with the still, and the log says so - never a 500.
    None when the capture, or its still, is not there."""
    rec = get_capture(cid)
    if rec is None:
        return None
    still = _under(cid, STILL_NAME)
    if still is None or original or not rec.get("annotated"):
        return still
    annotated = _under(cid, ANNOTATED_NAME)
    if annotated is None:
        log.warning("Capture %s says it is annotated but %s is missing: serving the original still.", cid, ANNOTATED_NAME)
        return still
    return annotated


def output_size(rec: dict) -> tuple[int, int]:
    """The size of the picture a capture shows: the crop's when its document
    has one, else the still's."""
    crop = ((rec.get("annotations") or {}).get("crop")) if rec.get("annotated") else None
    if crop:
        return int(crop["width"]), int(crop["height"])
    return int(rec["width"]), int(rec["height"])


def public_record(rec: dict, with_document: bool = False) -> dict:
    """The record as the API answers it: every record carries ``annotated``
    and ``output_width`` / ``output_height`` (a record from before
    annotations existed gets the defaults), and the document itself only
    when asked for - the Captures list does not need 500 shapes a still."""
    out = {k: v for k, v in rec.items() if k != "annotations"}
    out["annotated"] = bool(rec.get("annotated"))
    out["output_width"], out["output_height"] = output_size(rec)
    if with_document:
        out["annotations"] = rec.get("annotations") if out["annotated"] else None
    return out


def _write_record(cid: str, record: dict) -> None:
    cdir = CAPTURES_DIR / cid
    cdir.mkdir(parents=True, exist_ok=True)
    tmp = cdir / f"capture.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    replace_with_retry(tmp, _meta_path(cid))


def _wrong_size(width: int, height: int, expected: tuple[int, int]) -> ValueError:
    return ValueError(
        f"The annotated picture is {width}x{height} pixels; it must be {expected[0]}x{expected[1]}"
        " (the crop's size, or the still's)."
    )


def check_png(data: bytes, expected: tuple[int, int]) -> None:
    """``data`` is a real PNG of exactly ``expected`` pixels that decodes, and
    not over the cap. ``ValueError`` (a plain message) otherwise; ``TooLarge``
    over the cap.

    The IHDR's width and height (bytes 16-24, big-endian) are read HERE and
    compared with ``expected`` before Pillow sees the bytes: a header claiming
    a bomb-sized picture is answered by the size comparison, never by Pillow's
    decompression-bomb check, which raises a bare ``Exception`` from
    ``Image.open``. ``verify`` (chunks and CRCs) then ``load`` (the pixels)
    follow for a header that matches, so a PNG whose data cannot be decoded
    is refused at save rather than by the first consumer that draws it; the
    decode is bounded because the caller holds ``expected`` under
    ``MAX_ANNOTATE_PIXELS``. ``DecompressionBombError`` is caught too, as a belt."""
    from PIL import Image, UnidentifiedImageError

    if len(data) > MAX_ANNOTATED_BYTES:
        raise TooLarge(f"The annotated picture may not exceed {MAX_ANNOTATED_BYTES // 1024 ** 2} MB.")
    if not data.startswith(PNG_MAGIC):
        raise ValueError("The annotated picture must be a PNG.")
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise ValueError("The annotated picture is not a valid PNG.")
    width, height = struct.unpack(">II", data[16:24])
    if (width, height) != tuple(expected):
        raise _wrong_size(width, height, expected)
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            fmt, size = img.format, img.size
            img.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValueError("The annotated picture is not a valid PNG.") from None
    if fmt != "PNG":
        raise ValueError("The annotated picture must be a PNG.")
    if size != tuple(expected):
        raise _wrong_size(size[0], size[1], expected)


def save_annotations(cid: str, document: dict, image: bytes) -> dict:
    """Keep an annotation ``document`` (already validated by
    ``api.schemas.AnnotationDocument`` against this still's size) and the
    PNG the page exported for it. The PNG is checked (``check_png``) against
    the document's crop or the still's size BEFORE anything is written, and
    the export is published atomically, then the record. ``LookupError`` for
    no such capture; ``ValueError`` / ``TooLarge`` for a bad picture, with
    nothing written. Returns the public record with the document."""
    rec = get_capture(cid)
    if rec is None or _under(cid, STILL_NAME) is None:
        raise LookupError("No such capture.")
    crop = document.get("crop")
    expected = (int(crop["width"]), int(crop["height"])) if crop else (int(rec["width"]), int(rec["height"]))
    # The same bound the editor applies before it opens a still: past it the picture cannot be held by a
    # canvas, and ``check_png``'s decode below stays bounded.
    if expected[0] * expected[1] > MAX_ANNOTATE_PIXELS:
        raise ValueError(
            f"The picture would be {expected[0]}x{expected[1]} pixels, more than the {MAX_ANNOTATE_PIXELS // 1_000_000} million"
            " the editor can hold; this still cannot be annotated."
        )
    check_png(image, expected)
    cdir = CAPTURES_DIR / cid
    tmp = cdir / f"annotated.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_bytes(image)
    replace_with_retry(tmp, cdir / ANNOTATED_NAME)
    rec["annotations"] = document
    rec["annotated"] = True
    rec["annotated_at"] = datetime.now(timezone.utc).isoformat()
    rec["output_width"], rec["output_height"] = expected
    _write_record(cid, rec)
    return public_record(rec, with_document=True)


def revert_annotations(cid: str) -> bool | None:
    """Throw the annotations away: the export is deleted and the record goes
    back to the still. None for no such capture; True when there was
    something to revert, False when the still was never annotated."""
    rec = get_capture(cid)
    if rec is None:
        return None
    had = bool(rec.get("annotated"))
    annotated = _under(cid, ANNOTATED_NAME)
    if annotated is not None:
        annotated.unlink(missing_ok=True)
    for key in ("annotations", "annotated_at"):
        rec.pop(key, None)
    rec["annotated"] = False
    rec["output_width"], rec["output_height"] = int(rec["width"]), int(rec["height"])
    _write_record(cid, rec)
    return had


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
        # Not annotated yet: the picture shown is the still, at its own size.
        "annotated": False,
        "output_width": width, "output_height": height,
    }
    _write_record(cid, record)
    return record


def delete_capture(cid: str) -> bool:
    if not _valid(cid):
        return False
    d = CAPTURES_DIR / cid
    if not d.is_dir():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True
