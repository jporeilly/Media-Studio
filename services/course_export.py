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

**Several workshops** (W2e, E4). A walkthrough imported from several workshops of a course (``source.workshops``)
is planned PER GUIDE: a :class:`GuidePlan` per workshop, in the course's order, under one :class:`Plan` - the steps
grouped by their own ``source.slug`` (a step of no workshop of the record, or - with several - of none at all, is
not placed and says why; nothing is guessed), one guide read, parsed and planned each, the pictures in the course's
one ``_assets/images`` (a picture's name carries its slug), the player beside the FIRST guide with its line in that
guide only. The write takes every guide's sha256 (``{slug: sha256}``; the one sha256 of the body before W2e for a
record of one workshop), checks them all before the first byte (one changed is a 409 naming it), proves every
folder, checks every target writable, and keeps ONE journal - a failure part-way rolls back every guide and picture
written. The zip holds every guide.
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
from typing import Callable

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

CHANGED_MESSAGE = "The guide {guide} changed since the preview: preview again."
#: W2e: a walkthrough of several workshops is written only with EVERY guide's sha256 from the preview.
SHA_PER_GUIDE_MESSAGE = "Every guide's sha256 from the preview is needed for a walkthrough of several workshops ({slug}/guide.md is missing): preview again."
UNKNOWN_GUIDE_MESSAGE = "This walkthrough was not imported from the workshop {slug}: preview again."
#: W2e: a step whose workshop is none of the record's, and - with several workshops - one that names no workshop at all.
FOREIGN_WORKSHOP_REASON = "Its workshop is not one this walkthrough was imported from."
NO_WORKSHOP_REASON = "Added in the walkthrough, not imported: with several workshops it names no guide to go into, and none is guessed."
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
    """A guide's sha256 is not the one the preview showed: the owner may be editing it right now."""


def changed_message(guide: str) -> str:
    """The 409's line, naming the guide ("course/slug/guide.md")."""
    return CHANGED_MESSAGE.format(guide=guide)


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
    # F3, F4: the click kind and the marker travel too - the offline player names the click and draws the beacon.
    keys = ("id", "width", "height", "hotspot", "kind", "text", "instruction", "placement", "chapter", "click", "marker")
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
class GuidePlan:
    """One guide of the export (W2e, E4): the workshop, its guide as it is, the steps of the walkthrough that are
    ITS (by their own ``source.slug``) and what the write does there - computed by :func:`_plan_guide`, one per
    workshop of the record, in the course's order, held together by a :class:`Plan`."""

    workshop: dict
    guide: Path
    raw: bytes
    new_text: str = ""
    diff: str = ""
    placed: list[dict] = field(default_factory=list)
    not_placed: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    new_in_guide: list[dict] = field(default_factory=list)
    #: ``(the step's picture, its name in _assets/images)`` for every placed step of this guide.
    images: list[tuple[Path, str]] = field(default_factory=list)
    stale_images: list[str] = field(default_factory=list)
    #: Stale pictures another workshop's guide still names (or might): kept, ``{path, reason}`` (K5, NIT-3).
    kept: list[dict] = field(default_factory=list)
    #: Pictures of this walkthrough and this workshop no guide names (an interrupted export's): swept (recheck NIT-1).
    orphans: list[str] = field(default_factory=list)
    #: The player's files by name (the FIRST guide's only): the page and ``walkthrough.json`` as bytes, each step's picture as its path.
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
    def changed(self) -> bool:
        """Whether this guide's bytes change."""
        return self.new_bytes != self.raw

    @property
    def player_dir(self) -> Path:
        return self.guide.parent / PLAYER_FOLDER

    @property
    def figures(self) -> int:
        return len(self.placed)

    def where(self, course_id: str) -> str:
        return f"{course_id}/{self.slug}/{workshops.GUIDE_FILE}"


