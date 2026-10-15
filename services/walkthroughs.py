"""Walkthroughs (W1): an ordered list of steps, each a picture copied from a
still, with a hotspot, a tooltip or modal text, a placement and a chapter.

A walkthrough is a directory, ``data/walkthroughs/<wid>/``, holding
``walkthrough.json`` - the record - and one ``step-<sid>.png`` per step. The
record: ``id``, ``name``, ``owner_id`` / ``owner_name`` (read by the same rule
as a project's: ``api.deps.may_access_project`` works on it), ``created_at``,
``updated_at``, ``revision`` (an integer, +1 on every write), ``version`` and
``steps``. A step: ``id``, ``image`` (its file's name), ``width`` / ``height``
(the picture's), ``hotspot`` (``{x, y, width, height}`` in the picture's
pixels, inside it, at least ``MIN_HOTSPOT_SIDE`` a side, or null), ``kind``
(``tooltip`` or ``modal``), ``text`` (what the viewer READS on the picture),
``instruction`` (what the learner DOES there, as it would be spoken - the
walkthrough's transcript, and later its narration; "" when not written),
``placement``, ``chapter`` (the name of the chapter this step starts, or "")
and ``source`` (``{capture_id, annotated}``: where the picture came from,
informational only). A record from before ``instruction`` existed reads as
``""`` for every step (:func:`get_walkthrough` fills it in).

**The narration of a step** is ONE rule, :func:`narration_of`: the
instruction when it is not blank, else the text; :func:`transcript_markdown`
writes the whole walkthrough as Markdown from it, byte for byte what the
page's ``transcript`` (``lib/walkthrough.ts``) writes.

**A step's picture is a COPY, taken through the oracle.** :func:`add_step`
copies ``services.captures.image_path(cid)`` - the annotated picture when
there is one - into the walkthrough's folder. Deleting or re-annotating the
capture later never changes the walkthrough; :func:`replace_picture` copies
the capture's current picture again, keeping the hotspot clamped to the new
size. No other path here reads a capture's files.

**Writes are whole-document and versioned.** :func:`save` takes the whole
document and the ``revision`` the caller loaded: a stale revision is refused
(:class:`StaleRevision`, the route's 409) and nothing is written. The record
is published atomically, then the pictures no step names any more are
removed. Every file is proven to lie under the walkthrough's folder with the
one idiom every record folder uses (``utils.helpers.file_under``).
"""

from __future__ import annotations

import json
import re
import shutil
import struct
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from services import captures
from utils.config import CONFIG_DIR
from utils.helpers import file_under, is_hex12, replace_with_retry
from utils.logger import get_logger

log = get_logger("WALKTHROUGHS")

WALKTHROUGHS_DIR = CONFIG_DIR / "walkthroughs"
RECORD_NAME = "walkthrough.json"
#: The document's version this release reads and writes.
DOCUMENT_VERSION = 1
#: The limits (``api.schemas.WalkthroughIn`` enforces them on a save; the
#: routes on an add): steps in a walkthrough, characters of a step's text, of
#: a chapter's name, of the walkthrough's name, and a hotspot's smallest side.
MAX_STEPS = 200
MAX_STEP_TEXT = 1000
MAX_CHAPTER_CHARS = 80
MAX_NAME_CHARS = 120
MIN_HOTSPOT_SIDE = 8
#: Characters of a step's instruction: what the learner DOES at the step, as it would be spoken; the transcript reads it.
MAX_INSTRUCTION_CHARS = 2000
#: The JSON body of a save, refused with 413 as it passes (``api.body``): the
#: pictures never travel in it, so 200 steps of 1000 characters are well inside.
MAX_PUT_BYTES = 2 * 1024 * 1024
KINDS = ("tooltip", "modal")
PLACEMENTS = ("auto", "top", "bottom", "left", "right")
STEP_IMAGE_RE = re.compile(r"^step-([0-9a-f]{12})\.png$")
STALE_MESSAGE = "This walkthrough was changed elsewhere: reload it."
PNG_MAGIC = captures.PNG_MAGIC


class StaleRevision(Exception):
    """The caller's revision is not the record's: somebody saved in between."""


class NoSuchStep(LookupError):
    """A step id the walkthrough does not hold."""


