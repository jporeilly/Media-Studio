"""Course workshops (W2): the courses folder, a course's workshops, and the
parser that turns a workshop's GitBook-style guide into the chapters and
steps a walkthrough is made of. Nothing here writes: the import that makes
the walkthrough is ``services.walkthroughs.create_from_workshop``, fed by
:func:`import_plan`.

**The format.** The courses folder (Settings > Studio, ``courses_folder``)
holds one folder per course; a course is any child folder with a
``course.json`` (``id``, ``title``, ``version``, ``launchers``), a
``SUMMARY.md`` (``## Section`` headings and ``* [Title](nn-slug/guide.md)``
links: the order), ``_assets/images/*.png`` and, per workshop, a
``nn-slug/manifest.json`` (``title``, ``estimatedMinutes``, ``stepCount``)
and a ``nn-slug/guide.md``.

**The parser's rules** (:func:`parse_guide`, pure over the Markdown text):

- a **chapter** is a ``##`` (or ``###``) section, in order, except
  ``Troubleshooting``, ``Lab Files`` and ``Solution`` (``SKIPPED_SECTIONS``)
  and any section with no step and no launcher; the first step of a section
  carries the chapter's name; steps before the first heading carry none;
- a **step** is a numbered item of a section, in order; a ``<button
  data-launch="…">`` launcher in a section is a step too, where it stands
  ("Open win_preview.ktr in Pentaho Data Integration.": the file's name and
  the launcher's label from ``course.json``, else the button's own text);
  ``data-graph`` buttons are the guide's viewer, not steps;
- a step's **instruction** is the item's text as plain text (bold, emphasis
  and code marks dropped, links reduced to their text, the continuation lines
  joined - a bullet inside the item is a line of its own), followed, as
  further paragraphs, by the content that follows the item before the next
  item, launcher, hint or heading - a prose paragraph, a bullet list (a line
  per bullet), a code block (what the learner types or picks, verbatim, KEPT
  BETWEEN ITS ``` FENCES so the narration to come can skip it) and a table (a
  line per row, cells joined by " · "); a figure between them does not end
  that content, a hint does - capped at ``MAX_INSTRUCTION_CHARS``: a paragraph
  that does not fit is cut after its last whole sentence, a code block that
  does not fit is left out whole (its fences stay paired); the **text** (the
  tooltip) is the instruction's first sentence, at most ``TOOLTIP_CHARS``
  characters, cut at a word; every text obeys the save's own rule - the
  control characters ``services.walkthroughs.TEXT_CONTROL_RE`` names are
  stripped (a tab and a line break stay), and every field is bounded by the
  schema's own constants (``MAX_STEP_TEXT``, ``MAX_INSTRUCTION_CHARS``,
  ``MAX_CHAPTER_CHARS``, ``MAX_NOTE_CHARS``, ``MAX_SOURCE_PATH_CHARS``,
  ``MAX_ALT_CHARS``), so what the import writes the builder can save;
- a step's **picture** is the first figure that follows the item (or the
  launcher) before the next item, launcher or heading - a Markdown image line
  ``![alt](…png)``, or an ``<img src="…" alt="…">`` tag on its line, alone or
  in its house ``<figure>`` (any attribute order, either quote or none,
  self-closing or not; ``src`` and ``alt`` HTML-unescaped, other attributes
  ignored) - a figure after two items in a row belongs to the last of them;
  its path must name a PNG under the course's ``_assets/images``
  (:func:`image_relative_path`), so an image by URL (the Vimeo placeholder), a
  ``data:`` source, a ``<video>`` or an ``<iframe>`` is never a picture; the
  alt text is kept;
- **hints** (``>`` block quotes: Note, Tip, Warning, Critical, Key Takeaways)
  are not steps; an ``Under the hood`` hint is kept as the ``note`` of the
  step it follows (plain text, ``MAX_NOTE_CHARS``), for the narrated video to
  come;
- ``<details>`` blocks, ``:::: tabs`` fences, HTML wrappers (``<figure>``,
  ``<div>``, ``<figcaption>``; the ``<img>`` inside a figure is read, above),
  rules and anything else are skipped;
- at most ``MAX_PARSE_STEPS`` steps are read (a bound on abuse; the
  walkthrough's own ``MAX_STEPS`` applies on import).

**Several workshops, chosen sections** (W2e). One import may take up to ``MAX_WORKSHOPS``
workshops of ONE course (:func:`load_workshops` hands them back in the course's
order whatever the order asked), each with the chapters to leave out by name
(:func:`import_plan` with ``skip_chapters``; :func:`running_chapters` says which
chapter a step is in, the preview's ``in_chapter``); their steps are joined as one
walkthrough's by :func:`combine_steps` (with several workshops every workshop starts
a chapter, "<workshop title> · <section>"), the record's ``source`` by
:func:`import_source`, and the name when none is given by :func:`default_name`.

**Nothing outside the folder is read.** A course id and a slug are names
(``NAME_RE``: no separator, no ``..``), the course folder must be a real
folder under the courses folder and not a link, every file is proven under
its folder with ``utils.helpers.file_under`` after each path component is
checked not to be a link (:func:`_own_file`), the guide images included, and
a file over ``MAX_GUIDE_BYTES`` is refused before it is read.

**A bad course never takes the list down.** A course whose ``course.json``
or ``SUMMARY.md`` is over the cap, not UTF-8 or not JSON, and a workshop whose
``manifest.json`` or ``guide.md`` is, are LEFT OUT of the list with a logged
warning (:func:`list_courses`); the list answers the others. The preview or
import of one of those answers the reason (:class:`UnreadableCourse`, the
routes' 400).
"""

from __future__ import annotations

import bisect
import html
import json
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path

from services import walkthroughs as rules
from services.studio_settings import courses_folder
from utils.helpers import file_under
from utils.logger import get_logger

log = get_logger("WORKSHOPS")