@dataclass
class Plan:
    """What an export makes of a walkthrough and the current guides - computed whole, written by :func:`write` only,
    shown by :func:`plan_view`. Since W2e one :class:`GuidePlan` per workshop of the record (``source.workshops``,
    else ``[source.slug]``), in the course's order; the pictures share the course's one ``_assets/images``; the
    player (when asked) goes beside the FIRST guide. The flat lists (``placed`` and the rest) are the guides' joined,
    and the one-guide fields (``workshop``, ``guide``, ``sha256``) are the first guide's, for any reader before W2e."""

    rec: dict
    course: dict
    course_dir: Path
    guides: list[GuidePlan] = field(default_factory=list)
    #: Steps of no guide (E4): a slug none of the record's workshops, or - with several - no workshop at all.
    homeless_not_placed: list[dict] = field(default_factory=list)
    homeless_skipped: list[dict] = field(default_factory=list)
    #: What an interrupted export left, to sweep: ``(folder, name, why)`` (recheck NIT-1) - every folder this export uses.
    debris: list[tuple[Path, str, str]] = field(default_factory=list)
    player: bool = False

    @property
    def first(self) -> GuidePlan:
        return self.guides[0]

    @property
    def workshop(self) -> dict:
        return self.first.workshop

    @property
    def slug(self) -> str:
        return self.first.slug

    @property
    def slugs(self) -> list[str]:
        return [g.slug for g in self.guides]

    @property
    def guide(self) -> Path:
        return self.first.guide

    @property
    def sha256(self) -> str:
        return self.first.sha256

    @property
    def images_dir(self) -> Path:
        return self.course_dir.joinpath(*IMAGES_FOLDER)

    @property
    def player_dir(self) -> Path:
        return self.first.player_dir

    @property
    def player_files(self) -> dict[str, bytes | Path]:
        return self.first.player_files

    @property
    def stale_player(self) -> list[str]:
        return self.first.stale_player

    @property
    def images(self) -> list[tuple[Path, str]]:
        return [image for g in self.guides for image in g.images]

    @property
    def stale_images(self) -> list[str]:
        return [name for g in self.guides for name in g.stale_images]

    @property
    def placed(self) -> list[dict]:
        return [row for g in self.guides for row in g.placed]

    @property
    def not_placed(self) -> list[dict]:
        return sorted(self.homeless_not_placed + [row for g in self.guides for row in g.not_placed], key=lambda row: row["number"])

    @property
    def skipped(self) -> list[dict]:
        return sorted(self.homeless_skipped + [row for g in self.guides for row in g.skipped], key=lambda row: row["number"])

    @property
    def removed(self) -> list[dict]:
        return [row for g in self.guides for row in g.removed]

    @property
    def new_in_guide(self) -> list[dict]:
        return [row for g in self.guides for row in g.new_in_guide]

    @property
    def kept(self) -> list[dict]:
        return [row for g in self.guides for row in g.kept]

    @property
    def figures(self) -> int:
        return sum(g.figures for g in self.guides)

    @property
    def diff(self) -> str:
        """The guides' diffs, one after the other (a unified diff of several files)."""
        return "\n".join(g.diff for g in self.guides if g.diff)


def source_slugs(source: dict) -> list[str]:
    """The workshops a record was imported from, in the course's order: ``source.workshops`` (W2e), else the one
    ``source.slug`` of a record before it."""
    listed = [str(w["slug"]) for w in source.get("workshops") or [] if isinstance(w, dict) and w.get("slug")]
    return listed or [str(source["slug"])]


def step_slug(step: dict) -> str | None:
    """The workshop a step came from, by its own ``source.slug``; None for a step that names none (added from a still)."""
    source = step.get("source")
    return str(source["slug"]) if walkthroughs.is_workshop_source(source) else None