# One lock per walkthrough: a save, an added step and a replaced picture each
# read the record, change it and publish it, and two of them interleaved would
# lose one's change (the revision check guards the CALLERS' staleness, not the
# server's own read-modify-write).
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(wid: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(wid)
        if lock is None:
            lock = _locks[wid] = threading.Lock()
        return lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _dir(wid: str) -> Path:
    return WALKTHROUGHS_DIR / wid


def _record_path(wid: str) -> Path:
    return _dir(wid) / RECORD_NAME


def step_image_name(sid: str) -> str:
    return f"step-{sid}.png"


def get_walkthrough(wid: str) -> dict | None:
    if not is_hex12(wid):
        return None
    path = _record_path(wid)
    if not path.is_file():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    # A record from before the instruction existed reads as "" for every step.
    for step in rec.get("steps") or []:
        if isinstance(step, dict):
            step.setdefault("instruction", "")
    return rec


def list_walkthroughs() -> list[dict]:
    """Every walkthrough on disk, newest first by ``updated_at``; the API filters by owner."""
    if not WALKTHROUGHS_DIR.is_dir():
        return []
    out = []
    for d in WALKTHROUGHS_DIR.iterdir():
        if is_hex12(d.name):
            rec = get_walkthrough(d.name)
            if rec:
                out.append(rec)
    out.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
    return out


def chapter_count(steps: list[dict]) -> int:
    """How many steps start a chapter (a non-empty chapter name)."""
    return sum(1 for s in steps if (s.get("chapter") or "").strip())


def narration_of(step: dict) -> str:
    """What is said at a step: the instruction when it is not blank, else the
    text. The ONE rule (the page's ``narrationOf`` is the same); the
    transcript, and later the narrated video, read it."""
    instruction = step.get("instruction") or ""
    return instruction if instruction.strip() else (step.get("text") or "")


def transcript_markdown(record: dict) -> str:
    """The walkthrough as a transcript, in Markdown: the name as the title,
    each chapter as a heading at the step that starts it (the steps before
    any chapter under none), each step a numbered item carrying its
    narration ("(no instruction yet)" when there is none), a step's further
    lines indented under its number. Byte for byte what the page's
    ``transcript`` (``lib/walkthrough.ts``) writes - the two tests name each
    other."""
    name = (record.get("name") or "").strip() or "Walkthrough"
    lines = [f"# {name}", ""]
    steps = record.get("steps") or []
    if not steps:
        lines.append("(no steps yet)")
    for number, step in enumerate(steps, start=1):
        chapter = (step.get("chapter") or "").strip()
        if chapter:
            if lines[-1] != "":
                lines.append("")
            lines += [f"## {chapter}", ""]
        # The same two replaces as the page's ``plainText``: a raw document's CRLF or CR is one line break here too,
        # so the two writers agree byte for byte whatever the record holds (the API stores LF; a hand-made record may not).
        narration = narration_of(step).replace("\r\n", "\n").replace("\r", "\n").strip() or "(no instruction yet)"
        parts = narration.split("\n")
        lines.append(f"{number}. {parts[0]}")
        lines.extend("   " + part for part in parts[1:])
    return "\n".join(lines) + "\n"


def summary(rec: dict) -> dict:
    """The record as the list answers it: counts and the first step's id for a
    thumbnail, never the steps themselves (no texts and no instructions
    travel in a list)."""
    steps = rec.get("steps") or []
    return {
        "id": rec["id"], "name": rec.get("name") or "", "owner_id": rec.get("owner_id"), "owner_name": rec.get("owner_name"),
        "created_at": rec.get("created_at"), "updated_at": rec.get("updated_at"), "revision": int(rec.get("revision") or 1),
        "step_count": len(steps), "chapter_count": chapter_count(steps),
        "first_step_id": steps[0]["id"] if steps else None,
    }


def _write_record(wid: str, record: dict) -> None:
    d = _dir(wid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"walkthrough.{uuid.uuid4().hex[:8]}.tmp"
    tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
    replace_with_retry(tmp, _record_path(wid))


def png_size(path: Path) -> tuple[int, int] | None:
    """A PNG's size from its own header (IHDR, bytes 16-24), or None when the
    file is not a PNG: the one read a picture gets before anything draws it."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or not head.startswith(PNG_MAGIC) or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    if width < 1 or height < 1:
        return None
    return int(width), int(height)


def held_pictures(wid: str) -> dict[str, tuple[int, int]]:
    """The step pictures the walkthrough's folder holds, ``{file name: (width,
    height)}``, read from the files themselves: the validation context a save
    is checked against, so a step can only name a picture that is there, at
    the size it really is."""
    d = _dir(wid)
    if not is_hex12(wid) or not d.is_dir():
        return {}
    out: dict[str, tuple[int, int]] = {}
    for path in d.iterdir():
        if STEP_IMAGE_RE.fullmatch(path.name) and path.is_file():
            size = png_size(path)
            if size:
                out[path.name] = size
    return out


def find_step(rec: dict, sid: str) -> tuple[int, dict] | None:
    for i, step in enumerate(rec.get("steps") or []):
        if step.get("id") == sid:
            return i, step
    return None


def step_image_path(wid: str, sid: str) -> Path | None:
    """The file a step's picture is, proven under the walkthrough's folder;
    None when the walkthrough, the step or the file is not there."""
    rec = get_walkthrough(wid)
    if rec is None or not is_hex12(sid):
        return None
    found = find_step(rec, sid)
    if found is None:
        return None
    return file_under(_dir(wid), str(found[1].get("image") or ""))


def create(name: str, owner_id: str | None, owner_name: str | None) -> dict:
    wid = new_id()
    now = _now()
    record = {
        "id": wid, "name": (name or "").strip()[:MAX_NAME_CHARS] or "Walkthrough",
        "owner_id": owner_id, "owner_name": owner_name,
        "created_at": now, "updated_at": now, "revision": 1, "version": DOCUMENT_VERSION, "steps": [],
    }
    _write_record(wid, record)
    return record


def _copy_picture(wid: str, sid: str, cid: str) -> tuple[str, int, int, bool]:
    """Copy the capture's CURRENT picture - the oracle's answer, the annotated
    one when there is one - to ``step-<sid>.png``, atomically. Returns the
    file's name, its size and whether the capture was annotated. ``LookupError``
    when the capture or its picture is not there; ``ValueError`` when the
    picture is not a PNG (the copy is removed again)."""
    rec = captures.get_capture(cid)
    src = captures.image_path(cid)
    if rec is None or src is None:
        raise LookupError("The capture's image is missing.")
    d = _dir(wid)
    d.mkdir(parents=True, exist_ok=True)
    name = step_image_name(sid)
    tmp = d / f"{name}.{uuid.uuid4().hex[:8]}.tmp"
    shutil.copyfile(src, tmp)
    size = png_size(tmp)
    if size is None:
        tmp.unlink(missing_ok=True)
        raise ValueError("The capture's picture is not a PNG; it cannot be a step.")
    replace_with_retry(tmp, d / name)
    return name, size[0], size[1], bool(rec.get("annotated"))


def _new_step(sid: str, image: str, width: int, height: int, cid: str, annotated: bool) -> dict:
    return {
        "id": sid, "image": image, "width": width, "height": height, "hotspot": None,
        "kind": "tooltip", "text": "", "instruction": "", "placement": "auto", "chapter": "",
        "source": {"capture_id": cid, "annotated": annotated},
    }


def add_step(wid: str, capture_id: str, after: str | None = None) -> tuple[dict, int]:
    """A new step from the capture's picture, inserted after step ``after`` or
    appended, the record's revision +1. Returns the record and the new step's
    position (0-based). ``LookupError`` for no such walkthrough or a missing
    picture, :class:`NoSuchStep` for an ``after`` the walkthrough does not
    hold, ``ValueError`` at ``MAX_STEPS``."""
    with _lock(wid):
        rec = get_walkthrough(wid)
        if rec is None:
            raise LookupError("No such walkthrough.")
        steps = list(rec.get("steps") or [])
        if len(steps) >= MAX_STEPS:
            raise ValueError(f"A walkthrough can hold at most {MAX_STEPS} steps.")
        position = len(steps)
        if after is not None:
            found = find_step(rec, after)
            if found is None:
                raise NoSuchStep("No such step to insert after.")
            position = found[0] + 1
        sid = new_id()
        image, width, height, annotated = _copy_picture(wid, sid, capture_id)
        steps.insert(position, _new_step(sid, image, width, height, capture_id, annotated))
        rec["steps"] = steps
        rec["revision"] = int(rec.get("revision") or 1) + 1
        rec["updated_at"] = _now()
        _write_record(wid, rec)
        return rec, position


def clamp_hotspot(hotspot: dict | None, width: int, height: int) -> dict | None:
    """The hotspot kept inside a ``width`` x ``height`` picture: moved in when
    it hangs over an edge, shrunk when it is larger than the picture, and
    dropped (None) when the picture cannot hold ``MIN_HOTSPOT_SIDE`` a side.
    The frontend's ``clampHotspot`` (``lib/walkthrough.ts``) is the same rule."""
    if not hotspot:
        return None
    if width < MIN_HOTSPOT_SIDE or height < MIN_HOTSPOT_SIDE:
        return None
    w = max(MIN_HOTSPOT_SIDE, min(int(hotspot["width"]), width))
    h = max(MIN_HOTSPOT_SIDE, min(int(hotspot["height"]), height))
    x = max(0, min(int(hotspot["x"]), width - w))
    y = max(0, min(int(hotspot["y"]), height - h))
    return {"x": x, "y": y, "width": w, "height": h}