#: A guide (or any file read here) larger than this is refused before it is read.
MAX_GUIDE_BYTES = 2 * 1024 * 1024
#: Steps read from one guide, at most (a bound on abuse; ``walkthroughs.MAX_STEPS`` applies on import).
MAX_PARSE_STEPS = 500
#: Characters of a step's text made from its instruction's first sentence (the tooltip).
TOOLTIP_CHARS = 140
#: Workshops of one course that ONE import may take (W2e); the walkthrough's own ``MAX_STEPS`` bounds their steps together.
MAX_WORKSHOPS = 50
#: Sections that are never chapters, by their heading (case-insensitive, the ``<!-- -->`` comment dropped).
SKIPPED_SECTIONS = ("troubleshooting", "lab files", "solution")
#: The one hint kept (as the step's note); every other hint kind is skipped.
KEPT_HINT = "under the hood"
COURSE_FILE = "course.json"
SUMMARY_FILE = "SUMMARY.md"
GUIDE_FILE = "guide.md"
MANIFEST_FILE = "manifest.json"
#: A course id or a workshop slug: a folder NAME, never a path.
NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,99}$")
IMAGE_SUFFIXES = (".png",)
#: Where a guide's pictures live: the course's own images folder. A picture's path must resolve under it.
IMAGE_FOLDER = "_assets/images/"
NOT_SET_MESSAGE = "Set the Courses folder on the Settings page (Studio settings, an administrator) first."


class NoCoursesFolder(LookupError):
    """The courses folder is not set, or is no longer a folder."""


class UnreadableCourse(ValueError):
    """A course or workshop file over the cap, not UTF-8 text or not JSON: the course or workshop is left out
    of the list, and its preview or import answers this message."""


class GuideTooLarge(UnreadableCourse):
    """A file over ``MAX_GUIDE_BYTES``: refused before it is read."""


# -- the files -----------------------------------------------------------------------------------------

def safe_name(name: str) -> bool:
    """Whether ``name`` is a course id or a slug: one folder name, no separator, no ``..``."""
    return isinstance(name, str) and bool(NAME_RE.fullmatch(name)) and ".." not in name


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows junction (``is_junction`` exists from 3.12); never followed."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if is_junction else False
    except OSError:
        return True


def _own_dir(root: Path, name: str) -> Path | None:
    """``root/name`` proven a real folder under ``root``: a safe name, not a link, resolving inside."""
    if not safe_name(name):
        return None
    path = root / name
    try:
        if _is_link(path) or not path.is_dir():
            return None
        if root.resolve() not in path.resolve().parents:
            return None
    except OSError:
        return None
    return path


def _own_file(base: Path, rel: str) -> Path | None:
    """``base/rel`` proven an existing file under ``base``: every component a name (no ``.``, ``..``),
    none of them a link, and ``file_under``'s resolved check on top. None otherwise."""
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        return None
    current = base
    for part in parts:
        current = current / part
        if _is_link(current):
            return None
    return file_under(base, "/".join(parts))


def _read_text(path: Path) -> str | None:
    """The file's text, UTF-8, or None when it cannot be read; ``GuideTooLarge`` past the cap, before reading."""
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > MAX_GUIDE_BYTES:
        raise GuideTooLarge(f"{path.name} is larger than {MAX_GUIDE_BYTES // 1024 ** 2} MB and cannot be imported.")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _read_json(path: Path) -> dict | None:
    """The file as a JSON object, or None when it cannot be read or is not one (``GuideTooLarge`` past the cap)."""
    text = _read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _read_text_strict(path: Path) -> str:
    """The file's text, or ``UnreadableCourse`` saying why not (over the cap, or not UTF-8 text)."""
    text = _read_text(path)
    if text is None:
        raise UnreadableCourse(f"{path.name} cannot be read as UTF-8 text.")
    return text


def _read_json_strict(path: Path) -> dict:
    """The file as a JSON object, or ``UnreadableCourse`` saying why not (over the cap, not UTF-8, not JSON)."""
    text = _read_text_strict(path)
    try:
        data = json.loads(text)
    except ValueError:
        raise UnreadableCourse(f"{path.name} is not valid JSON.") from None
    if not isinstance(data, dict):
        raise UnreadableCourse(f"{path.name} is not a JSON object.")
    return data


def courses_root() -> Path:
    """The courses folder, or ``NoCoursesFolder``."""
    root = courses_folder()
    if root is None:
        raise NoCoursesFolder(NOT_SET_MESSAGE)
    return root


# -- the courses ---------------------------------------------------------------------------------------

def parse_summary(text: str) -> list[dict]:
    """``SUMMARY.md`` as sections: ``[{"title", "workshops": [{"slug", "title"}]}]`` in the file's order.
    A ``## `` heading starts a section (``###`` and deeper are grouping only); a ``* [Title](slug/guide.md)``
    item is a workshop. Items before any heading sit in a section with no title."""
    sections: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = {"title": plain_text(heading.group(1)), "workshops": []}
            sections.append(current)
            continue
        item = re.match(r"^[-*+]\s+\[([^\]]*)\]\(([^)]+)\)", line)
        if not item:
            continue
        slug = item.group(2).strip().split("#", 1)[0].split("?", 1)[0].strip("/").split("/", 1)[0]
        if not safe_name(slug):
            continue
        if current is None:
            current = {"title": "", "workshops": []}
            sections.append(current)
        current["workshops"].append({"slug": slug, "title": plain_text(item.group(1)) or slug})
    return sections


