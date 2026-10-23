"""Export to course (W2c): a walkthrough imported from a course workshop, written back into that workshop.

Each step with a picture Media Studio took (a capture: never the guide's own picture, which is "already in the
guide") becomes a FIGURE in the workshop's ``guide.md``, right after the step it belongs to, and its picture a
PNG in the course's ``_assets/images``. The figure is the content manager's documented annotated image - the
picture with a numbered pin where the hotspot is, the step's text behind the pin (``AnnotatedImage.tsx``) - which
a Content Manager from 0.7.42 (a Content Editor from 1.30.0) renders as such; an older one shows the picture, the
pin's text as a numbered line and the caption. The studio setting that allows the write still defaults to off: an
export into a published course is an administrator's decision, made once the course's readers have that version.

**Where a figure goes** (C1). An imported step carries its ``anchor`` (``{key, ordinal}``,
``services.workshops.anchor_key``), kept through every replaced picture and save. The export re-parses the
CURRENT guide (:func:`services.workshops.parse_guide`, which gives every step its span: the item's first line to
the last line that is not blank before the next item, launcher, hint, heading, ``<details>``, rule or ``:::``
fence) and places each step by its anchor; a step without one - imported before W2c, or added from a still - or
whose anchor the guide no longer has, is placed by its text: the longest common subsequence of the steps'
first sentences and the guide's, in order (:func:`align`). A step it cannot place is reported, never guessed.

**The figure** (C3, :func:`figure_lines`), between a marker pair, after the step's span::

    <!-- media-studio: walkthrough <wid> step <sid> -->

    <div align="center">
    <figure class="pcm-annotated">
    <img src="../_assets/images/wt-<slug>-<wid>-<sid>.png" alt="<the step's text>" width="<at most 800>">
    <ol class="pcm-annotated__hotspots">
    <li data-x="<x %>" data-y="<y %>"><the step's text></li>
    </ol>
    <figcaption><the step's chapter></figcaption>
    </figure>
    </div>

    <!-- /media-studio -->

- the house figure of the course standard (one tag a line, no blank line inside: all HTML, nothing for Markdown
  to re-read), with the content manager's ``pcm-annotated`` class and ONE hotspot at the hotspot's centre, in
  percent of the picture, one decimal (:func:`centre_percent`, whole-number maths); a step without a hotspot is
  the plain house figure, no ``<ol>``; every text HTML-escaped and on one line;
- the picture: the step's PNG as it is (RGBA, transparent padding and all), copied to
  ``<course>/_assets/images/wt-<slug>-<wid>-<sid>.png`` - the walkthrough's id in the name, so a Duplicate (which
  keeps its original's step ids) never writes over its original's pictures (:func:`image_name`);
- a step of a NESTED list item (indented 1-3 spaces) gets its block indented to the item's content column, so it
  stays inside the item (:func:`item_indent`); a block rewritten in place keeps the indentation it has.

**Re-export is idempotent** (C4): a block between a marker pair of THIS walkthrough is rewritten in place (its
picture too); one whose step no longer has a picture of ours, or is gone, is removed with its picture - unless
another workshop's guide of the course names that picture too: then it is kept and the plan says so
(:func:`used_elsewhere`); other walkthroughs' blocks and pictures and every authored figure are never touched.
Exporting twice writes nothing the second time.

**Writes** (C5) happen only through :func:`write` - behind the studio setting "Allow export into the Courses
folder" (off by default), on the owner's or an administrator's action, with the guide's sha256 from the plan
(the guide changed since = :class:`GuideChanged`, nothing written) - and only through ONE routine,
:func:`write_file`: a temporary file beside the target, published atomically, nothing when the bytes are the
same. Nothing is written outside ``<courses folder>/<course>/``: every folder is proven a real folder under its
parent (no link, no junction: :class:`ExportRefused`), as the import's readers prove theirs; every target is
checked writable before the first byte (:func:`check_writable`), and a failure met part-way rolls back everything
the export did (:class:`_Journal`) and answers :class:`ExportWriteFailed`, naming the file. The guide keeps its
line endings (CRLF stays CRLF: new lines take the file's own) and its bytes everywhere else.

The plan (C2, :func:`compute` + :func:`plan_view`) writes nothing; the zips (C6 :func:`course_zip`, C7
:func:`player_zip`) neither - each is spooled to a temporary file (PNGs stored, the rest deflated) that the route
streams and then removes.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from services import walkthroughs, workshops
from services.studio_settings import courses_export_allowed
from utils.helpers import replace_with_retry
from utils.logger import get_logger

log = get_logger("COURSE_EXPORT")

APP_DIR = Path(__file__).resolve().parent.parent
#: The interactive player, built by ``npm run build`` into one HTML file (``vite build --mode player``): its
#: script and its styles inline, the walkthrough's data in the one slot below, its pictures beside it.
PLAYER_TEMPLATE = APP_DIR / "frontend" / "dist" / "walkthrough-player.html"
PLAYER_DATA_SLOT = '<script type="application/json" id="walkthrough-data">null</script>'
PLAYER_TITLE_SLOT = "<title>Walkthrough</title>"
#: The player's folder: ``<course>/<slug>/walkthrough/`` when written into a workshop, the zip's top folder.
PLAYER_FOLDER = "walkthrough"
#: The widest a figure is shown in the guide: the house figure's own cap (the content manager's COURSE-STANDARD 6).
MAX_FIGURE_WIDTH = 800
IMAGES_FOLDER = ("_assets", "images")
MARKER_CLOSE = "<!-- /media-studio -->"
_MARKER_OPEN = re.compile(r"^<!-- media-studio: walkthrough ([0-9a-f]{12}) (?:step ([0-9a-f]{12})|(player)) -->$")
_IMAGE_REF = re.compile(r'src="\.\./_assets/images/(wt-[A-Za-z0-9._-]+\.png)"')
_PLAYER_STEP = re.compile(r"^step-[0-9a-f]{12}\.png$")
_EOL = re.compile(r"\r\n|\r|\n")
#: The deterministic time a zip's entries carry, so the same export is the same zip.
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

CHANGED_MESSAGE = "The guide changed since the preview: preview again."
NOT_IMPORTED_MESSAGE = "This walkthrough was not imported from a course workshop: only the player can be exported."
EXPORT_OFF_MESSAGE = ("Export into the Courses folder is off: an administrator turns on \"Allow export into the Courses folder\" "
                      "in Settings › Studio. Download the zip instead and copy it into the course by hand.")
PLAYER_NOT_BUILT_MESSAGE = "The player bundle is not built on this server (frontend/dist/walkthrough-player.html): run npm run build."
NOT_FOUND_REASON = "Its text is not in the guide any more (the item was reworded, moved or removed)."
#: A step with no anchor and no workshop source that its text does not place: one added in the builder - or one
#: imported before 0.19.0 whose capture replaced its source and whose item was reworded since.
ADDED_REASON = "Added in the walkthrough, not in the guide (or imported before 0.19.0 and its item reworded since)."
NO_TEXT_REASON = "It has no text to find it by in the guide."
WRITE_FAILED_MESSAGE = "Could not write {path} into the course: {reason}. Nothing was changed: download the zip instead."
NO_PICTURE_REASON = "No picture yet."
GUIDE_PICTURE_REASON = "Its picture is the guide's own: already in the guide."
MISSING_FILE_REASON = "Its picture file is missing from the walkthrough."


class NotImported(ValueError):
    """The walkthrough names no course workshop (it was not imported): there is nothing to place it in."""


class ExportRefused(ValueError):
    """A folder or file the export would write is a link, a junction or not a folder: nothing is written."""


class ExportWriteFailed(ExportRefused):
    """A file of the course could not be written (read-only, locked, a full disk): found before the first byte
    when it can be, else met mid-write and everything this export wrote is rolled back. The message names the file."""


class GuideChanged(RuntimeError):
    """The guide's sha256 is not the one the preview showed: the owner may be editing it right now."""