def compute(rec: dict, player: bool = False) -> Plan:
    """The export of ``rec`` into its workshops' CURRENT guides, computed and nothing written (C2): a
    :class:`GuidePlan` per workshop of the record (E4) - the steps grouped by their own ``source.slug``; with one
    workshop every step is its, as before; with several, a step that names no workshop, or one of a workshop the
    record was not imported from, is not placed and says why - where each step goes, what is skipped and why, the
    new guides and their pictures. :class:`NotImported` for a walkthrough that names no workshop;
    ``workshops.NoCoursesFolder``, ``LookupError`` and ``workshops.UnreadableCourse`` as the import answers them;
    :class:`PlayerNotBuilt` when the player is asked for without its template."""
    source = rec.get("source") or {}
    if not walkthroughs.is_workshop_source(source):
        raise NotImported(NOT_IMPORTED_MESSAGE)
    root = workshops.courses_root()
    slugs = source_slugs(source)
    course, parts, course_dir = workshops.load_workshops(root, str(source["course"]), slugs)
    steps = list(rec.get("steps") or [])
    plan = Plan(rec=rec, course=course, course_dir=course_dir, player=player)

    # The chapter each step is in (the latest chapter name at or before it): the figure's caption.
    captions, chapter = [], ""
    for step in steps:
        chapter = (step.get("chapter") or "").strip() or chapter
        captions.append(chapter or (rec.get("name") or ""))

    # The steps grouped by their workshop (E4): with ONE workshop every step is its (a step added from a still is
    # placed by its text, as before W2e); with several, by the step's own slug - none, or another workshop's, and
    # the step has no guide to go into: nothing is guessed.
    members: dict[str, list[tuple[int, dict]]] = {workshop["slug"]: [] for workshop, _text in parts}
    for index, step in enumerate(steps):
        slug = step_slug(step)
        if slug is None and len(members) == 1:
            slug = next(iter(members))
        if slug in members:
            members[slug].append((index, step))
            continue
        row = {"number": index + 1, "step_id": step["id"], "text": one_line(step.get("text") or "")}
        if not walkthroughs.has_picture(step):
            plan.homeless_skipped.append({**row, "reason": NO_PICTURE_REASON})
        elif walkthroughs.has_guide_picture(step):
            plan.homeless_skipped.append({**row, "reason": GUIDE_PICTURE_REASON})
        else:
            plan.homeless_not_placed.append({**row, "reason": FOREIGN_WORKSHOP_REASON if slug else NO_WORKSHOP_REASON})

    for position, (workshop, _text) in enumerate(parts):
        plan.guides.append(_plan_guide(plan, workshop, members[workshop["slug"]], captions, player and position == 0))

    # The debris of an interrupted export in the folders this one writes (the player's only when it is asked for).
    for g in plan.guides:
        plan.debris += [(g.guide.parent, name, DEBRIS_REASON) for name in debris_in(g.guide.parent)]
    plan.debris += [(plan.images_dir, name, DEBRIS_REASON) for name in debris_in(plan.images_dir)]
    plan.debris += [(plan.images_dir, name, ORPHAN_REASON) for g in plan.guides for name in g.orphans]
    if player:
        plan.debris += [(plan.player_dir, name, DEBRIS_REASON) for name in debris_in(plan.player_dir)]
    return plan