def _launcher_labels(course: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (course.get("launchers") or {}).items():
        if isinstance(value, dict) and isinstance(value.get("label"), str) and value["label"].strip():
            out[str(key)] = value["label"].strip()
    return out


def _course_or_reason(root: Path, course_id: str) -> tuple[dict | None, str | None]:
    """One course, or why it is left out: ``(course, None)`` for a course; ``(None, None)`` when ``course_id``
    is not a course folder under ``root`` at all (not a safe name, a link, no ``course.json``, or one that is a
    link); ``(None, reason)`` when its ``course.json`` or ``SUMMARY.md`` is over the cap, not UTF-8 or not JSON.

    The course: its ``course.json`` (the id, title, version, description, launcher labels), its workshops in
    ``SUMMARY.md`` order - those with a readable ``guide.md`` within the cap and a readable manifest when there
    is one (the manifest's title, minutes and step count) - and ``left_out``: ``{slug: reason}`` for the
    workshops whose manifest or guide could not be read, so the preview and the import can say why."""
    folder = _own_dir(root, course_id)
    if folder is None:
        return None, None
    course_file = _own_file(folder, COURSE_FILE)
    if course_file is None:
        return None, None
    try:
        data = _read_json_strict(course_file)
        summary_file = _own_file(folder, SUMMARY_FILE)
        summary = parse_summary(_read_text_strict(summary_file)) if summary_file else []
    except UnreadableCourse as exc:
        return None, str(exc)
    if not summary:
        # No SUMMARY.md: the workshop folders in name order, in one section.
        found = []
        for child in sorted(folder.iterdir()):
            if child.is_dir() and not _is_link(child) and safe_name(child.name) and _own_file(folder, f"{child.name}/{GUIDE_FILE}"):
                found.append({"slug": child.name, "title": child.name})
        summary = [{"title": "", "workshops": found}] if found else []
    sections = []
    left_out: dict[str, str] = {}
    for section in summary:
        workshops = []
        for item in section["workshops"]:
            slug = item["slug"]
            guide = _own_file(folder, f"{slug}/{GUIDE_FILE}")
            if guide is None:
                continue
            manifest_file = _own_file(folder, f"{slug}/{MANIFEST_FILE}")
            try:
                manifest = _read_json_strict(manifest_file) if manifest_file else {}
                _read_text_strict(guide)
            except UnreadableCourse as exc:
                left_out[slug] = f"{slug}/{exc}"
                continue
            title = manifest.get("title") if isinstance(manifest.get("title"), str) and manifest["title"].strip() else item["title"]
            workshops.append({
                "slug": slug,
                "title": title.strip()[:rules.MAX_NAME_CHARS],
                "description": str(manifest.get("description") or "")[:500],
                "estimatedMinutes": manifest.get("estimatedMinutes") if isinstance(manifest.get("estimatedMinutes"), int) else None,
                "stepCount": manifest.get("stepCount") if isinstance(manifest.get("stepCount"), int) else None,
            })
        if workshops:
            sections.append({"title": section["title"], "workshops": workshops})
    title = data.get("title") if isinstance(data.get("title"), str) and data["title"].strip() else course_id
    return {
        "id": course_id,
        "title": title.strip()[:200],
        "version": str(data.get("version") or "")[:40],
        "description": str(data.get("description") or "")[:500],
        "launchers": _launcher_labels(data),
        "sections": sections,
        "left_out": left_out,
    }, None


def load_course(root: Path, course_id: str) -> dict | None:
    """One course (see :func:`_course_or_reason`), or None - not a course, or one left out."""
    course, _reason = _course_or_reason(root, course_id)
    return course


def list_courses(root: Path) -> list[dict]:
    """Every course under ``root`` (a child folder with a ``course.json``), by folder name. A course whose
    files cannot be read is left out with a warning, as is a workshop of a course; the rest are listed."""
    out = []
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    for child in children:
        if not (child.is_dir() and not _is_link(child) and safe_name(child.name)):
            continue
        course, reason = _course_or_reason(root, child.name)
        if reason:
            log.warning("Course %s is left out of the list: %s", child.name, reason)
            continue
        if course is None:
            continue
        for slug, why in course["left_out"].items():
            log.warning("Course %s: workshop %s is left out of the list: %s", child.name, slug, why)
        out.append(course)
    return out


def public_course(course: dict) -> dict:
    """The course as the API lists it: no launcher table, no left-out reasons."""
    return {key: value for key, value in course.items() if key not in ("launchers", "left_out")}


def find_workshop(course: dict, slug: str) -> dict | None:
    for section in course["sections"]:
        for workshop in section["workshops"]:
            if workshop["slug"] == slug:
                return {**workshop, "section": section["title"]}
    return None


def load_workshops(root: Path, course_id: str, slugs: list[str]) -> tuple[dict, list[tuple[dict, str]], Path]:
    """The course, each named workshop (as listed, with its section) with its guide's text - IN THE COURSE'S ORDER,
    whatever the order of ``slugs`` - and the course's folder (W2e). ``LookupError`` for no such course or for a
    workshop that is not there (named); ``UnreadableCourse`` (``GuideTooLarge`` for a file over the cap) with the
    reason for a course or workshop the list left out (the workshop named). Nothing is half-done: one workshop
    refused refuses the lot before any guide is handed back."""
    course, reason = _course_or_reason(root, course_id)
    if reason:
        raise UnreadableCourse(f"{course_id}/{reason}")
    if course is None:
        raise LookupError("No such course.")
    listed = {w["slug"]: {**w, "section": section["title"]} for section in course["sections"] for w in section["workshops"]}
    for slug in slugs:
        if slug not in listed:
            if slug in course["left_out"]:
                raise UnreadableCourse(f"{course_id}/{course['left_out'][slug]}")
            raise LookupError(f"No such workshop in that course: {slug}.")
    folder = root / course_id
    wanted = set(slugs)
    out: list[tuple[dict, str]] = []
    for slug, workshop in listed.items():
        if slug not in wanted:
            continue
        guide = _own_file(folder, f"{slug}/{GUIDE_FILE}")
        if guide is None:
            raise LookupError(f"The workshop's guide cannot be read: {slug}.")
        out.append((workshop, _read_text_strict(guide)))
    return course, out, folder


def load_workshop(root: Path, course_id: str, slug: str) -> tuple[dict, dict, str, Path]:
    """The course, the workshop (as listed), the guide's text and the course's folder: :func:`load_workshops` for one."""
    course, parts, folder = load_workshops(root, course_id, [slug])
    workshop, text = parts[0]
    return course, workshop, text, folder


def resolve_image(folder: Path, slug: str, image: str | None) -> Path | None:
    """The guide image, as the guide wrote it (``../_assets/images/x.png#w=520``), proven under the
    COURSE folder; None for no image, a URL, a path outside the course's ``_assets/images``, a link, or a
    missing file."""
    rel = image_relative_path(slug, image)
    return _own_file(folder, rel) if rel else None


def image_relative_path(slug: str, image: str | None) -> str | None:
    """The image's path relative to the course folder (``_assets/images/x.png``), or None for a URL (a
    ``data:`` source included), a path that walks out of the course or is not under its ``_assets/images``
    (``IMAGE_FOLDER``), or a file that is not a PNG."""
    if not image:
        return None
    target = image.split("#", 1)[0].split("?", 1)[0].strip()
    if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith(("/", "\\")):
        return None
    joined = posixpath.normpath(posixpath.join(slug, target.replace("\\", "/")))
    if not joined.lower().startswith(IMAGE_FOLDER) or not joined.lower().endswith(IMAGE_SUFFIXES):
        return None
    if len(joined) > rules.MAX_SOURCE_PATH_CHARS or rules.TEXT_CONTROL_RE.search(joined):
        return None
    return joined


# -- plain text ----------------------------------------------------------------------------------------

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
#: An alt text or a link text may hold one level of brackets (``$.customers[*]``).
_BRACKETED = r"((?:[^\[\]]|\[[^\[\]]*\])*)"
_IMAGE_INLINE = re.compile(r"!\[" + _BRACKETED + r"\]\([^)]*\)")
_LINK_INLINE = re.compile(r"\[" + _BRACKETED + r"\]\([^)]*\)")
_CODE_SPAN = re.compile(r"`([^`]*)`")
#: How a code block opens and closes inside an instruction: the fence the guide used, kept so the narration round
#: can skip the block; the language is dropped. ``plain_text`` keeps a fence where it drops a code span's marks.
CODE_FENCE = "```"
#: Stands in for a fence while the code-span pass runs; every control character was stripped before it.
_FENCE_MARK = "\x00"
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_EM = re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?!\w)|(?<![\w_])_(?!\s)([^_]+?)(?<!\s)_(?!\w)")
#: A mark left over from an unbalanced bold or emphasis: a ``*`` touching a word; never ``[*]`` or an ``A * B`` operator.
_LONE_STAR = re.compile(r"(?<!\[)(?:(?<=\S)\*|\*(?=\S))(?!\])")
_CHECKBOX = re.compile(r"^\[[ xX]\]\s*")
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def plain_text(text: str) -> str:
    """Markdown and HTML to plain text: the control characters a step may not carry stripped (the save's
    own rule, ``services.walkthroughs.TEXT_CONTROL_RE``), comments and tags out, an image to its alt text, a
    link to its text, code spans to their text (a ``` fence KEPT: it marks a code block for the narration to
    skip), bold and emphasis marks dropped (an unbalanced mark too), entities unescaped - and the control
    characters stripped once more, since an entity may encode one (``&#12;`` is a form feed) - a leading
    ``[ ]`` checkbox dropped, spaces collapsed."""
    s = _HTML_COMMENT.sub("", rules.strip_control_characters(text))
    s = _IMAGE_INLINE.sub(r"\1", s)
    s = _LINK_INLINE.sub(r"\1", s)
    s = s.replace(CODE_FENCE, _FENCE_MARK)
    s = _CODE_SPAN.sub(r"\1", s).replace("`", "")
    s = _TAG.sub("", s)
    s = rules.strip_control_characters(html.unescape(s).replace(_FENCE_MARK, CODE_FENCE))
    for _ in range(3):
        s = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _EM.sub(lambda m: m.group(1) or m.group(2) or "", s)
    s = _LONE_STAR.sub("", s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return _CHECKBOX.sub("", s).strip()


def cut_at_word(text: str, limit: int, ellipsis: str = "…") -> str:
    """``text`` within ``limit`` characters, cut at a word and ended with an ellipsis when it was longer."""
    if len(text) <= limit:
        return text
    head = text[: max(1, limit - len(ellipsis))]
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).rstrip() + ellipsis