def replace_picture(wid: str, sid: str, capture_id: str) -> dict:
    """Replace a step's picture with the capture's current one (copied again
    through the oracle); the hotspot is kept, clamped to the new size; the
    revision +1. ``LookupError`` for no such walkthrough or picture,
    :class:`NoSuchStep` for no such step."""
    with _lock(wid):
        rec = get_walkthrough(wid)
        if rec is None:
            raise LookupError("No such walkthrough.")
        found = find_step(rec, sid)
        if found is None:
            raise NoSuchStep("No such step.")
        index, step = found
        image, width, height, annotated = _copy_picture(wid, sid, capture_id)
        step = {**step, "image": image, "width": width, "height": height,
                "hotspot": clamp_hotspot(step.get("hotspot"), width, height),
                "source": {"capture_id": capture_id, "annotated": annotated}}
        rec["steps"][index] = step
        rec["revision"] = int(rec.get("revision") or 1) + 1
        rec["updated_at"] = _now()
        _write_record(wid, rec)
        return rec


def _sweep_pictures(wid: str, keep: set[str]) -> int:
    """Delete the step pictures no step names any more. Returns how many went."""
    gone = 0
    d = _dir(wid)
    if not d.is_dir():
        return 0
    for path in list(d.iterdir()):
        if STEP_IMAGE_RE.fullmatch(path.name) and path.name not in keep and path.is_file():
            try:
                path.unlink()
                gone += 1
            except OSError as exc:
                log.warning("Walkthrough %s: could not remove the dropped picture %s: %s", wid, path.name, exc)
    return gone