class PlayerNotBuilt(RuntimeError):
    """``frontend/dist/walkthrough-player.html`` is not there."""


_write_lock = threading.Lock()


# -- the pure rules ------------------------------------------------------------------------------------

def centre_percent(start: int, size: int, whole: int) -> str:
    """Where a span's centre is, in percent of ``whole``, with one decimal: ``(start + size / 2) / whole``,
    rounded half up in whole numbers (no float can tip a .x5), clamped to 0.0-100.0. Pure."""
    if whole <= 0:
        return "0.0"
    numerator = (2 * int(start) + int(size)) * 1000
    denominator = 2 * int(whole)
    tenths = max(0, min(1000, (2 * numerator + denominator) // (2 * denominator)))
    return f"{tenths // 10}.{tenths % 10}"


def one_line(value: str) -> str:
    """A text on one line: every run of white space one space, trimmed (a blank line would end the figure's HTML)."""
    return " ".join((value or "").split())


def image_name(slug: str, wid: str, sid: str) -> str:
    """The picture's file name in the course: ``wt-<slug>-<wid>-<sid>.png`` - unique per walkthrough (a Duplicate keeps
    its original's step ids, so the step id alone would let the copy's export overwrite the original's picture), and
    without the step's number (it changes when steps move; the guide keeps the order)."""
    return f"wt-{slug}-{wid}-{sid}.png"


def marker_open(wid: str, what: str) -> str:
    """The opening marker of a block: ``what`` is ``step <sid>`` or ``player``."""
    return f"<!-- media-studio: walkthrough {wid} {what} -->"


def figure_text(step: dict, number: int) -> str:
    """What the figure says of the step - its alt text and its pin's text: the step's text, else its narration's
    first sentence, else "Step N"; one line."""
    text = one_line(step.get("text") or "") or one_line(workshops.first_sentence(walkthroughs.narration_of(step)))
    return text or f"Step {number}"


def _escape(text: str) -> str:
    """One rule for every text in a figure: HTML-escaped, quotes included (an alt is an attribute)."""
    return html.escape(text, quote=True)


def figure_lines(wid: str, step: dict, number: int, name: str, caption: str) -> list[str]:
    """The block a step's picture becomes in the guide, line by line (no endings): the marker, the house figure
    (annotated when the step has a hotspot) and the closing marker. Pure."""
    sid = step["id"]
    text = _escape(figure_text(step, number))
    width = max(1, min(int(step.get("width") or 0), MAX_FIGURE_WIDTH))
    img = f'<img src="../_assets/images/{name}" alt="{text}" width="{width}">'
    hotspot = step.get("hotspot")
    lines = [marker_open(wid, f"step {sid}"), "", '<div align="center">']
    if hotspot:
        x = centre_percent(hotspot["x"], hotspot["width"], step["width"])
        y = centre_percent(hotspot["y"], hotspot["height"], step["height"])
        lines += ['<figure class="pcm-annotated">', img, '<ol class="pcm-annotated__hotspots">',
                  f'<li data-x="{x}" data-y="{y}">{text}</li>', "</ol>"]
    else:
        lines += ["<figure>", img]
    lines += [f"<figcaption>{_escape(one_line(caption) or f'Step {number}')}</figcaption>", "</figure>", "</div>", "", MARKER_CLOSE]
    return lines


def player_block_lines(wid: str) -> list[str]:
    """The guide's pointer to the interactive player written beside it (C7) - a link until the content manager
    has a launcher that opens it in the guide."""
    line = (f"**Interactive walkthrough:** open [{PLAYER_FOLDER}/index.html]({PLAYER_FOLDER}/index.html), in this "
            "workshop's folder, in a web browser. It runs offline.")
    return [marker_open(wid, "player"), "", line, "", MARKER_CLOSE]


def lead_of(text: str) -> str:
    """A step's first sentence as alignment compares it (``workshops.first_sentence`` through ``key_text``)."""
    return workshops.key_text(workshops.first_sentence(text or ""))


def step_lead(step: dict) -> str:
    """A walkthrough step's lead: its instruction's first sentence, else its text's."""
    return lead_of(step.get("instruction") or "") or lead_of(step.get("text") or "")


def lcs_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """The index pairs of a longest common subsequence of ``a`` and ``b`` (an empty item matches nothing). Pure."""
    n, m = len(a), len(b)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row, below = table[i], table[i + 1]
        for j in range(m - 1, -1, -1):
            row[j] = below[j + 1] + 1 if a[i] and a[i] == b[j] else max(below[j], row[j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if a[i] and a[i] == b[j]:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def align(steps: list[dict], parsed: list[workshops.ParsedStep]) -> tuple[dict[int, int], dict[int, str]]:
    """Which guide step each walkthrough step is (C1): ``({walkthrough index: guide index}, {walkthrough index:
    why not})``. First by anchor - the step's ``{key, ordinal}`` against the guide's; then the rest (no anchor,
    or one the guide no longer has) by their text, in order: the longest common subsequence of their first
    sentences and those of the guide steps nobody took. One guide step for one walkthrough step. Pure."""
    by_anchor = {(p.key, p.ordinal): j for j, p in enumerate(parsed)}
    placed: dict[int, int] = {}
    taken: set[int] = set()
    rest: list[int] = []
    for i, step in enumerate(steps):
        anchor = step.get("anchor") or {}
        j = by_anchor.get((anchor.get("key"), anchor.get("ordinal"))) if anchor else None
        if j is not None and j not in taken:
            placed[i] = j
            taken.add(j)
        else:
            rest.append(i)
    free = [j for j in range(len(parsed)) if j not in taken]
    leads = [step_lead(steps[i]) for i in rest]
    for x, y in lcs_pairs(leads, [workshops.key_text(parsed[j].text) for j in free]):
        placed[rest[x]] = free[y]
    def reason(i: int, lead: str) -> str:
        if not lead:
            return NO_TEXT_REASON
        step = steps[i]
        added = not step.get("anchor") and not walkthroughs.is_workshop_source(step.get("source"))
        return ADDED_REASON if added else NOT_FOUND_REASON

    why = {i: reason(i, lead) for i, lead in zip(rest, leads) if i not in placed}
    return placed, why


def split_lines(text: str) -> list[tuple[str, str]]:
    """The text as ``(line, ending)`` pairs - the ending "\\r\\n", "\\r", "\\n" or "" for the last - so that
    joining them is the text again, byte for byte; line ``i`` is ``workshops.guide_lines(text)[i]``."""
    out, pos = [], 0
    for match in _EOL.finditer(text):
        out.append((text[pos:match.start()], match.group()))
        pos = match.end()
    out.append((text[pos:], ""))
    return out


def file_eol(lines: list[tuple[str, str]]) -> str:
    """The file's own line ending, for the lines an export adds: the commonest one it uses ("\\n" for none)."""
    counts = {"\r\n": 0, "\n": 0, "\r": 0}
    for _line, ending in lines:
        if ending:
            counts[ending] += 1
    best = max(counts, key=lambda e: (counts[e], e == "\n"))
    return best if counts[best] else "\n"


@dataclass
class MarkerBlock:
    wid: str
    sid: str | None  # None for the player's block
    start: int  # the opening marker's line (0-based)
    end: int  # the closing marker's line


def marker_blocks(plain: list[str]) -> list[MarkerBlock]:
    """Every well-formed marker pair in the guide: an opening marker, then its closing one with no other opening
    marker between. A marker without its pair is left alone - never touched, never counted."""
    out: list[MarkerBlock] = []
    i, n = 0, len(plain)
    while i < n:
        match = _MARKER_OPEN.match(plain[i].strip())
        if match:
            j = i + 1
            while j < n and plain[j].strip() != MARKER_CLOSE and not _MARKER_OPEN.match(plain[j].strip()):
                j += 1
            if j < n and plain[j].strip() == MARKER_CLOSE:
                out.append(MarkerBlock(match.group(1), match.group(2), i, j))
                i = j + 1
                continue
        i += 1
    return out


def apply_edits(lines: list[tuple[str, str]], edits: list[tuple[int, int, list[str]]], eol: str) -> list[tuple[str, str]]:
    """The lines with each ``(start, stop, new)`` edit made: lines ``start..stop-1`` replaced by ``new`` (an
    insertion when ``start == stop``, a removal when ``new`` is empty). New lines take ``eol``; a replaced range
    keeps its last line's own ending on its last new line, and an insertion after a last line without one gives
    that line ``eol`` and the inserted block none - so a file's last-line state is kept. The edits never overlap."""
    out: list[tuple[str, str]] = []
    pos = 0
    for start, stop, new in sorted(edits, key=lambda e: (e[0], e[1])):
        if start < pos:
            raise ValueError("Two edits of the guide overlap.")
        out.extend(lines[pos:start])
        if new:
            if stop > start:
                last = lines[stop - 1][1]
            elif out and out[-1][1] == "":
                out[-1] = (out[-1][0], eol)
                last = ""
            else:
                last = eol
            out.extend((line, eol) for line in new[:-1])
            out.append((new[-1], last))
        pos = stop
    out.extend(lines[pos:])
    return out


def player_document(rec: dict) -> dict:
    """The walkthrough as the player bundle carries it: the name and what the player shows of each step - no
    owner, no ids of stills, no notes (the bundle may go into a public course)."""
    keys = ("id", "width", "height", "hotspot", "kind", "text", "instruction", "placement", "chapter")
    steps = []
    for step in rec.get("steps") or []:
        row = {key: step.get(key) for key in keys}
        row["image"] = walkthroughs.step_image_name(step["id"]) if walkthroughs.has_picture(step) else None
        steps.append(row)
    return {"version": walkthroughs.DOCUMENT_VERSION, "name": rec.get("name") or "Walkthrough", "steps": steps}


def player_html(template: str, rec: dict) -> str:
    """The player's page: the template with the walkthrough in its data slot (``<`` escaped, so no text can end
    the script) and the name as the title."""
    if PLAYER_DATA_SLOT not in template:
        raise PlayerNotBuilt(PLAYER_NOT_BUILT_MESSAGE)
    data = json.dumps(player_document(rec), ensure_ascii=False, separators=(",", ":"))
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = template.replace(PLAYER_DATA_SLOT, f'<script type="application/json" id="walkthrough-data">{data}</script>', 1)
    title = html.escape(one_line(rec.get("name") or "") or "Walkthrough")
    return page.replace(PLAYER_TITLE_SLOT, f"<title>{title}</title>", 1)


def player_files(rec: dict) -> dict[str, bytes | Path]:
    """The player bundle's files: ``index.html`` and ``walkthrough.json`` (bytes) and every step's PNG as the
    walkthrough holds it (its path: read only when written or zipped). :class:`PlayerNotBuilt` without the built
    template."""
    if not PLAYER_TEMPLATE.is_file():
        raise PlayerNotBuilt(PLAYER_NOT_BUILT_MESSAGE)
    files: dict[str, bytes | Path] = {
        "index.html": player_html(PLAYER_TEMPLATE.read_text(encoding="utf-8"), rec).encode("utf-8"),
        "walkthrough.json": (json.dumps(player_document(rec), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    for step in rec.get("steps") or []:
        path = walkthroughs.step_image_path(rec["id"], step["id"])
        if path is not None:
            files[path.name] = path
    return files


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str | None:
    try:
        return _sha(path.read_bytes()) if path.is_file() and not workshops._is_link(path) else None
    except OSError:
        return None


# -- the folders ----------------------------------------------------------------------------------------

def own_folder(base: Path, name: str) -> Path:
    """``base/name`` proven a real folder under ``base`` - never a link or a junction - or not there yet (the
    write makes it); :class:`ExportRefused` otherwise."""
    path = base / name
    if workshops._is_link(path):
        raise ExportRefused(f"{name} is a link or a junction: nothing is written through it.")
    if not path.exists():
        return path
    if not path.is_dir():
        raise ExportRefused(f"{name} is not a folder.")
    if base.resolve() not in path.resolve().parents:
        raise ExportRefused(f"{name} is not inside its course.")
    return path


def write_file(folder: Path, name: str, data: bytes) -> bool:
    """THE one routine that writes into a course (C3/C5): ``data`` as ``folder/name`` through a temporary file
    beside it, published atomically (``replace_with_retry``); nothing when the file already holds exactly those
    bytes. The caller has proven ``folder``; a ``name`` that is a link is refused. True when it wrote."""
    dest = folder / name
    if workshops._is_link(dest):
        raise ExportRefused(f"{name} is a link: nothing is written through it.")
    if dest.is_file() and _file_sha(dest) == _sha(data):
        return False
    tmp = folder / f".{name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_bytes(data)
        replace_with_retry(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return True


# -- the plan -------------------------------------------------------------------------------------------

@dataclass
class Plan:
    """What an export makes of a walkthrough and the current guide - computed whole, written by :func:`write`
    only, shown by :func:`plan_view`."""

    rec: dict
    course: dict
    workshop: dict
    course_dir: Path
    guide: Path
    raw: bytes
    new_text: str
    diff: str
    placed: list[dict] = field(default_factory=list)
    not_placed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    new_in_guide: list[dict] = field(default_factory=list)
    #: ``(the step's picture, its name in _assets/images)`` for every placed step.
    images: list[tuple[Path, str]] = field(default_factory=list)
    stale_images: list[str] = field(default_factory=list)
    #: Stale pictures another workshop's guide still names (or might): kept, ``{path, reason}`` (K5, NIT-3).
    kept: list[dict] = field(default_factory=list)
    #: What an interrupted export left, to sweep: ``(folder, name, why)`` (recheck NIT-1).
    debris: list[tuple[Path, str, str]] = field(default_factory=list)
    player: bool = False
    #: The player's files by name: the page and ``walkthrough.json`` as bytes, each step's picture as its path.
    player_files: dict[str, bytes | Path] = field(default_factory=dict)
    stale_player: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.workshop["slug"]

    @property
    def sha256(self) -> str:
        return _sha(self.raw)

    @property
    def new_bytes(self) -> bytes:
        return self.new_text.encode("utf-8")

    @property
    def images_dir(self) -> Path:
        return self.course_dir.joinpath(*IMAGES_FOLDER)

    @property
    def player_dir(self) -> Path:
        return self.guide.parent / PLAYER_FOLDER

    @property
    def figures(self) -> int:
        return len(self.placed)


def compute(rec: dict, player: bool = False) -> Plan:
    """The export of ``rec`` into its workshop's CURRENT guide, computed and nothing written (C2): where each
    step goes, what is skipped and why, the new guide and its pictures. :class:`NotImported` for a walkthrough
    that names no workshop; ``workshops.NoCoursesFolder``, ``LookupError`` and ``workshops.UnreadableCourse`` as
    the import answers them; :class:`PlayerNotBuilt` when the player is asked for without its template."""
    source = rec.get("source") or {}
    if not walkthroughs.is_workshop_source(source):
        raise NotImported(NOT_IMPORTED_MESSAGE)
    root = workshops.courses_root()
    course, workshop, _text, course_dir = workshops.load_workshop(root, str(source["course"]), str(source["slug"]))
    slug = workshop["slug"]
    guide = workshops._own_file(course_dir, f"{slug}/{workshops.GUIDE_FILE}")
    if guide is None:
        raise LookupError("The workshop's guide cannot be read.")
    raw = guide.read_bytes()
    if len(raw) > workshops.MAX_GUIDE_BYTES:
        raise workshops.GuideTooLarge(f"{workshops.GUIDE_FILE} is larger than {workshops.MAX_GUIDE_BYTES // 1024 ** 2} MB.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise workshops.UnreadableCourse(f"{course['id']}/{slug}/{workshops.GUIDE_FILE} cannot be read as UTF-8 text.") from None
    parsed = workshops.parse_guide(text, course["launchers"]).steps
    lines = split_lines(text)
    plain = [line for line, _ending in lines]
    eol = file_eol(lines)
    wid = rec["id"]
    steps = list(rec.get("steps") or [])
    ours = [b for b in marker_blocks(plain) if b.wid == wid]
    block_of: dict[str, MarkerBlock] = {}
    surplus: list[MarkerBlock] = []  # a second block for a step that has one: removed
    for block in ours:
        if block.sid is None:
            continue
        if block.sid in block_of:
            surplus.append(block)
        else:
            block_of[block.sid] = block
    player_block = next((b for b in ours if b.sid is None), None)
    plan = Plan(rec=rec, course=course, workshop=workshop, course_dir=course_dir, guide=guide, raw=raw, new_text=text, diff="",
                player=player)

    # The chapter each step is in (the latest chapter name at or before it): the figure's caption.
    captions, chapter = [], ""
    for step in steps:
        chapter = (step.get("chapter") or "").strip() or chapter
        captions.append(chapter or (rec.get("name") or ""))

    placement, why = align(steps, parsed)
    used_parsed = set(placement.values())
    edits: list[tuple[int, int, list[str]]] = []

    def remove(block: MarkerBlock, reason: str, sid: str | None) -> None:
        start = block.start - 1 if block.start > 0 and not plain[block.start - 1].strip() else block.start
        edits.append((start, block.end + 1, []))
        plan.removed.append({"step_id": sid, "line": block.start + 1, "reason": reason})

    present = {s["id"] for s in steps}
    for number, step in enumerate(steps, start=1):
        sid, index = step["id"], number - 1
        row = {"number": number, "step_id": sid, "text": one_line(step.get("text") or "")}
        block = block_of.get(sid)
        if not walkthroughs.has_picture(step):
            plan.skipped.append({**row, "reason": NO_PICTURE_REASON})
            if block:
                remove(block, "The step has no picture any more.", sid)
            continue
        if walkthroughs.has_guide_picture(step):
            plan.skipped.append({**row, "reason": GUIDE_PICTURE_REASON})
            if block:
                remove(block, "The step's picture is the guide's own now.", sid)
            continue
        picture = walkthroughs.step_image_path(wid, sid)
        if picture is None:
            plan.not_placed.append({**row, "reason": MISSING_FILE_REASON})
            continue
        name = image_name(slug, wid, sid)
        figure = figure_lines(wid, step, number, name, captions[index])
        hotspot = step.get("hotspot")
        spot = ({"x": centre_percent(hotspot["x"], hotspot["width"], step["width"]),
                 "y": centre_percent(hotspot["y"], hotspot["height"], step["height"])} if hotspot else None)
        if block:
            # In place, at the indentation the block already has (a nested item's figure stays in its item).
            new_lines = indented(figure, re.match(r"[ \t]*", plain[block.start]).group())
            old = plain[block.start:block.end + 1]
            edits.append((block.start, block.end + 1, new_lines))
            how, line, sentence = ("unchanged" if old == new_lines else "replaced"), block.start + 1, row["text"]
        elif index in placement:
            guide_step = parsed[placement[index]]
            new_lines = indented(figure, item_indent(plain[guide_step.start_line - 1]))
            edits.append((guide_step.end_line, guide_step.end_line, [""] + new_lines))
            how, line, sentence = "new", guide_step.end_line, guide_step.text
        else:
            plan.not_placed.append({**row, "reason": why.get(index, NOT_FOUND_REASON)})
            continue
        plan.placed.append({**row, "text": sentence, "image": name, "hotspot": spot, "line": line, "how": how})
        plan.images.append((picture, name))

    for sid, block in block_of.items():
        if sid not in present:
            remove(block, "The step is no longer in the walkthrough.", sid)
    for block in surplus:
        remove(block, "A second block for the same step.", block.sid)

    if player:
        plan.player_files = player_files(rec)
        if player_block:
            edits.append((player_block.start, player_block.end + 1, player_block_lines(wid)))
        else:
            last = max((i for i, line in enumerate(plain) if line.strip()), default=-1)
            edits.append((last + 1, last + 1, ([""] if last >= 0 else []) + player_block_lines(wid)))

    new_lines = apply_edits(lines, edits, eol)
    plan.new_text = "".join(line + ending for line, ending in new_lines)
    plan.diff = "\n".join(difflib.unified_diff(plain, [line for line, _e in new_lines], f"a/{slug}/guide.md", f"b/{slug}/guide.md", lineterm=""))
    plan.new_in_guide = [{"text": p.text, "line": p.start_line} for j, p in enumerate(parsed) if j not in used_parsed]

    # The pictures our old blocks named that the new guide no longer does: ours by their name, removed with them -
    # unless another workshop's guide of the course names one too (an author reused it): then it stays, and says so.
    old_names = {m.group(1) for b in ours for line in plain[b.start:b.end + 1] for m in _IMAGE_REF.finditer(line)}
    candidates = sorted(n for n in old_names if n.startswith(f"wt-{slug}-") and f"_assets/images/{n}" not in plan.new_text
                        and (plan.images_dir / n).is_file())
    # Recheck NIT-1: a picture of THIS walkthrough (its name says so) that no block names any more and no guide shows -
    # an interrupted export's, killed before its removal - is swept with the stale ones.
    ours_named = re.compile(rf"^wt-{re.escape(slug)}-{wid}-[0-9a-f]{{12}}\.png$")
    current = {name for _picture, name in plan.images}
    orphans = []
    if plan.images_dir.is_dir() and not workshops._is_link(plan.images_dir):
        orphans = sorted(p.name for p in plan.images_dir.iterdir() if ours_named.fullmatch(p.name) and p.is_file()
                         and p.name not in current and p.name not in candidates and f"_assets/images/{p.name}" not in plan.new_text)
    used = used_elsewhere(course_dir, slug, candidates + orphans)
    plan.stale_images = [n for n in candidates if n not in used]
    plan.kept = [{"path": f"_assets/images/{n}", "reason": used[n]} for n in candidates + orphans if n in used]
    if player and plan.player_dir.is_dir() and not workshops._is_link(plan.player_dir):
        plan.stale_player = sorted(p.name for p in plan.player_dir.iterdir()
                                   if _PLAYER_STEP.fullmatch(p.name) and p.name not in plan.player_files and p.is_file())
    # The debris of an interrupted export in the folders this one writes (the player's only when it is asked for).
    plan.debris = [(plan.guide.parent, name, DEBRIS_REASON) for name in debris_in(plan.guide.parent)]
    plan.debris += [(plan.images_dir, name, DEBRIS_REASON) for name in debris_in(plan.images_dir)]
    plan.debris += [(plan.images_dir, name, ORPHAN_REASON) for name in orphans if name not in used]
    if player:
        plan.debris += [(plan.player_dir, name, DEBRIS_REASON) for name in debris_in(plan.player_dir)]
    return plan


def used_elsewhere(course_dir: Path, slug: str, names: list[str]) -> dict[str, str]:
    """``{name: why it stays}`` for the pictures in ``names`` another workshop of the course may still show: "still used
    by <other>" when ``<course>/<other>/guide.md`` names ``_assets/images/<name>``; "another guide could not be read
    (<other>)" when such a guide is over the cap or not UTF-8 text - it might name it, so the picture is kept (and
    said) rather than removed on a guess. An export never removes a picture another guide uses."""
    if not names:
        return {}
    found: dict[str, str] = {}
    unreadable: str | None = None
    try:
        children = sorted(course_dir.iterdir())
    except OSError:
        return {name: "another guide could not be read (the course folder)" for name in names}
    for child in children:
        if child.name == slug or not workshops.safe_name(child.name) or workshops._is_link(child) or not child.is_dir():
            continue
        guide = workshops._own_file(course_dir, f"{child.name}/{workshops.GUIDE_FILE}")
        if guide is None:
            continue
        try:
            text = workshops._read_text(guide)
        except workshops.GuideTooLarge:
            text = None
        if text is None:
            unreadable = unreadable or child.name
            continue
        for name in names:
            if name not in found and f"_assets/images/{name}" in text:
                found[name] = f"still used by {child.name}"
    if unreadable:
        for name in names:
            found.setdefault(name, f"another guide could not be read ({unreadable})")
    return found


#: What an interrupted export leaves in a folder it writes (a process killed between a write and its commit): the one
#: routine's temporary files, the journal's backups, a write probe - by their names, ``.<file>.<8 hex>.tmp|keep|bak``
#: and ``.ms-write-check-<8 hex>``. Recheck NIT-1: the next export lists and sweeps them.
_DEBRIS = re.compile(r"^\..+\.[0-9a-f]{8}\.(?:tmp|keep|bak)$|^\.ms-write-check-[0-9a-f]{8}$")
DEBRIS_REASON = "left by an interrupted export"
ORPHAN_REASON = "a picture of this walkthrough that no guide names"


def debris_in(folder: Path) -> list[str]:
    """The names in ``folder`` an interrupted export left (:data:`_DEBRIS`); [] for a folder that is not there."""
    try:
        return sorted(p.name for p in folder.iterdir() if _DEBRIS.fullmatch(p.name) and p.is_file() and not workshops._is_link(p))
    except OSError:
        return []


#: A nested ordered item - one indented 1 to 3 spaces - and the column its content starts at (CommonMark: the
#: marker's indent, the marker, then 1 to 4 spaces).
_NESTED_ITEM = re.compile(r"^( {1,3})(\d+[.)])( {1,4})\S")


def item_indent(line: str) -> str:
    """The indentation a figure after this item's lines needs to stay inside the item: the item's content column for
    a nested item (indented 1-3 spaces - its figure at column 0 would end the item and its parent), none for a
    top-level item or a launcher (as the guides' own figures stand between items)."""
    match = _NESTED_ITEM.match(line)
    return " " * (len(match.group(1)) + len(match.group(2)) + len(match.group(3))) if match else ""


def indented(lines: list[str], indent: str) -> list[str]:
    """``lines`` with ``indent`` before every line that is not blank (a blank line stays empty)."""
    return [indent + line if line else line for line in lines] if indent else list(lines)


def file_actions(plan: Plan) -> list[dict]:
    """Every file the export touches, relative to the course folder, with what it does: ``write`` (new or
    different bytes), ``unchanged`` or ``remove``."""
    rows = [{"path": f"{plan.slug}/{workshops.GUIDE_FILE}", "action": "write" if plan.new_bytes != plan.raw else "unchanged"}]
    for picture, name in plan.images:
        rows.append({"path": f"_assets/images/{name}", "action": "write" if _file_sha(plan.images_dir / name) != _file_sha(picture) else "unchanged"})
    rows += [{"path": f"_assets/images/{name}", "action": "remove"} for name in plan.stale_images]
    for name, data in sorted(plan.player_files.items()):
        rows.append({"path": f"{plan.slug}/{PLAYER_FOLDER}/{name}", "action": "write" if _file_sha(plan.player_dir / name) != _content_sha(data) else "unchanged"})
    rows += [{"path": f"{plan.slug}/{PLAYER_FOLDER}/{name}", "action": "remove"} for name in plan.stale_player]
    rows += [{"path": debris_path(plan, folder, name), "action": "remove"} for folder, name, _why in plan.debris]
    return rows


def debris_path(plan: Plan, folder: Path, name: str) -> str:
    """A swept file's path relative to the course folder."""
    return f"{folder.relative_to(plan.course_dir).as_posix()}/{name}"


def _content(data: bytes | Path) -> bytes:
    """A file's content to write: bytes as they are, a path read when it is written (never held for the plan)."""
    return data.read_bytes() if isinstance(data, Path) else data


def _content_sha(data: bytes | Path) -> str | None:
    return _file_sha(data) if isinstance(data, Path) else _sha(data)


def plan_view(plan: Plan) -> dict:
    """The plan as the API answers it (C2): nothing written."""
    files = file_actions(plan)
    return {
        "course": {"id": plan.course["id"], "title": plan.course["title"], "version": plan.course["version"]},
        "workshop": {"slug": plan.slug, "title": plan.workshop.get("title") or plan.slug},
        "guide": f"{plan.course['id']}/{plan.slug}/{workshops.GUIDE_FILE}",
        "guide_sha256": plan.sha256,
        "placed": plan.placed, "not_placed": plan.not_placed, "skipped": plan.skipped, "removed": plan.removed,
        "new_in_guide": plan.new_in_guide, "kept": plan.kept,
        "debris": [{"path": debris_path(plan, folder, name), "reason": why} for folder, name, why in plan.debris],
        "files": files, "changed": any(f["action"] != "unchanged" for f in files),
        "figures": plan.figures, "player": plan.player,
        "diff": plan.diff,
        "allowed": courses_export_allowed(),
    }


# -- the write, guarded (C5) ----------------------------------------------------------------------------

class _Journal:
    """What one export did to the course, so that a failure part-way puts the course back as it was (K3): a file
    it created is removed again, a file it replaced or removed is restored from the backup it kept beside it, a
    folder it made is taken away when empty. :meth:`commit` drops the backups once everything is written."""

    def __init__(self, course_dir: Path, course_id: str):
        self.course_dir, self.course_id = course_dir, course_id
        self.created: list[Path] = []
        self.backups: list[tuple[Path, Path]] = []  # (the file, its backup)
        self.folders: list[Path] = []
        self.current = ""  # the file being written, for the message

    def rel(self, path: Path) -> str:
        return f"{self.course_id}/{path.relative_to(self.course_dir).as_posix()}"

    def _backup(self, path: Path) -> None:
        backup = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.bak")
        replace_with_retry(path, backup)
        self.backups.append((path, backup))

    def folder(self, path: Path) -> None:
        if not path.is_dir():
            self.current = self.rel(path)
            path.mkdir()
            self.folders.append(path)

    def write(self, folder: Path, name: str, data: bytes | Path) -> bool:
        """:func:`write_file`, the one routine, with what it replaces kept to restore."""
        dest = folder / name
        self.current = self.rel(dest)
        if workshops._is_link(dest):
            raise ExportRefused(f"{self.current} is a link: nothing is written through it.")
        content = _content(data)
        if dest.is_file():
            if _file_sha(dest) == _sha(content):
                return False
            keep = dest.with_name(f".{name}.{uuid.uuid4().hex[:8]}.keep")
            shutil.copyfile(dest, keep)
            self.backups.append((dest, keep))
        elif not dest.exists():
            self.created.append(dest)
        return write_file(folder, name, content)

    def remove(self, path: Path) -> None:
        self.current = self.rel(path)
        self._backup(path)

    def commit(self) -> None:
        for _path, backup in self.backups:
            try:
                backup.unlink(missing_ok=True)
            except OSError as exc:  # the export is written; a leftover backup is only untidy
                log.warning("Export to course: could not remove the backup %s: %s", backup, exc)

    def rollback(self) -> None:
        for path in reversed(self.created):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("Export to course: could not take back %s: %s", path, exc)
        for path, backup in reversed(self.backups):
            try:
                replace_with_retry(backup, path)
            except OSError as exc:
                log.warning("Export to course: could not restore %s from %s: %s", path, backup, exc)
        for folder in reversed(self.folders):
            try:
                folder.rmdir()
            except OSError:
                pass


def _probe(folder: Path) -> None:
    """Prove a file can be created in ``folder`` (``os.access`` cannot tell on Windows): one made and removed."""
    probe = folder / f".ms-write-check-{uuid.uuid4().hex[:8]}"
    with open(probe, "xb"):
        pass
    probe.unlink()


def check_writable(plan: Plan, folders: list[Path], files: list[Path], course_id: str) -> None:
    """Before the first byte (K3): every folder the export writes into takes a new file (its nearest existing
    folder, for one still to be made), and every existing file it replaces or removes is writable and not a link.
    :class:`ExportWriteFailed` naming the first that is not."""
    def rel(path: Path) -> str:
        return f"{course_id}/{path.relative_to(plan.course_dir).as_posix()}"

    for folder in folders:
        target = folder
        while not target.exists() and target != plan.course_dir:
            target = target.parent
        try:
            _probe(target)
        except OSError as exc:
            raise ExportWriteFailed(WRITE_FAILED_MESSAGE.format(path=rel(folder), reason=exc.strerror or str(exc))) from None
    for path in files:
        if workshops._is_link(path):
            raise ExportRefused(f"{rel(path)} is a link: nothing is written through it.")
        if path.exists() and not os.access(path, os.W_OK):
            raise ExportWriteFailed(WRITE_FAILED_MESSAGE.format(path=rel(path), reason="it is read-only"))


def write(rec: dict, guide_sha256: str, player: bool = False) -> dict:
    """Write the export into the course: refused with :class:`GuideChanged` (nothing written) when the guide's
    sha256 is not ``guide_sha256``; every folder proven (no link, no junction) and every target checked writable
    before the first byte (:func:`check_writable`); then the pictures, the player (when asked), ``guide.md`` -
    its sha256 checked once more just before it is replaced - and last the pictures of our old blocks no guide
    names any more. Every write through :func:`write_file`, inside the course's folder, kept in a :class:`_Journal`:
    a failure on the way (an ``OSError``, a link met, the guide changed under it) rolls back everything this export
    did and answers :class:`ExportWriteFailed` (or the refusal it met). Returns what was written."""
    with _write_lock:
        plan = compute(rec, player=player)
        if plan.sha256 != guide_sha256:
            raise GuideChanged(CHANGED_MESSAGE)
        course_id = plan.course["id"]
        # Every folder proven first - a link or a junction anywhere refuses the export before a byte is written.
        workshop_dir = own_folder(plan.course_dir, plan.slug)
        if workshop_dir.resolve() != plan.guide.parent.resolve():
            raise ExportRefused("The workshop's folder is not where its guide is.")
        assets_dir = own_folder(plan.course_dir, IMAGES_FOLDER[0])
        images_dir = own_folder(assets_dir, IMAGES_FOLDER[1]) if assets_dir.is_dir() else assets_dir / IMAGES_FOLDER[1]
        player_dir = own_folder(workshop_dir, PLAYER_FOLDER) if plan.player_files else None
        actions = {row["path"]: row["action"] for row in file_actions(plan)}
        targets = [images_dir / name for _picture, name in plan.images if actions.get(f"_assets/images/{name}") == "write"]
        targets += [images_dir / name for name in plan.stale_images]
        if player_dir is not None:
            targets += [player_dir / name for name in plan.player_files if actions.get(f"{plan.slug}/{PLAYER_FOLDER}/{name}") == "write"]
            targets += [player_dir / name for name in plan.stale_player]
        if plan.new_bytes != plan.raw:
            targets.append(plan.guide)
        targets += [folder / name for folder, name, _why in plan.debris]
        # Only the folders something goes into are probed: an export with nothing to write touches nothing.
        folders = [f for f in (workshop_dir, images_dir, player_dir) if f is not None and any(t.parent == f for t in targets)]
        check_writable(plan, folders, targets, course_id)

        journal = _Journal(plan.course_dir, course_id)
        written: list[str] = []
        removed: list[str] = []
        guide_written = False
        try:
            for folder in (assets_dir, images_dir, player_dir):
                if folder is not None:
                    journal.folder(folder)
            for picture, name in plan.images:
                if journal.write(images_dir, name, picture):
                    written.append(f"_assets/images/{name}")
            if player_dir is not None:
                for name, data in sorted(plan.player_files.items()):
                    if journal.write(player_dir, name, data):
                        written.append(f"{plan.slug}/{PLAYER_FOLDER}/{name}")
            if plan.new_bytes != plan.raw:
                # Once more, just before it is replaced: somebody may have saved it while the pictures were written.
                if _file_sha(plan.guide) != guide_sha256:
                    raise GuideChanged(CHANGED_MESSAGE)
                guide_written = journal.write(workshop_dir, workshops.GUIDE_FILE, plan.new_bytes)
                if guide_written:
                    written.append(f"{plan.slug}/{workshops.GUIDE_FILE}")
            for name in plan.stale_images:
                target = images_dir / name
                if target.is_file() and not workshops._is_link(target):
                    journal.remove(target)
                    removed.append(f"_assets/images/{name}")
            if plan.stale_player and player_dir is not None:
                for name in plan.stale_player:
                    target = player_dir / name
                    if target.is_file() and not workshops._is_link(target):
                        journal.remove(target)
                        removed.append(f"{plan.slug}/{PLAYER_FOLDER}/{name}")
            # The debris of an interrupted export: swept through the journal too, so a failure puts it back.
            for folder, name, _why in plan.debris:
                target = folder / name
                if target.is_file() and not workshops._is_link(target):
                    journal.remove(target)
                    removed.append(debris_path(plan, folder, name))
        except (GuideChanged, ExportRefused):
            journal.rollback()
            raise
        except OSError as exc:
            journal.rollback()
            raise ExportWriteFailed(WRITE_FAILED_MESSAGE.format(path=journal.current, reason=exc.strerror or str(exc))) from None
        journal.commit()
    images = sum(1 for path in written if path.startswith("_assets/images/"))
    where = f"{plan.course['id']}/{plan.slug}/{workshops.GUIDE_FILE}"
    n = plan.figures
    if written or removed:
        message = f"Wrote {n} figure{'' if n == 1 else 's'} into {where}. Review and publish it in the Content Editor."
    else:
        message = f"Nothing to write: {where} already holds {n} figure{'' if n == 1 else 's'} of this walkthrough."
    return {
        "course": plan.course["id"], "slug": plan.slug, "guide": where, "figures": n, "images": images,
        "player": bool(plan.player_files), "guide_written": guide_written, "written": written, "removed": removed,
        "guide_sha256": _sha(plan.new_bytes), "message": message,
    }


def audit_detail(result: dict) -> str:
    """The audit row's detail: "<course>/<slug>: N figures, M images" (", player" when it was written)."""
    n, m = result["figures"], result["images"]
    detail = f"{result['course']}/{result['slug']}: {n} figure{'' if n == 1 else 's'}, {m} image{'' if m == 1 else 's'}"
    return detail + (", player" if result["player"] else "")


# -- the zips (C6, C7) ----------------------------------------------------------------------------------

#: Where a zip is spooled before it is sent (None: the system's temporary folder); the route removes it after.
ZIP_TEMP_DIR: Path | None = None


def _zip(entries: dict[str, bytes | Path]) -> Path:
    """A zip of ``entries`` (path in the zip -> bytes, or the file to copy in) written to a temporary file and its
    path returned (K6: never held whole in memory); in path order, every entry dated the same - one export, one zip.
    A PNG is STORED (it is compressed already), any other file deflated. A failure removes the file again."""
    fd, name = tempfile.mkstemp(prefix="ms-export-", suffix=".zip", dir=ZIP_TEMP_DIR)
    os.close(fd)
    path = Path(name)
    try:
        with zipfile.ZipFile(path, "w") as zf:
            for arcname in sorted(entries):
                info = zipfile.ZipInfo(arcname, date_time=_ZIP_TIME)
                info.compress_type = zipfile.ZIP_STORED if arcname.lower().endswith(".png") else zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                data = entries[arcname]
                if isinstance(data, Path):
                    with open(data, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
                else:
                    zf.writestr(info, data)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def course_zip(plan: Plan) -> Path:
    """The export as a zip laid out like the course folder (C6): ``<slug>/guide.md``, ``_assets/images/…``
    (every placed step's picture) and, with the player, ``<slug>/walkthrough/…`` - to copy into the course by
    hand where the server may not write it. The zip's temporary file; the caller removes it."""
    entries: dict[str, bytes | Path] = {f"{plan.slug}/{workshops.GUIDE_FILE}": plan.new_bytes}
    for picture, name in plan.images:
        entries[f"_assets/images/{name}"] = picture
    for name, data in plan.player_files.items():
        entries[f"{plan.slug}/{PLAYER_FOLDER}/{name}"] = data
    return _zip(entries)


def player_zip(rec: dict) -> Path:
    """The interactive player as a zip (C7): ``walkthrough/index.html`` (the player, its script and styles
    inline, the walkthrough in it), ``walkthrough/walkthrough.json`` and the step pictures beside it - opens
    from the disk, no network, no app. Any walkthrough, imported or not. The zip's temporary file; the caller
    removes it."""
    return _zip({f"{PLAYER_FOLDER}/{name}": data for name, data in player_files(rec).items()})