def _plan_guide(plan: Plan, workshop: dict, members: list[tuple[int, dict]], captions: list[str], player: bool) -> GuidePlan:
    """One guide's plan (E4): the workshop's CURRENT guide re-parsed, ``members`` - ``(index in the walkthrough,
    step)`` for the steps that are this workshop's - placed by anchor, else by text (:func:`align`), each figure
    numbered as the step is in the whole walkthrough; the blocks of ours in this guide rewritten, removed or left;
    the player's block (when ``player``: the FIRST guide only) at the end."""
    rec, course, course_dir = plan.rec, plan.course, plan.course_dir
    wid = rec["id"]
    slug = workshop["slug"]
    guide = workshops._own_file(course_dir, f"{slug}/{workshops.GUIDE_FILE}")
    if guide is None:
        raise LookupError(f"The workshop's guide cannot be read: {slug}.")
    raw = guide.read_bytes()
    if len(raw) > workshops.MAX_GUIDE_BYTES:
        raise workshops.GuideTooLarge(f"{slug}/{workshops.GUIDE_FILE} is larger than {workshops.MAX_GUIDE_BYTES // 1024 ** 2} MB.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise workshops.UnreadableCourse(f"{course['id']}/{slug}/{workshops.GUIDE_FILE} cannot be read as UTF-8 text.") from None
    parsed = workshops.parse_guide(text, course["launchers"]).steps
    lines = split_lines(text)
    plain = [line for line, _ending in lines]
    eol = file_eol(lines)
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
    g = GuidePlan(workshop=workshop, guide=guide, raw=raw, new_text=text)

    local_steps = [step for _index, step in members]
    placement, why = align(local_steps, parsed)
    used_parsed = set(placement.values())
    edits: list[tuple[int, int, list[str]]] = []

    def remove(block: MarkerBlock, reason: str, sid: str | None) -> None:
        start = block.start - 1 if block.start > 0 and not plain[block.start - 1].strip() else block.start
        edits.append((start, block.end + 1, []))
        g.removed.append({"step_id": sid, "line": block.start + 1, "reason": reason})

    present = {step["id"] for step in local_steps}
    for local, (index, step) in enumerate(members):
        number = index + 1
        sid = step["id"]
        row = {"number": number, "step_id": sid, "text": one_line(step.get("text") or "")}
        block = block_of.get(sid)
        if not walkthroughs.has_picture(step):
            g.skipped.append({**row, "reason": NO_PICTURE_REASON})
            if block:
                remove(block, "The step has no picture any more.", sid)
            continue
        if walkthroughs.has_guide_picture(step):
            g.skipped.append({**row, "reason": GUIDE_PICTURE_REASON})
            if block:
                remove(block, "The step's picture is the guide's own now.", sid)
            continue
        picture = walkthroughs.step_image_path(wid, sid)
        if picture is None:
            g.not_placed.append({**row, "reason": MISSING_FILE_REASON})
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
        elif local in placement:
            guide_step = parsed[placement[local]]
            new_lines = indented(figure, item_indent(plain[guide_step.start_line - 1]))
            edits.append((guide_step.end_line, guide_step.end_line, [""] + new_lines))
            how, line, sentence = "new", guide_step.end_line, guide_step.text
        else:
            g.not_placed.append({**row, "reason": why.get(local, NOT_FOUND_REASON)})
            continue
        g.placed.append({**row, "text": sentence, "image": name, "hotspot": spot, "line": line, "how": how})
        g.images.append((picture, name))

    for sid, block in block_of.items():
        if sid not in present:
            remove(block, "The step is no longer in the walkthrough.", sid)
    for block in surplus:
        remove(block, "A second block for the same step.", block.sid)

    if player:
        g.player_files = player_files(rec)
        if player_block:
            edits.append((player_block.start, player_block.end + 1, player_block_lines(wid)))
        else:
            last = max((i for i, line in enumerate(plain) if line.strip()), default=-1)
            edits.append((last + 1, last + 1, ([""] if last >= 0 else []) + player_block_lines(wid)))

    new_lines = apply_edits(lines, edits, eol)
    g.new_text = "".join(line + ending for line, ending in new_lines)
    g.diff = "\n".join(difflib.unified_diff(plain, [line for line, _e in new_lines], f"a/{slug}/guide.md", f"b/{slug}/guide.md", lineterm=""))
    g.new_in_guide = [{"text": p.text, "line": p.start_line} for j, p in enumerate(parsed) if j not in used_parsed]

    # The pictures our old blocks named that the new guide no longer does: ours by their name, removed with them -
    # unless another workshop's guide of the course names one too (an author reused it - one of this walkthrough's
    # OWN other guides among them): then it stays, and says so.
    images_dir = plan.images_dir
    old_names = {m.group(1) for b in ours for line in plain[b.start:b.end + 1] for m in _IMAGE_REF.finditer(line)}
    candidates = sorted(n for n in old_names if n.startswith(f"wt-{slug}-") and f"_assets/images/{n}" not in g.new_text
                        and (images_dir / n).is_file())
    # Recheck NIT-1: a picture of THIS walkthrough and workshop (its name says so) that no block names any more and no
    # guide shows - an interrupted export's, killed before its removal - is swept with the stale ones.
    ours_named = re.compile(rf"^wt-{re.escape(slug)}-{wid}-[0-9a-f]{{12}}\.png$")
    current = {name for _picture, name in g.images}
    orphans = []
    if images_dir.is_dir() and not workshops._is_link(images_dir):
        orphans = sorted(p.name for p in images_dir.iterdir() if ours_named.fullmatch(p.name) and p.is_file()
                         and p.name not in current and p.name not in candidates and f"_assets/images/{p.name}" not in g.new_text)
    used = used_elsewhere(course_dir, slug, candidates + orphans)
    g.stale_images = [n for n in candidates if n not in used]
    g.kept = [{"path": f"_assets/images/{n}", "reason": used[n]} for n in candidates + orphans if n in used]
    g.orphans = [n for n in orphans if n not in used]
    if player and g.player_dir.is_dir() and not workshops._is_link(g.player_dir):
        g.stale_player = sorted(p.name for p in g.player_dir.iterdir()
                                if _PLAYER_STEP.fullmatch(p.name) and p.name not in g.player_files and p.is_file())
    return g


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
    different bytes), ``unchanged`` or ``remove`` - every guide's ``guide.md`` first, then the pictures (one folder
    for the course), the player's files (beside the first guide), and what is swept."""
    rows = [{"path": f"{g.slug}/{workshops.GUIDE_FILE}", "action": "write" if g.changed else "unchanged"} for g in plan.guides]
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