def save(wid: str, document: dict, revision: int) -> dict:
    """Replace the whole walkthrough with ``document`` (already validated by
    ``api.schemas.WalkthroughIn`` against the pictures the folder holds):
    the name and the steps, the revision +1. ``revision`` must be the record's
    current one, else :class:`StaleRevision` and nothing is written. The
    record is published first; the pictures no step names are removed after."""
    with _lock(wid):
        rec = get_walkthrough(wid)
        if rec is None:
            raise LookupError("No such walkthrough.")
        current = int(rec.get("revision") or 1)
        if int(revision) != current:
            raise StaleRevision(STALE_MESSAGE)
        steps = [dict(s) for s in document.get("steps") or []]
        rec = {
            **rec,
            "name": (document.get("name") or "").strip()[:MAX_NAME_CHARS] or rec.get("name") or "Walkthrough",
            "steps": steps, "revision": current + 1, "updated_at": _now(), "version": DOCUMENT_VERSION,
        }
        _write_record(wid, rec)
        _sweep_pictures(wid, {s["image"] for s in steps})
        return rec


def delete_walkthrough(wid: str) -> bool:
    """Remove the folder - under the walkthrough's own lock, so a save, an
    add or a replace in flight finishes first and finds nothing to write
    afterwards; the lock leaves the table inside the same critical section,
    so no later request shares an object a saver may still hold."""
    if not is_hex12(wid):
        return False
    with _lock(wid):
        d = _dir(wid)
        if not d.is_dir():
            return False
        shutil.rmtree(d, ignore_errors=True)
        with _locks_guard:
            _locks.pop(wid, None)
        return True


def duplicate(wid: str, owner_id: str | None, owner_name: str | None) -> dict | None:
    """A copy named "<name> (copy)", the pictures copied, the steps as they are
    (their ids kept: they need only be unique within a walkthrough), revision
    1, owned by the caller. None for no such walkthrough."""
    with _lock(wid):
        rec = get_walkthrough(wid)
        if rec is None:
            return None
        new = new_id()
        now = _now()
        steps = []
        for step in rec.get("steps") or []:
            src = file_under(_dir(wid), str(step.get("image") or ""))
            if src is None:
                log.warning("Walkthrough %s: step %s has no picture on disk; it is left out of the copy.", wid, step.get("id"))
                continue
            _dir(new).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, _dir(new) / src.name)
            steps.append(dict(step))
        # "(copy)" is kept whatever the name's length: the base is shortened to make room for it.
        suffix = " (copy)"
        base = (rec.get("name") or "Walkthrough")[:MAX_NAME_CHARS - len(suffix)].rstrip()
        name = f"{base}{suffix}"
        record = {
            "id": new, "name": name, "owner_id": owner_id, "owner_name": owner_name,
            "created_at": now, "updated_at": now, "revision": 1, "version": DOCUMENT_VERSION, "steps": steps,
        }
        _write_record(new, record)
        return record
