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
``placement``, ``chapter`` (the name of the chapter this step starts, or ""),
``note`` (W2: the guide's "Under the hood" hint that followed the step, plain
text, for the narrated video to come - the player does not show it) and
``source`` (where the picture came from, informational only: ``{capture_id,
annotated}`` for a still, ``{course, slug, image, alt, launcher}`` for a step
imported from a course workshop - and, since W2c, the two merged once a
capture replaced an imported step's picture: the workshop fields kept,
``image`` null, :func:`captured_source`). A record from before ``instruction``
or ``note`` existed reads as ``""`` for every step (:func:`get_walkthrough`
fills them in).

**An imported step knows where it is in its guide** (W2c): ``anchor``
(``{key, ordinal}``, ``services.workshops.anchor_key``) is set by the import
and kept by every replaced picture and every save (:func:`keep_anchor`: the
record's own anchor for the step's id wins - the builder neither edits nor
sends it); Export to course (``services.course_export``) places the step's
figure by it.

**A step may have no picture yet** (W2): a workshop step the guide showed no
screenshot for imports with ``image`` null and ``width`` / ``height`` 0 and no
hotspot; the builder shows a placeholder and the player a card, and the first
picture it gets - a guided capture in the desktop app, or Replace picture -
goes through :func:`replace_picture` like any other.

**The narration of a step** is ONE rule, :func:`narration_of`: the
instruction when it is not blank, else the text; :func:`transcript_markdown`
writes the whole walkthrough as Markdown from it, byte for byte what the
page's ``transcript`` (``lib/walkthrough.ts``) writes.

**A step's picture is a COPY, taken through ONE copy routine.**
:func:`_copy_file` is the only thing that writes a step's picture: a copy to
a temporary file beside the step's, the PNG header read, the file published
atomically as ``step-<sid>.png``. Two resolvers hand it a source:
:func:`_copy_picture` asks the captures' oracle (``services.captures
.image_path(cid)`` - the annotated picture when there is one), and
:func:`create_from_workshop` takes a guide image already proven under the
courses folder by ``services.workshops``. :func:`add_step` and
:func:`replace_picture` go through the first; the import through the second.
Deleting or re-annotating the capture later never changes the walkthrough;
:func:`replace_picture` copies the capture's current picture again, keeping
the hotspot clamped to the new size (or setting the one it is given). No other
path here reads a capture's files or a course's.

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
#: Characters of a step's note (W2): the guide's "Under the hood" hint kept for the narrated video to come.
MAX_NOTE_CHARS = 4000
#: Characters of a step's informational source fields (W2): a guide image's or a launcher's path, and an image's alt text.
MAX_SOURCE_PATH_CHARS = 400
MAX_ALT_CHARS = 2000
#: W2c, a step's anchor in its guide: characters of its key (the section and the first sentence, normalised), and its
#: ordinal at most - the parse's own bound on a guide's steps (``services.workshops.MAX_PARSE_STEPS``, a test pins it).
MAX_ANCHOR_KEY_CHARS = 400
MAX_ANCHOR_ORDINAL = 500
#: A control character a step's text, instruction or note may not carry - a tab and a line break are allowed, a
#: CR is normalised away: THE one rule, read by ``api.schemas.StepIn`` (the refusal) and by the workshop parser
#: (``services.workshops.plain_text`` and its code lines strip them), so an import never writes what a save refuses.
TEXT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control_characters(text: str) -> str:
    """``text`` without the characters ``TEXT_CONTROL_RE`` names."""
    return TEXT_CONTROL_RE.sub("", text or "")
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
    # A record from before the instruction or the note existed reads as "" for every step.
    for step in rec.get("steps") or []:
        if isinstance(step, dict):
            step.setdefault("instruction", "")
            step.setdefault("note", "")
    return rec