def guide_view(plan: Plan, g: GuidePlan) -> dict:
    """One guide's block of the plan (E4): its own line's counts, lists and diff."""
    return {
        "slug": g.slug, "title": g.workshop.get("title") or g.slug, "guide": g.where(plan.course["id"]), "guide_sha256": g.sha256,
        "placed": g.placed, "not_placed": g.not_placed, "skipped": g.skipped, "removed": g.removed,
        "new_in_guide": g.new_in_guide, "kept": g.kept, "figures": g.figures, "changed": g.changed, "diff": g.diff,
    }


def plan_view(plan: Plan) -> dict:
    """The plan as the API answers it (C2): nothing written. Since W2e ``guides`` holds a block per guide
    (:func:`guide_view`); the flat lists are the guides' joined (the steps of no guide among ``not_placed`` and
    ``skipped``) and ``workshop``, ``guide`` and ``guide_sha256`` are the first guide's, for a page before W2e."""
    files = file_actions(plan)
    return {
        "course": {"id": plan.course["id"], "title": plan.course["title"], "version": plan.course["version"]},
        "workshop": {"slug": plan.slug, "title": plan.workshop.get("title") or plan.slug},
        "guide": plan.first.where(plan.course["id"]),
        "guide_sha256": plan.sha256,
        "guides": [guide_view(plan, g) for g in plan.guides],
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


def check_writable(course_dir: Path, folders: list[Path], files: list[Path], course_id: str, *, failed_message: str = WRITE_FAILED_MESSAGE) -> None:
    """Before the first byte (K3): every folder the export writes into takes a new file (its nearest existing
    folder, for one still to be made), and every existing file it replaces or removes is writable and not a link.
    :class:`ExportWriteFailed` naming the first that is not (``failed_message`` is its template: the export's, or
    the place's)."""
    def rel(path: Path) -> str:
        return f"{course_id}/{path.relative_to(course_dir).as_posix()}"

    for folder in folders:
        target = folder
        while not target.exists() and target != course_dir:
            target = target.parent
        try:
            _probe(target)
        except OSError as exc:
            raise ExportWriteFailed(failed_message.format(path=rel(folder), reason=exc.strerror or str(exc))) from None
    for path in files:
        if workshops._is_link(path):
            raise ExportRefused(f"{rel(path)} is a link: nothing is written through it.")
        if path.exists() and not os.access(path, os.W_OK):
            raise ExportWriteFailed(failed_message.format(path=rel(path), reason="it is read-only"))


def write_course_files(course_dir: Path, course_id: str, files: list[tuple[Path, bytes]], expected: dict[Path, str | None], *,
                       failed_message: str = WRITE_FAILED_MESSAGE, changed_message_of: Callable[[str], str] = changed_message) -> list[str]:
    """Write ``files`` - ``(a target inside the course, its new bytes)`` - the way :func:`write` writes an export
    (V3's Place in the course reuses it for a video's link): under the write lock, every folder on each target's way
    proven a real folder of the course (:func:`own_folder`), every target checked writable before the first byte
    (:func:`check_writable`), each file's sha256 checked against ``expected`` (the sha256 the preview showed; None
    for a file that was not there) just before it is replaced - :class:`GuideChanged` with ``changed_message_of``
    naming it - and every write through ONE :class:`_Journal`: a failure part-way puts every file back and answers
    :class:`ExportWriteFailed` (``failed_message``). Returns the paths written, relative to the course folder."""
    with _write_lock:
        for target, _data in files:
            folder = course_dir
            for part in target.relative_to(course_dir).parts[:-1]:
                folder = own_folder(folder, part)
        folders = sorted({target.parent for target, _data in files}, key=str)
        check_writable(course_dir, folders, [target for target, _data in files], course_id, failed_message=failed_message)
        journal = _Journal(course_dir, course_id)
        written: list[str] = []
        try:
            for target, data in files:
                rel = f"{course_id}/{target.relative_to(course_dir).as_posix()}"
                if _file_sha(target) != expected.get(target):
                    raise GuideChanged(changed_message_of(rel))
                if journal.write(target.parent, target.name, data):
                    written.append(target.relative_to(course_dir).as_posix())
        except (GuideChanged, ExportRefused):
            journal.rollback()
            raise
        except OSError as exc:
            journal.rollback()
            raise ExportWriteFailed(failed_message.format(path=journal.current, reason=exc.strerror or str(exc))) from None
        journal.commit()
    return written


def _shas_for(plan: Plan, shas: dict[str | None, str]) -> dict[str, str]:
    """The sha256 the body gave for each guide of the plan (E4): ``{slug: sha256}``, or ``{None: sha256}`` - the body
    before W2e, accepted for a record of ONE workshop only. :class:`ExportRefused` for a guide without one, or a
    slug the record has no workshop of; :class:`GuideChanged` naming the first guide whose sha256 is not the plan's."""
    given = dict(shas)
    if None in given:
        if len(plan.guides) != 1:
            raise ExportRefused(SHA_PER_GUIDE_MESSAGE.format(slug=plan.guides[1].slug))
        given = {plan.slug: given[None]}
    for slug in given:
        if slug not in plan.slugs:
            raise ExportRefused(UNKNOWN_GUIDE_MESSAGE.format(slug=slug))
    for g in plan.guides:
        if g.slug not in given:
            raise ExportRefused(SHA_PER_GUIDE_MESSAGE.format(slug=g.slug))
    for g in plan.guides:
        if g.sha256 != given[g.slug]:
            raise GuideChanged(changed_message(g.where(plan.course["id"])))
    return given


def write(rec: dict, shas: dict[str | None, str], player: bool = False) -> dict:
    """Write the export into the course: refused with :class:`GuideChanged` (nothing written, the guide named) when
    ANY guide's sha256 is not the one in ``shas`` (``{slug: sha256}`` per guide; ``{None: sha256}`` is the body
    before W2e, for a record of one workshop); every folder proven (no link, no junction) and every target of every
    guide checked writable before the first byte (:func:`check_writable`); then the pictures, the player (when
    asked, beside the FIRST guide), each ``guide.md`` in the course's order - its sha256 checked once more just
    before it is replaced - and last the pictures of our old blocks no guide names any more. Every write through
    :func:`write_file`, inside the course's folder, kept in ONE :class:`_Journal`: a failure on the way (an
    ``OSError``, a link met, a guide changed under it) rolls back everything this export did, every guide and
    picture written, and answers :class:`ExportWriteFailed` (or the refusal it met). Returns what was written."""
    with _write_lock:
        plan = compute(rec, player=player)
        given = _shas_for(plan, shas)
        course_id = plan.course["id"]
        # Every folder proven first - a link or a junction anywhere refuses the export before a byte is written.
        workshop_dirs: dict[str, Path] = {}
        for g in plan.guides:
            folder = own_folder(plan.course_dir, g.slug)
            if folder.resolve() != g.guide.parent.resolve():
                raise ExportRefused(f"The workshop's folder is not where its guide is: {g.slug}.")
            workshop_dirs[g.slug] = folder
        assets_dir = own_folder(plan.course_dir, IMAGES_FOLDER[0])
        images_dir = own_folder(assets_dir, IMAGES_FOLDER[1]) if assets_dir.is_dir() else assets_dir / IMAGES_FOLDER[1]
        player_dir = own_folder(workshop_dirs[plan.slug], PLAYER_FOLDER) if plan.player_files else None
        actions = {row["path"]: row["action"] for row in file_actions(plan)}
        targets = [images_dir / name for _picture, name in plan.images if actions.get(f"_assets/images/{name}") == "write"]
        targets += [images_dir / name for name in plan.stale_images]
        if player_dir is not None:
            targets += [player_dir / name for name in plan.player_files if actions.get(f"{plan.slug}/{PLAYER_FOLDER}/{name}") == "write"]
            targets += [player_dir / name for name in plan.stale_player]
        targets += [g.guide for g in plan.guides if g.changed]
        targets += [folder / name for folder, name, _why in plan.debris]
        # Only the folders something goes into are probed: an export with nothing to write touches nothing.
        folders = [f for f in (*workshop_dirs.values(), images_dir, player_dir) if f is not None and any(t.parent == f for t in targets)]
        check_writable(plan.course_dir, folders, targets, course_id)

        journal = _Journal(plan.course_dir, course_id)
        written: list[str] = []
        removed: list[str] = []
        guides_written: dict[str, bool] = {g.slug: False for g in plan.guides}
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
            for g in plan.guides:
                if not g.changed:
                    continue
                # Once more, just before it is replaced: somebody may have saved it while the pictures were written.
                if _file_sha(g.guide) != given[g.slug]:
                    raise GuideChanged(changed_message(g.where(course_id)))
                guides_written[g.slug] = journal.write(workshop_dirs[g.slug], workshops.GUIDE_FILE, g.new_bytes)
                if guides_written[g.slug]:
                    written.append(f"{g.slug}/{workshops.GUIDE_FILE}")
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
    n = plan.figures
    figures = f"{n} figure{'' if n == 1 else 's'}"
    where = plan.first.where(course_id)
    if len(plan.guides) == 1:
        into = where
        message = (f"Wrote {figures} into {where}. Review and publish it in the Content Editor." if written or removed
                   else f"Nothing to write: {where} already holds {figures} of this walkthrough.")
    else:
        into = f"{len(plan.guides)} guides of {course_id} ({', '.join(plan.slugs)})"
        message = (f"Wrote {figures} into {into}. Review and publish them in the Content Editor." if written or removed
                   else f"Nothing to write: the {into} already hold {figures} of this walkthrough.")
    guides = [{"slug": g.slug, "guide": g.where(course_id), "figures": g.figures, "guide_written": guides_written[g.slug],
               "guide_sha256": _sha(g.new_bytes)} for g in plan.guides]
    return {
        "course": course_id, "slug": plan.slug, "guide": where, "guides": guides, "figures": n, "images": images,
        "player": bool(plan.player_files), "guide_written": any(guides_written.values()), "written": written, "removed": removed,
        "guide_sha256": guides[0]["guide_sha256"], "message": message,
    }


def audit_detail(result: dict) -> str:
    """The audit row's detail: "<course>/<slug>: N figures, M images" for one guide, "<course>: N figures, M images
    into K guides (a, b)" for several (E4) - ", player" when the player was written."""
    n, m = result["figures"], result["images"]
    counts = f"{n} figure{'' if n == 1 else 's'}, {m} image{'' if m == 1 else 's'}"
    guides = result.get("guides") or []
    if len(guides) > 1:
        detail = f"{result['course']}: {counts} into {len(guides)} guides ({', '.join(g['slug'] for g in guides)})"
    else:
        detail = f"{result['course']}/{result['slug']}: {counts}"
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
    """The export as a zip laid out like the course folder (C6): every guide's ``<slug>/guide.md``,
    ``_assets/images/…`` (every placed step's picture) and, with the player, ``<first slug>/walkthrough/…`` - to
    copy into the course by hand where the server may not write it. The zip's temporary file; the caller removes it."""
    entries: dict[str, bytes | Path] = {f"{g.slug}/{workshops.GUIDE_FILE}": g.new_bytes for g in plan.guides}
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