def cut_at_sentence(text: str, limit: int) -> str:
    """``text`` within ``limit`` characters, cut after the last whole sentence that fits; at a word when no
    sentence end fits."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(head)]
    if ends:
        return head[: ends[-1]].rstrip()
    return cut_at_word(text, limit)


def first_sentence(text: str) -> str:
    """The first sentence of the first paragraph, on one line, within ``TOOLTIP_CHARS``."""
    first = (text or "").split("\n\n", 1)[0].replace("\n", " ").strip()
    match = _SENTENCE_END.search(first)
    sentence = first[: match.end()] if match else first
    return cut_at_word(sentence.strip(), TOOLTIP_CHARS)


# -- the guide's blocks --------------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_ITEM = re.compile(r"^ {0,3}(\d+)[.)]\s+(.*)$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_IMAGE_LINE = re.compile(r"^\s*!\[" + _BRACKETED + r"\]\(([^)\s]+)[^)]*\)\s*$")
#: A tag's attributes up to its ``>``: a ``>`` inside a quoted value does not end the tag.
_TAG_ATTRS = r"""((?:[^>"']|"[^"]*"|'[^']*')*)"""
#: The house figure's picture: an ``<img>`` tag at the start of its line, alone or after the opening tags of its
#: ``<figure>``, ``<div>``, ``<p>`` or ``<a>`` (what follows it on the line - a caption, closing tags - is not read).
_IMG_LINE = re.compile(r"^\s*(?:<(?:figure|div|p|a)\b" + _TAG_ATTRS + r">\s*)*<img\b" + _TAG_ATTRS + ">", re.I)
#: One attribute of a tag: its name, and its value double-quoted, single-quoted, bare, or none.
_TAG_ATTR = re.compile(r"""([^\s"'<>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?""")
_BUTTON = re.compile(r"<button\b([^>]*)>(.*?)</button>", re.S | re.I)
_ATTR = re.compile(r'\b(data-launch|data-path|data-graph)\s*=\s*"([^"]*)"', re.I)
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_HINT_KIND = re.compile(r"^\*\*([^*]+?):\*\*\s*(.*)$")


@dataclass
class Block:
    kind: str
    text: str = ""
    lines: list[str] = field(default_factory=list)
    level: int = 0
    attrs: dict = field(default_factory=dict)
    #: W2c: the block's first and last line in the guide (0-based, inclusive) - what a step's span is made of.
    start: int = 0
    end: int = 0


def _is_special(line: str) -> bool:
    """A line that starts something of its own (never a lazy continuation of a paragraph or an item)."""
    s = line.strip()
    return (not s or bool(_HEADING.match(line)) or bool(_ITEM.match(line)) or bool(_BULLET.match(line)) or s.startswith(">")
            or bool(_FENCE.match(line)) or s.startswith("<") or bool(_IMAGE_LINE.match(line)) or bool(_TABLE_ROW.match(line))
            or bool(_RULE.match(line)) or s.startswith(":::"))


def _launcher_block(line: str) -> Block | None:
    """A ``<button data-launch>`` on the line - a launcher; a ``data-graph`` button is the guide's viewer."""
    for match in _BUTTON.finditer(line):
        attrs = {key.lower(): value for key, value in _ATTR.findall(match.group(1))}
        if "data-launch" in attrs:
            return Block("launcher", text=plain_text(match.group(2)), attrs={"id": attrs["data-launch"], "path": attrs.get("data-path") or None})
    return None


def _img_block(line: str) -> Block | None:
    """An ``<img>`` tag standing on the line (``_IMG_LINE``) as an image block: its ``src`` and its ``alt``,
    HTML-unescaped (a repeated attribute: the first, as a browser reads it), the alt's control characters
    stripped and its spaces collapsed. The path's rules are the Markdown image's (:func:`image_relative_path`)."""
    match = _IMG_LINE.match(line)
    if not match:
        return None
    attrs: dict[str, str] = {}
    for attr in _TAG_ATTR.finditer(match.group(2)):
        value = next((v for v in attr.group(2, 3, 4) if v is not None), "")
        attrs.setdefault(attr.group(1).lower(), html.unescape(value))
    alt = " ".join(rules.strip_control_characters(attrs.get("alt", "")).split())
    return Block("image", text=alt, attrs={"path": attrs.get("src", "")})


def guide_lines(text: str) -> list[str]:
    """The guide's lines without their endings (CRLF, CR or LF): line ``i`` here is line ``i`` of every span."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def blocks_of(text: str) -> list[Block]:
    """The guide's lines as blocks, each with its first and last line (``start``, ``end``, 0-based): headings,
    numbered items (with their continuation lines), bullet lists, hints (block quotes), code fences, images (a
    Markdown image line, or an ``<img>`` tag on its line, in its figure or alone), launchers, tables, prose
    paragraphs. ``<details>`` blocks, rules and ``:::`` fences are ``stop`` blocks - no content, but a step's
    span ends before them (W2c); HTML wrappers yield nothing."""
    lines = guide_lines(text)
    out: list[Block] = []
    i = 0
    n = len(lines)
    # Each pass appends at most one block; the next pass (or the end) gives it the lines it was read from.
    mark, begin = 0, 0
    while i < n:
        if len(out) > mark:
            out[-1].start, out[-1].end = begin, _last_line(lines, begin, i)
        mark, begin = len(out), i
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if _FENCE.match(line):
            fence = _FENCE.match(line).group(1)
            i += 1
            body: list[str] = []
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i].rstrip())
                i += 1
            i += 1
            out.append(Block("code", lines=body))
            continue
        if s.lower().startswith("<details"):
            while i < n and "</details>" not in lines[i].lower():
                i += 1
            i += 1
            out.append(Block("stop", text="details"))
            continue
        if s.startswith(">"):
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]).rstrip())
                i += 1
            out.append(Block("quote", lines=quote))
            continue
        launcher = _launcher_block(line)
        if launcher:
            out.append(launcher)
            i += 1
            continue
        heading = _HEADING.match(line)
        if heading:
            out.append(Block("heading", text=plain_text(heading.group(2)), level=len(heading.group(1))))
            i += 1
            continue
        item = _ITEM.match(line)
        if item:
            body = [item.group(2).strip()]
            i += 1
            while i < n:
                nxt = lines[i]
                if not nxt.strip():
                    # A loose item: an indented line after a blank still belongs to it.
                    if i + 1 < n and lines[i + 1].startswith(("  ", "\t")) and lines[i + 1].strip() and not _ITEM.match(lines[i + 1]):
                        i += 1
                        continue
                    break
                if nxt.startswith(("  ", "\t")) and not _ITEM.match(nxt):
                    bullet = _BULLET.match(nxt)
                    if bullet:
                        body.append(bullet.group(2).strip())
                    elif nxt.strip().startswith("<") or _FENCE.match(nxt):
                        break
                    else:
                        body[-1] = (body[-1] + " " + nxt.strip()).strip()
                    i += 1
                    continue
                if not _is_special(nxt):
                    body[-1] = (body[-1] + " " + nxt.strip()).strip()
                    i += 1
                    continue
                break
            out.append(Block("item", lines=body, attrs={"number": int(item.group(1))}))
            continue
        bullet = _BULLET.match(line)
        if bullet:
            items: list[str] = []
            while i < n:
                b = _BULLET.match(lines[i])
                if b:
                    items.append(b.group(2).strip())
                    i += 1
                elif lines[i].strip() and lines[i].startswith(("  ", "\t")) and items and not _is_special(lines[i].strip()):
                    items[-1] = items[-1] + " " + lines[i].strip()
                    i += 1
                else:
                    break
            out.append(Block("bullets", lines=items))
            continue
        image = _IMAGE_LINE.match(line)
        if image:
            out.append(Block("image", text=plain_text(image.group(1)), attrs={"path": image.group(2)}))
            i += 1
            continue
        figure = _img_block(line)
        if figure:
            out.append(figure)
            i += 1
            continue
        if _TABLE_ROW.match(line):
            rows: list[str] = []
            while i < n and _TABLE_ROW.match(lines[i]):
                cells = [plain_text(c) for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[-:\s]*", c) for c in cells):
                    rows.append(" · ".join(c for c in cells if c))
                i += 1
            out.append(Block("table", lines=rows))
            continue
        if _RULE.match(line) or s.startswith(":::"):
            # A rule or a ``:::`` fence (the guide's tabs): no content, but a step's span ends before it (W2c).
            i += 1
            out.append(Block("stop", text="rule" if _RULE.match(line) else "fence"))
            continue
        if s.startswith("<"):
            i += 1
            continue
        prose: list[str] = [s]
        i += 1
        while i < n and not _is_special(lines[i]):
            prose.append(lines[i].strip())
            i += 1
        out.append(Block("prose", text=" ".join(prose)))
    if len(out) > mark:
        out[-1].start, out[-1].end = begin, _last_line(lines, begin, n)
    return out


def _last_line(lines: list[str], start: int, stop: int) -> int:
    """The last line before ``stop`` that is not blank (``start`` when none is)."""
    for j in range(stop - 1, start - 1, -1):
        if lines[j].strip():
            return j
    return start


# -- the parse -----------------------------------------------------------------------------------------

@dataclass
class ParsedStep:
    chapter: str = ""
    instruction: str = ""
    text: str = ""
    #: The guide image as written (``../_assets/images/x.png#w=520``) or None; ``alt`` beside it.
    image: str | None = None
    alt: str = ""
    launcher: dict | None = None
    note: str = ""
    #: W2c, the step's place in the guide: its section's heading (as written), its span - the item's (or the
    #: launcher's) first line through the last line that is not blank before the next item, launcher, hint,
    #: heading, ``<details>``, rule or ``:::`` fence, 1-based and inclusive - and its anchor: ``key`` (the
    #: section and the first sentence, normalised: :func:`anchor_key`) with ``ordinal``, the how-manieth step
    #: of the guide with that key (1 for the first).
    section: str = ""
    start_line: int = 0
    end_line: int = 0
    key: str = ""
    ordinal: int = 0
    _paragraphs: list[str] = field(default_factory=list, repr=False)
    _block_end: int = field(default=0, repr=False)


@dataclass
class ParsedGuide:
    title: str
    steps: list[ParsedStep]
    chapters: list[str]
    truncated: bool = False


def _hint_kind(quote: list[str]) -> tuple[str, list[str]]:
    """The hint's kind ("under the hood", "note", …; "" for none) and its lines without the marker."""
    for index, line in enumerate(quote):
        if not line.strip():
            continue
        match = _HINT_KIND.match(line.strip())
        if not match:
            return "", quote
        rest = quote[:index] + ([match.group(2)] if match.group(2).strip() else []) + quote[index + 1:]
        return match.group(1).strip().lower(), rest
    return "", quote


def _note_text(lines: list[str]) -> str:
    """A hint's plain text: paragraphs separated by a blank line, wrapped lines joined, a ``####`` title
    and each bullet or numbered line a line of its own."""
    paragraphs: list[list[str]] = [[]]
    for raw in lines:
        s = raw.strip()
        if not s:
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        heading = _HEADING.match(s)
        bullet = _BULLET.match(s)
        item = _ITEM.match(s)
        if heading:
            paragraphs[-1].append(plain_text(heading.group(2)))
            paragraphs.append([])
        elif bullet or item:
            paragraphs[-1].append(plain_text((bullet or item).group(2)))
        elif paragraphs[-1]:
            # A wrapped line continues the line before it (a bullet's, or the paragraph's).
            paragraphs[-1][-1] = (paragraphs[-1][-1] + " " + plain_text(s)).strip()
        else:
            paragraphs[-1].append(plain_text(s))
    text = "\n\n".join("\n".join(p) for p in paragraphs if p)
    return cut_at_sentence(text.strip(), rules.MAX_NOTE_CHARS)


def _item_paragraph(lines: list[str]) -> str:
    return "\n".join(t for t in (plain_text(x) for x in lines) if t)


def _block_paragraph(block: Block) -> str:
    """What a block following an item adds to the instruction: prose as one line, bullets and table
    rows as lines, a code block verbatim between its fences (its lines stripped of control characters by
    the same rule as every text)."""
    if block.kind == "prose":
        return plain_text(block.text)
    if block.kind in ("bullets", "table"):
        return "\n".join(t for t in (plain_text(x) if block.kind == "bullets" else x for x in block.lines) if t)
    if block.kind == "code":
        body = "\n".join(rules.strip_control_characters(x) for x in block.lines).strip("\n")
        return f"{CODE_FENCE}\n{body}\n{CODE_FENCE}" if body else ""
    return ""


def join_within(paragraphs: list[str], limit: int) -> str:
    """The paragraphs joined by a blank line within ``limit`` characters: a paragraph that does not fit is
    cut after its last whole sentence (``cut_at_sentence``), a code block that does not fit is left out whole
    - its fences stay paired - and nothing follows the cut."""
    out: list[str] = []
    length = 0
    for paragraph in paragraphs:
        if not paragraph:
            continue
        extra = len(paragraph) + (2 if out else 0)
        if length + extra <= limit:
            out.append(paragraph)
            length += extra
            continue
        if paragraph.startswith(CODE_FENCE):
            break
        room = limit - length - (2 if out else 0)
        cut = cut_at_sentence(paragraph, room).strip() if room > 1 else ""
        if cut:
            out.append(cut)
        break
    return "\n\n".join(out)


def launcher_instruction(button_text: str, path: str | None, labels: dict[str, str], launcher_id: str) -> str:
    """"Open win_preview.ktr in Pentaho Data Integration." for a button with a file, from the launcher's
    label in ``course.json`` (else the app named by the button's "Open in …"); the button's own text,
    as a sentence, for one without."""
    text = plain_text(button_text).rstrip(".").strip()
    if path:
        name = plain_text(posixpath.basename(path.replace("\\", "/")) or path)
        app = plain_text(labels.get(launcher_id, "")) or re.sub(r"^open\s+in\s+", "", text, flags=re.IGNORECASE).strip() or text
        return f"Open {name} in {app}."
    return f"{text}." if text else f"Open {plain_text(labels.get(launcher_id, '')) or launcher_id}."


def source_path(value: str | None) -> str | None:
    """A launcher's or an image's path as the step's source keeps it: control characters stripped, within
    ``MAX_SOURCE_PATH_CHARS``; None for none or for one too long to be a real path."""
    if not value:
        return None
    clean = rules.strip_control_characters(value)
    return clean if clean and len(clean) <= rules.MAX_SOURCE_PATH_CHARS else None


def parse_guide(text: str, launchers: dict[str, str] | None = None) -> ParsedGuide:
    """The guide's chapters and steps, by the rules in the module docstring. Pure."""
    labels = launchers or {}
    title = ""
    steps: list[ParsedStep] = []
    chapters: list[str] = []
    section = ""
    section_skipped = False
    section_steps = 0
    open_step: ParsedStep | None = None
    accepting = False
    truncated = False

    def start_step(step: ParsedStep) -> ParsedStep | None:
        nonlocal section_steps, open_step, accepting, truncated
        if len(steps) >= MAX_PARSE_STEPS:
            truncated = True
            open_step, accepting = None, False
            return None
        if section_steps == 0:
            step.chapter = cut_at_word(section, rules.MAX_CHAPTER_CHARS) if section else ""
            if section:
                chapters.append(step.chapter)
        section_steps += 1
        steps.append(step)
        open_step, accepting = step, True
        return step

    blocks = blocks_of(text)
    for block in blocks:
        if block.kind == "heading":
            if block.level == 1:
                if not title:
                    title = block.text
                continue
            if block.level >= 4:
                continue
            section = block.text
            section_skipped = section.lower().strip() in SKIPPED_SECTIONS
            section_steps = 0
            open_step, accepting = None, False
            continue
        if section_skipped:
            continue
        if block.kind == "item":
            step = ParsedStep(section=section, start_line=block.start + 1, _block_end=block.end)
            step._paragraphs.append(_item_paragraph(block.lines))
            start_step(step)
            continue
        if block.kind == "launcher":
            step = ParsedStep(launcher={"id": block.attrs["id"], "path": source_path(block.attrs.get("path")), "label": block.text},
                              section=section, start_line=block.start + 1, _block_end=block.end)
            step._paragraphs.append(launcher_instruction(block.text, block.attrs.get("path"), labels, block.attrs["id"]))
            start_step(step)
            continue
        if block.kind == "image":
            if open_step is not None and open_step.image is None and image_relative_path("x", block.attrs["path"]) is not None:
                open_step.image = block.attrs["path"]
                open_step.alt = block.text[: rules.MAX_ALT_CHARS]
            continue
        if block.kind == "quote":
            kind, body = _hint_kind(block.lines)
            if kind == KEPT_HINT:
                target = open_step or (steps[-1] if steps else None)
                if target is not None:
                    note = _note_text(body)
                    if note:
                        target.note = cut_at_sentence((target.note + "\n\n" + note).strip() if target.note else note, rules.MAX_NOTE_CHARS)
            accepting = False
            continue
        if block.kind in ("prose", "bullets", "code", "table"):
            if open_step is not None and accepting:
                paragraph = _block_paragraph(block)
                if paragraph:
                    open_step._paragraphs.append(paragraph)
            continue

    # The spans (W2c): a step runs to the last line that is not blank before the next block that starts
    # something of its own - an item, a launcher, a hint, a heading, a ``<details>``, a rule or a ``:::`` fence.
    lines = guide_lines(text)
    stops = [b.start for b in blocks if b.kind in SPAN_STOPS]
    seen: dict[str, int] = {}
    for step in steps:
        step.instruction = join_within([p.replace("\r", "").strip() for p in step._paragraphs], rules.MAX_INSTRUCTION_CHARS)
        step.text = first_sentence(step.instruction)[: rules.MAX_STEP_TEXT]
        later = bisect.bisect_right(stops, step._block_end)
        stop = stops[later] if later < len(stops) else len(lines)
        step.end_line = max(step._block_end, _last_line(lines, step._block_end, stop)) + 1
        step.key = anchor_key(step.section, step.text)
        seen[step.key] = step.ordinal = seen.get(step.key, 0) + 1
    return ParsedGuide(title=title, steps=steps, chapters=chapters, truncated=truncated)


#: The blocks a step's span ends before (W2c): each starts something of its own.
SPAN_STOPS = ("item", "launcher", "quote", "heading", "stop")
#: A section heading's own number ("2. Preview the valid rows"): left out of an anchor's key, so renumbering the
#: sections moves no anchor.
_SECTION_NUMBER = re.compile(r"^\d+[.)]\s+")


def key_text(value: str) -> str:
    """A text as an anchor compares it: its runs of white space one space, trimmed, lower case."""
    return " ".join((value or "").split()).lower()


def anchor_key(section: str, sentence: str) -> str:
    """A step's anchor key (W2c): its section's heading without its number and the step's first sentence (the
    tooltip ``first_sentence`` makes), each through :func:`key_text`, joined by " :: " - within
    ``walkthroughs.MAX_ANCHOR_KEY_CHARS``. Pure."""
    head = key_text(_SECTION_NUMBER.sub("", (section or "").strip()))[: rules.MAX_CHAPTER_CHARS]
    return f"{head} :: {key_text(sentence)[:TOOLTIP_CHARS]}"[: rules.MAX_ANCHOR_KEY_CHARS]


# -- the preview and the import plan -------------------------------------------------------------------

def preview(course: dict, workshop: dict, parsed: ParsedGuide, folder: Path) -> dict:
    """What the import would make, without writing anything: the name, the chapters, each step with its
    instruction, its text, whether a picture was found (resolving under the course), the launcher if any,
    whether a note was kept; the counts; a message when there are no steps or pictures are missing."""
    missing: list[str] = []
    rows = []
    for number, (step, in_chapter) in enumerate(zip(parsed.steps, running_chapters(parsed.steps)), start=1):
        rel = image_relative_path(workshop["slug"], step.image)
        found = resolve_image(folder, workshop["slug"], step.image) is not None if rel else False
        if rel and not found:
            missing.append(posixpath.basename(rel))
        rows.append({
            "number": number, "chapter": step.chapter, "instruction": step.instruction, "text": step.text,
            "picture": found, "image": posixpath.basename(rel) if rel else None, "alt": step.alt,
            "launcher": step.launcher, "note": bool(step.note),
            # W2e: the chapter the step is IN (the dialog's ticks; ``skip_chapters`` names these).
            "in_chapter": in_chapter,
        })
    pictures = sum(1 for r in rows if r["picture"])
    counts = {"steps": len(rows), "chapters": len(parsed.chapters), "pictures": pictures, "to_capture": len(rows) - pictures}
    messages = []
    if not rows:
        messages.append("No steps were found in this guide: the walkthrough will be empty.")
    if missing:
        messages.append(f"The guide names {len(missing)} picture{'' if len(missing) == 1 else 's'} not in the course's _assets "
                        f"({', '.join(sorted(set(missing))[:5])}{', …' if len(set(missing)) > 5 else ''}): those steps import without a picture.")
    if parsed.truncated:
        messages.append(f"Only the first {MAX_PARSE_STEPS} steps of the guide were read.")
    name = (workshop.get("title") or parsed.title or workshop["slug"]).strip()[: rules.MAX_NAME_CHARS]
    return {
        "course": {"id": course["id"], "title": course["title"], "version": course["version"]},
        "workshop": {"slug": workshop["slug"], "title": workshop.get("title") or parsed.title, "section": workshop.get("section", ""),
                     "estimatedMinutes": workshop.get("estimatedMinutes"), "stepCount": workshop.get("stepCount")},
        "name": name, "chapters": list(parsed.chapters), "steps": rows, "counts": counts,
        "message": " ".join(messages) or None,
    }


def running_chapters(steps: list[ParsedStep]) -> list[str]:
    """The chapter each step is IN (W2e): the name of the latest step at or before it that starts one - "" for the
    steps before the first heading. What the preview answers as ``in_chapter`` and what ``skip_chapters`` names. Pure."""
    out: list[str] = []
    current = ""
    for step in steps:
        current = step.chapter or current
        out.append(current)
    return out


def import_plan(course: dict, workshop: dict, parsed: ParsedGuide, folder: Path, skip_chapters=()) -> list[dict]:
    """The steps ``services.walkthroughs.create_from_workshop`` takes for one workshop: each with its picture as a
    path proven under the course (or None), its own ``source`` and its anchor. The steps of the chapters named in
    ``skip_chapters`` are left out (W2e: by the chapter's name as :func:`running_chapters` gives it - "" the steps
    before the first heading; a name the guide has no chapter of is ignored, so an edited guide gives more steps
    rather than fewer); a kept step's anchor is its place in the FULL guide, so the export still finds it. Pure."""
    skip = set(skip_chapters or ())
    steps = []
    for step, in_chapter in zip(parsed.steps, running_chapters(parsed.steps)):
        if in_chapter in skip:
            continue
        rel = image_relative_path(workshop["slug"], step.image)
        picture = resolve_image(folder, workshop["slug"], step.image) if rel else None
        steps.append({
            "chapter": step.chapter, "instruction": step.instruction, "text": step.text, "note": step.note,
            "picture": picture, "anchor": {"key": step.key, "ordinal": step.ordinal},
            "source": {"course": course["id"], "slug": workshop["slug"], "image": source_path(rel) if picture else None,
                       "alt": rules.strip_control_characters(step.alt)[: rules.MAX_ALT_CHARS],
                       "launcher": source_path((step.launcher or {}).get("path"))},
        })
    return steps


def import_source(course: dict, workshops: list[dict]) -> dict:
    """The record's ``source`` (W2e): ``{course, slug, title, version}`` as before - ``slug`` the FIRST workshop's, for
    any reader before this - and ``workshops``, ``[{slug, title}, …]`` in the course's order (``workshops`` as
    :func:`load_workshops` lists them)."""
    return {
        "course": course["id"], "slug": workshops[0]["slug"], "title": course["title"], "version": course["version"],
        "workshops": [{"slug": w["slug"], "title": (w.get("title") or w["slug"]).strip()[: rules.MAX_NAME_CHARS]} for w in workshops],
    }


def chapter_across(title: str, chapter: str) -> str:
    """A step's chapter in a walkthrough of several workshops (W2e): "<workshop title> · <section>", or the title
    alone for a step that starts no section's chapter, cut at a word to ``MAX_CHAPTER_CHARS``. Pure."""
    return cut_at_word(f"{title} · {chapter}" if chapter else title, rules.MAX_CHAPTER_CHARS)


def combine_steps(parts: list[tuple[dict, list[dict]]]) -> list[dict]:
    """The steps of the chosen workshops as ONE walkthrough's (W2e): ``parts`` is ``(workshop, its planned steps)`` in
    the course's order. With one workshop the steps are as they are; with several, a step that starts a chapter
    starts "<workshop title> · <section>" (:func:`chapter_across`) and the first step of each workshop starts a
    chapter named by the workshop when it starts none - so every workshop starts a chapter and the player's Chapters
    menu reads like the course. The parts are not changed. Pure."""
    if len(parts) <= 1:
        return [dict(step) for _workshop, steps in parts for step in steps]
    out: list[dict] = []
    for workshop, steps in parts:
        title = (workshop.get("title") or workshop["slug"]).strip()
        for index, step in enumerate(steps):
            chapter = step.get("chapter") or ""
            if chapter or index == 0:
                chapter = chapter_across(title, chapter)
            out.append({**step, "chapter": chapter})
    return out


def default_name(course: dict, slugs: list[str]) -> str:
    """The walkthrough's name when none is given (W2e, E1) - the dialog's ``defaultName`` (``lib/workshops.ts``) on
    the server, ONE rule: one workshop, its title; every workshop of the course, the course's title; every workshop
    of exactly one section (that has a title), the section's title; otherwise "<first workshop's title> + N more" -
    the first in the COURSE's order, whatever order the workshops were named in. "" for none. Pure."""
    chosen = set(slugs)
    every = [w for section in course["sections"] for w in section["workshops"]]
    picked = [w for w in every if w["slug"] in chosen]
    if not picked:
        return ""
    if len(picked) == 1:
        return picked[0]["title"]
    if len(picked) == len(every):
        return course["title"]
    for section in course["sections"]:
        own = [w["slug"] for w in section["workshops"]]
        if own and section["title"] and len(own) == len(picked) and all(slug in chosen for slug in own):
            return section["title"]
    return f"{picked[0]['title']} + {len(picked) - 1} more"