def has_picture(step: dict) -> bool:
    """Whether the step has a picture at all (W2: an imported step may not, yet)."""
    return bool(step.get("image"))


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
    """The record as the list answers it: counts and the id of the first step
    WITH a picture for a thumbnail (an imported step may have none yet), never
    the steps themselves (no texts and no instructions travel in a list)."""
    steps = rec.get("steps") or []
    first = next((s for s in steps if has_picture(s)), None)
    return {
        "id": rec["id"], "name": rec.get("name") or "", "owner_id": rec.get("owner_id"), "owner_name": rec.get("owner_name"),
        "created_at": rec.get("created_at"), "updated_at": rec.get("updated_at"), "revision": int(rec.get("revision") or 1),
        "step_count": len(steps), "chapter_count": chapter_count(steps),
        "first_step_id": first["id"] if first else None,
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
    if found is None or not has_picture(found[1]):
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


def _copy_file(wid: str, sid: str, src: Path, what: str, frame: tuple[int, int] | None = None) -> tuple[str, int, int, Placement | None]:
    """THE one copy routine for a step's picture: ``src`` copied to a
    temporary file beside the step's, its PNG header read, the file published
    atomically as ``step-<sid>.png``. Returns the file's name, its size and,
    when ``frame`` was given and the picture is not that size already, how
    the picture was placed on the frame (W2b: drawn centred on a canvas of the
    frame's size by :func:`_draw_in_frame`, on the same temporary file before
    it is published). ``ValueError`` (naming ``what``: "The capture's
    picture", "The guide's picture x.png") when the file is not a PNG - the
    copy is removed again and nothing is left behind. The caller has already
    proven ``src`` under the folder it belongs to; this reads it and nothing
    else."""
    d = _dir(wid)
    d.mkdir(parents=True, exist_ok=True)
    name = step_image_name(sid)
    tmp = d / f"{name}.{uuid.uuid4().hex[:8]}.tmp"
    shutil.copyfile(src, tmp)
    size = png_size(tmp)
    if size is None:
        tmp.unlink(missing_ok=True)
        raise ValueError(f"{what} is not a PNG; it cannot be a step.")
    placement = None
    if frame is not None and size != frame:
        try:
            placement = place_in_frame(size[0], size[1], frame[0], frame[1])
            _draw_in_frame(tmp, frame, placement)
        except ValueError:  # the still does not fit the frame: nothing is left behind
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:  # a picture Pillow cannot decode: nothing is left behind
            tmp.unlink(missing_ok=True)
            raise ValueError(f"{what} could not be drawn on the frame: {exc}") from None
        size = frame
    replace_with_retry(tmp, d / name)
    return name, size[0], size[1], placement


def _copy_picture(wid: str, sid: str, cid: str, frame: tuple[int, int] | None = None) -> tuple[str, int, int, bool, Placement | None]:
    """Copy the capture's CURRENT picture - the oracle's answer, the annotated
    one when there is one - to ``step-<sid>.png`` through :func:`_copy_file`
    (on ``frame`` when one is given, W2b). Returns the file's name, its size,
    whether the capture was annotated and the placement on the frame, if any.
    ``LookupError`` when the capture or its picture is not there; ``ValueError``
    when the picture is not a PNG (the copy is removed again)."""
    rec = captures.get_capture(cid)
    src = captures.image_path(cid)
    if rec is None or src is None:
        raise LookupError("The capture's image is missing.")
    name, width, height, placement = _copy_file(wid, sid, src, "The capture's picture", frame=frame)
    return name, width, height, bool(rec.get("annotated")), placement


def _new_step(sid: str, image: str, width: int, height: int, cid: str, annotated: bool) -> dict:
    return {
        "id": sid, "image": image, "width": width, "height": height, "hotspot": None,
        "kind": "tooltip", "text": "", "instruction": "", "placement": "auto", "chapter": "", "note": "",
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
        image, width, height, annotated, _ = _copy_picture(wid, sid, capture_id)
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


#: ``replace_picture``'s "keep the step's own hotspot" (the default): a value no caller's hotspot can be.
KEEP_HOTSPOT = object()


def is_workshop_source(source) -> bool:
    """Whether a step's ``source`` says it came from a course workshop (W2: ``{course, slug, …}``)."""
    return isinstance(source, dict) and bool(source.get("course")) and bool(source.get("slug"))


def has_guide_picture(step: dict) -> bool:
    """Whether the step's picture is the guide's own (W2c): imported with the step, never replaced since -
    its workshop ``source`` still names the guide's ``image``. Export to course never writes such a step."""
    source = step.get("source")
    return has_picture(step) and is_workshop_source(source) and bool(source.get("image"))


def captured_source(old, capture_id: str, annotated: bool) -> dict:
    """The ``source`` of a step whose picture a capture has just replaced (W2c, C1): a workshop step KEEPS where
    it came from - its course, slug, alt text and launcher - with ``image`` null (the picture is no longer the
    guide's) and the capture's ``capture_id`` and ``annotated`` merged in; any other step's is the capture's own
    ``{capture_id, annotated}``, as before W2c."""
    capture = {"capture_id": capture_id, "annotated": bool(annotated)}
    if not is_workshop_source(old):
        return capture
    return {"course": old["course"], "slug": old["slug"], "image": None, "alt": old.get("alt") or "",
            "launcher": old.get("launcher"), **capture}

#: W2b, the frame of a guided capture's run (Window kind: the widest width x the tallest height of its
#: stills so far). A smaller still is drawn centred on a TRANSPARENT canvas of the frame's size (alpha 0
#: outside the still, the still itself opaque), so the run's pictures are one size and the player's
#: stage shows through in either theme; a still that does not fit is refused (the page never sends such
#: a frame: a larger still grows the frame first). A side is bounded as a capture's is, and the whole by
#: pixels - the canvas is allocated whole, so the cap is the one the annotation decode already uses.
MAX_FRAME_SIDE = captures.capture.MAX_DIMENSION
MAX_FRAME_PIXELS = captures.MAX_ANNOTATE_PIXELS


def frame_refusal(width: int, height: int) -> str | None:
    """Why a ``width`` x ``height`` frame is refused, or None: a side outside
    1..``MAX_FRAME_SIDE``, or more than ``MAX_FRAME_PIXELS`` pixels in all. Pure."""
    if not (1 <= width <= MAX_FRAME_SIDE and 1 <= height <= MAX_FRAME_SIDE):
        return f"A frame's sides must be 1-{MAX_FRAME_SIDE:,} pixels: {width} x {height} is not."
    if width * height > MAX_FRAME_PIXELS:
        return f"A frame may hold at most {MAX_FRAME_PIXELS:,} pixels: {width} x {height} is {width * height:,}."
    return None


#: Where a still sits on a frame: its top-left at ``dx``, ``dy`` (it is never scaled).
Placement = tuple[int, int]


def place_in_frame(width: int, height: int, frame_width: int, frame_height: int) -> Placement:
    """Where a ``width`` x ``height`` still goes on a ``frame_width`` x
    ``frame_height`` canvas it fits: centred, by whole pixels. ``ValueError``
    when it does not fit (the page sends no such frame). Pure."""
    if width > frame_width or height > frame_height:
        raise ValueError(f"The frame ({frame_width} x {frame_height}) is smaller than the still ({width} x {height}).")
    return (frame_width - width) // 2, (frame_height - height) // 2


def hotspot_in_frame(hotspot: dict | None, placement: Placement | None) -> dict | None:
    """The hotspot given in the still's pixels, moved with the still onto the
    frame (``clamp_hotspot`` then keeps it inside). Pure."""
    if not hotspot or placement is None:
        return hotspot
    dx, dy = placement
    return {"x": dx + int(hotspot["x"]), "y": dy + int(hotspot["y"]), "width": int(hotspot["width"]), "height": int(hotspot["height"])}


def _draw_in_frame(path: Path, frame: tuple[int, int], placement: Placement) -> None:
    """Draw the PNG at ``path`` on a transparent canvas of the frame's size,
    where :func:`place_in_frame` put it, and write the canvas back to ``path``."""
    from PIL import Image  # the one picture-drawing routine of this module; Pillow is a requirement already

    canvas = Image.new("RGBA", frame, (0, 0, 0, 0))
    with Image.open(path) as still:
        picture = still.convert("RGBA")
    canvas.paste(picture, placement)
    canvas.save(path, format="PNG")


def replace_picture(wid: str, sid: str, capture_id: str, hotspot=KEEP_HOTSPOT, frame: tuple[int, int] | None = None) -> dict:
    """Replace a step's picture with the capture's current one (copied again
    through the oracle) - the FIRST picture of a step that had none; the
    hotspot is kept, clamped to the new size, unless ``hotspot`` is given (a
    rectangle, clamped into the new picture, or None for none - the guided
    capture's pointer rule); with ``frame`` (W2b) a smaller picture is drawn
    centred on a transparent canvas of the frame's size and a given hotspot -
    in the still's pixels - moves with it (a picture that does not fit is a
    ``ValueError``, nothing written); the revision +1. ``LookupError`` for no
    such walkthrough or picture, :class:`NoSuchStep` for no such step."""
    with _lock(wid):
        rec = get_walkthrough(wid)
        if rec is None:
            raise LookupError("No such walkthrough.")
        found = find_step(rec, sid)
        if found is None:
            raise NoSuchStep("No such step.")
        index, step = found
        image, width, height, annotated, placement = _copy_picture(wid, sid, capture_id, frame=frame)
        wanted = step.get("hotspot") if hotspot is KEEP_HOTSPOT else hotspot_in_frame(hotspot, placement)
        # The step's anchor stays (``{**step}``); a workshop step keeps where in the guide it came from (W2c).
        step = {**step, "image": image, "width": width, "height": height,
                "hotspot": clamp_hotspot(wanted, width, height),
                "source": captured_source(step.get("source"), capture_id, annotated)}
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


def keep_anchor(step: dict, rec: dict) -> dict:
    """A saved step with its anchor (W2c, C1): the anchor the record holds for the step's id - the server's,
    set by the import; the builder never edits it and does not send it - else the one the document carries,
    else none (no ``anchor`` key, as a record from before W2c reads)."""
    held = next((s.get("anchor") for s in rec.get("steps") or [] if s.get("id") == step.get("id")), None)
    anchor = held or step.get("anchor")
    step.pop("anchor", None)
    if anchor:
        step["anchor"] = dict(anchor)
    return step


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
        steps = [keep_anchor(dict(s), rec) for s in document.get("steps") or []]
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
            if not has_picture(step):
                # A step without a picture yet (an imported one): copied as it is, there is no file.
                steps.append(dict(step))
                continue
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
        if rec.get("source"):
            record["source"] = dict(rec["source"])
        _write_record(new, record)
        return record


# -- the import of a course workshop (W2) ------------------------------------------------------------

def create_from_workshop(name: str, owner_id: str | None, owner_name: str | None, source: dict, steps: list[dict]) -> dict:
    """A new walkthrough built from a parsed workshop (``services.workshops``):
    ``source`` is ``{course, slug, title, version}`` and each of ``steps`` carries
    ``chapter``, ``instruction``, ``text``, ``note``, ``source`` (``{course, slug,
    image, alt, launcher}``) and ``picture`` - the guide image as a ``Path`` already
    proven under the courses folder, or None. Every picture goes through the ONE
    copy routine (:func:`_copy_file`); a step whose picture is missing or not a PNG
    imports WITHOUT a picture (logged, never an error - the desktop app captures
    it later). ``ValueError`` past ``MAX_STEPS`` before anything is written; the
    planned record is validated with the save's own model (``api.schemas
    .WalkthroughIn``, the belt under the parser's rules) before it is written, and
    an invalid plan is a ``ValueError`` naming the field with nothing left behind;
    a failure while copying removes the folder again, so no half-made walkthrough
    is left behind. Revision 1; returns the record."""
    # Lazy: ``api.schemas`` imports this module for its constants, so the import cannot sit at the top.
    from pydantic import ValidationError

    from api.schemas import WalkthroughIn, plain_validation_error

    if len(steps) > MAX_STEPS:
        raise ValueError(f"The workshop has {len(steps)} steps; a walkthrough can hold at most {MAX_STEPS}.")
    wid = new_id()
    now = _now()
    out: list[dict] = []
    try:
        for parsed in steps:
            sid = new_id()
            image, width, height = None, 0, 0
            step_source = dict(parsed.get("source") or {})
            picture = parsed.get("picture")
            if picture is not None:
                try:
                    image, width, height, _ = _copy_file(wid, sid, Path(picture), f"The guide's picture {Path(picture).name}")
                except (OSError, ValueError) as exc:
                    log.warning("Workshop import %s: step %d imports without its picture: %s", source.get("slug"), len(out) + 1, exc)
                    image, width, height = None, 0, 0
            if image is None and "image" in step_source:
                step_source["image"] = None
            made = {
                "id": sid, "image": image, "width": width, "height": height, "hotspot": None,
                "kind": "tooltip", "text": parsed.get("text") or "", "instruction": parsed.get("instruction") or "",
                "placement": "auto", "chapter": parsed.get("chapter") or "", "note": parsed.get("note") or "",
                "source": step_source,
            }
            if parsed.get("anchor"):
                # W2c: where in the guide the step is - kept through every replaced picture and save (C1).
                made["anchor"] = dict(parsed["anchor"])
            out.append(made)
        record = {
            "id": wid, "name": (name or "").strip()[:MAX_NAME_CHARS] or "Walkthrough",
            "owner_id": owner_id, "owner_name": owner_name,
            "created_at": now, "updated_at": now, "revision": 1, "version": DOCUMENT_VERSION,
            "source": dict(source), "steps": out,
        }
        # The belt: what the builder's save would refuse, the import refuses first - against the pictures just copied.
        try:
            WalkthroughIn.model_validate(
                {"version": DOCUMENT_VERSION, "name": record["name"], "revision": 1, "steps": out},
                context={"pictures": held_pictures(wid)},
            )
        except ValidationError as exc:
            raise ValueError(f"The imported walkthrough is not valid: {plain_validation_error(exc)}") from None
        _write_record(wid, record)
    except Exception:
        shutil.rmtree(_dir(wid), ignore_errors=True)
        raise
    return record
