"""Place in the course (V3): the Vimeo link of a walkthrough's video written into its course workshop's guide, by
the course standard - and ``hasVideo`` into the workshop's ``manifest.json``, the Welcome tour into ``course.json``
when asked.

**The guide** (:func:`plan_guide`, pure over the text). The course standard's **Walkthrough placeholder** - a
``<figure>`` holding only ``<figcaption class="pcm-video-caption">Walkthrough: coming soon</figcaption>`` - becomes
the video figure::

    <figure>

    ![Walkthrough](https://vimeo.com/<id>/<hash>)

    <figcaption class="pcm-video-caption">Watch: <what it shows></figcaption>

    </figure>

the link on the line above the caption with blank lines either side, the caption ``Watch: …`` (the walkthrough's
name by default). An Unlisted video's link carries its hash (the content manager's player refuses the bare id);
any other video's is the bare id - a numeric id either way, so the content manager's S509 passes. When the guide
has no placeholder but already holds a Vimeo figure of OURS - a ``![Walkthrough](https://vimeo.com/…)`` line - the
link is replaced (Publish again, then place again) and its caption rewritten; when it has neither, the plan says so
(``how: "none"``) and offers the insertion after the guide's first section heading (``insert``), or the lines to
paste by hand. The guide keeps its line endings and every other byte (``course_export.split_lines`` /
``apply_edits``).

**The JSON files** are edited byte for byte (:func:`set_json_key`): one top-level key's value replaced in place -
or the key added after the last - the file's own line endings, indentation and every other byte kept. The
manifest's ``hasVideo`` becomes ``true``; ``course.json``'s ``welcome`` gains ``video`` and ``caption`` and keeps
its other keys.

**The write** goes through the export's machinery (``course_export.write_course_files``): behind the studio
setting ``courses_export`` (403 at the route), each file's sha256 as the preview showed it (409 naming the file),
every folder proven inside the course, every target checked writable, one journal for the guide, the manifest and
``course.json`` together - a failure part-way puts all three back. The plan (:func:`compute`) writes nothing.
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from services import course_export, walkthroughs, workshops
from services import projects as store
from services.course_export import apply_edits, file_eol, one_line, split_lines
from services.studio_settings import courses_export_allowed
from services.walkthrough_video import load_snapshot

PLACEHOLDER_CAPTION = "Walkthrough: coming soon"
CAPTION_CLASS = "pcm-video-caption"
ALT = "Walkthrough"
WATCH = "Watch: "
MANIFEST_FILE = "manifest.json"
COURSE_FILE = "course.json"

PLACE_OFF_MESSAGE = ("Export into the Courses folder is off: an administrator turns on \"Allow export into the Courses folder\" "
                     "in Settings › Studio. Meanwhile copy the lines into the guide by hand.")
NO_PLACEHOLDER_MESSAGE = ("The guide has no Walkthrough placeholder and no video figure of ours: tick \"Insert the figure after "
                          "the guide's first section heading\", or copy the lines into the guide by hand.")
NOT_PUBLISHED_MESSAGE = "Publish the video to Vimeo first: there is no Vimeo link to place."
NOT_FROM_COURSE_MESSAGE = ("This project was not made from a walkthrough imported from a course workshop: there is no guide to "
                           "place the link in.")
WRITE_FAILED_MESSAGE = "Could not write {path} into the course: {reason}. Nothing was changed: copy the lines into the guide by hand."
CHANGED_MESSAGE = "The file {path} changed since the preview: preview again."

_CAPTION_RE = re.compile(r'^(\s*)<figcaption class="pcm-video-caption">(.*?)</figcaption>\s*$')
_FIGURE_RE = re.compile(r"^(\s*)!\[Walkthrough\]\((https?://(?:www\.)?vimeo\.com/[^)\s]*)\)\s*$")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_SECTION_RE = re.compile(r"^## ")
_TITLE_RE = re.compile(r"^# ")
_WATCH_RE = re.compile(r"^watch\s*:\s*", re.IGNORECASE)


class NotPublished(ValueError):
    """The project has no Vimeo video yet."""


class NotFromCourse(ValueError):
    """The project's walkthrough was not imported from a course workshop."""


class NoPlaceholder(ValueError):
    """The guide has no placeholder and no figure of ours, and the insertion was not asked for."""


def changed_message(rel: str) -> str:
    """The 409's line: the export's for a guide, the file's own otherwise."""
    return course_export.changed_message(rel) if rel.endswith("/" + workshops.GUIDE_FILE) else CHANGED_MESSAGE.format(path=rel)


# -- the pure rules --------------------------------------------------------------------------------------------------

def video_link(vimeo: dict) -> str:
    """The link the course embeds: ``https://vimeo.com/<id>/<hash>`` for an Unlisted video, the bare id otherwise."""
    vid = str(vimeo.get("id") or "")
    hash_ = vimeo.get("hash")
    return f"https://vimeo.com/{vid}/{hash_}" if hash_ else f"https://vimeo.com/{vid}"


def figure_line(link: str) -> str:
    return f"![{ALT}]({link})"


def caption_text(caption: str) -> str:
    """``Watch: <what it shows>`` - the prefix once, whatever was typed; "Walkthrough" for nothing."""
    text = _WATCH_RE.sub("", one_line(caption or "")).strip()
    return WATCH + (text or ALT)


def existing_caption(text: str) -> str:
    """What the dialog edits of a caption already in the guide: its words without the ``Watch:`` prefix."""
    return _WATCH_RE.sub("", one_line(html.unescape(text or ""))).strip()


def caption_line(caption: str) -> str:
    return f'<figcaption class="{CAPTION_CLASS}">{html.escape(caption_text(caption), quote=False)}</figcaption>'


def figure_block(link: str, caption: str) -> list[str]:
    """The video figure, line by line (no endings): the course standard's form."""
    return ["<figure>", "", figure_line(link), "", caption_line(caption), "", "</figure>"]


@dataclass(frozen=True)
class Placeholder:
    start: int  # the ``<figure>`` line (0-based)
    caption: int  # the figcaption line
    end: int  # the ``</figure>`` line


def _fenced(plain: list[str]) -> list[bool]:
    """Whether each line is inside a code fence (a fence line itself counts as inside)."""
    out, inside = [], False
    for line in plain:
        if _FENCE_RE.match(line):
            inside = not inside
            out.append(True)
            continue
        out.append(inside)
    return out


def find_placeholder(plain: list[str]) -> Placeholder | None:
    """The Walkthrough placeholder in the standard's exact form: a figure holding only the caption."""
    fenced = _fenced(plain)
    for i, line in enumerate(plain):
        if fenced[i]:
            continue
        match = _CAPTION_RE.match(line)
        if not match or match.group(2).strip() != PLACEHOLDER_CAPTION:
            continue
        j = i - 1
        while j >= 0 and not plain[j].strip():
            j -= 1
        k = i + 1
        while k < len(plain) and not plain[k].strip():
            k += 1
        if j >= 0 and plain[j].strip() == "<figure>" and k < len(plain) and plain[k].strip() == "</figure>":
            return Placeholder(j, i, k)
    return None


def find_figure(plain: list[str]) -> int | None:
    """The first ``![Walkthrough](https://vimeo.com/…)`` line of ours, outside a code fence."""
    fenced = _fenced(plain)
    for i, line in enumerate(plain):
        if not fenced[i] and _FIGURE_RE.match(line):
            return i
    return None


def figure_caption_index(plain: list[str], figure: int) -> int | None:
    """The ``pcm-video-caption`` line of the figure at ``figure``: the first one before the figure closes or
    another block starts."""
    for k in range(figure + 1, len(plain)):
        stripped = plain[k].strip()
        if _CAPTION_RE.match(plain[k]):
            return k
        if stripped in ("</figure>", "<figure>") or stripped.startswith("#") or _FIGURE_RE.match(plain[k]):
            return None
    return None


def first_section_heading(plain: list[str]) -> int | None:
    """The guide's first ``## `` heading, else its ``# `` title, outside a fence; None for a guide with neither."""
    fenced = _fenced(plain)
    for pattern in (_SECTION_RE, _TITLE_RE):
        for i, line in enumerate(plain):
            if not fenced[i] and pattern.match(line):
                return i
    return None


@dataclass
class GuideEdit:
    """What the guide becomes: ``how`` is ``placeholder``, ``figure``, ``insert`` or ``none``; ``line`` the 1-based
    line of the placeholder's ``<figure>``, of the figure line or of the heading the block goes after (0 for a guide
    with no heading); ``lines`` the figure to paste by hand."""

    how: str
    line: int
    heading: str
    new_text: str
    diff: str
    existing_caption: str | None
    changed: bool
    lines: list[str]


def plan_guide(text: str, link: str, caption: str, *, insert: bool, slug: str = "guide") -> GuideEdit:
    """The guide's edit, computed over its text and nothing written (the rules in the module docstring)."""
    lines = split_lines(text)
    plain = [line for line, _ending in lines]
    eol = file_eol(lines)
    block = figure_block(link, caption)
    edits: list[tuple[int, int, list[str]]] = []
    existing: str | None = None
    heading = ""
    placeholder = find_placeholder(plain)
    if placeholder is not None:
        indent = re.match(r"[ \t]*", plain[placeholder.start]).group()
        edits.append((placeholder.start, placeholder.end + 1, course_export.indented(block, indent)))
        how, line = "placeholder", placeholder.start + 1
    else:
        figure = find_figure(plain)
        if figure is not None:
            indent = _FIGURE_RE.match(plain[figure]).group(1)
            edits.append((figure, figure + 1, [indent + figure_line(link)]))
            caption_at = figure_caption_index(plain, figure)
            if caption_at is not None:
                match = _CAPTION_RE.match(plain[caption_at])
                existing = existing_caption(match.group(2))
                edits.append((caption_at, caption_at + 1, [match.group(1) + caption_line(caption)]))
            how, line = "figure", figure + 1
        else:
            at_heading = first_section_heading(plain)
            heading = plain[at_heading] if at_heading is not None else ""
            at = at_heading + 1 if at_heading is not None else 0
            line = at
            if insert:
                after = [""] if at < len(plain) and plain[at].strip() else []
                edits.append((at, at, [""] + block + after if at_heading is not None else block + [""]))
                how = "insert"
            else:
                how = "none"
    if edits:
        new_lines = apply_edits(lines, edits, eol)
        new_text = "".join(line_text + ending for line_text, ending in new_lines)
    else:
        new_lines, new_text = lines, text
    changed = new_text != text
    diff = "\n".join(difflib.unified_diff(plain, [l for l, _e in new_lines], f"a/{slug}/guide.md", f"b/{slug}/guide.md", lineterm="")) if changed else ""
    return GuideEdit(how, line, heading, new_text, diff, existing, changed, block)


# -- the JSON edits --------------------------------------------------------------------------------------------------

def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos] in " \t\r\n":
        pos += 1
    return pos


def top_level_spans(text: str) -> tuple[int, int, dict[str, tuple[int, int]], list[str]]:
    """The object's ``{`` and ``}`` positions, each top-level key's value span and the keys in order - read with the
    standard decoder, so what it accepts is exactly what ``json.loads`` accepts. ``ValueError`` for anything else."""
    decoder = json.JSONDecoder()
    pos = _skip_ws(text, 0)
    if pos >= len(text) or text[pos] != "{":
        raise ValueError("The file is not a JSON object.")
    open_at = pos
    pos = _skip_ws(text, pos + 1)
    spans: dict[str, tuple[int, int]] = {}
    order: list[str] = []
    if pos < len(text) and text[pos] == "}":
        return open_at, pos, spans, order
    while True:
        pos = _skip_ws(text, pos)
        if pos >= len(text) or text[pos] != '"':
            raise ValueError("The file is not a JSON object.")
        key, pos = decoder.raw_decode(text, pos)
        pos = _skip_ws(text, pos)
        if pos >= len(text) or text[pos] != ":":
            raise ValueError("The file is not a JSON object.")
        pos = _skip_ws(text, pos + 1)
        start = pos
        _value, pos = decoder.raw_decode(text, pos)
        spans[str(key)] = (start, pos)
        order.append(str(key))
        pos = _skip_ws(text, pos)
        if pos < len(text) and text[pos] == ",":
            pos += 1
            continue
        if pos < len(text) and text[pos] == "}":
            return open_at, pos, spans, order
        raise ValueError("The file is not a JSON object.")


def _line_indent(text: str, at: int) -> str:
    start = text.rfind("\n", 0, at) + 1
    return re.match(r"[ \t]*", text[start:]).group()


def _dump(value, indent: str, eol: str, pretty: bool) -> str:
    if isinstance(value, (dict, list)) and value and pretty:
        lines = json.dumps(value, ensure_ascii=False, indent=2).split("\n")
        return eol.join([lines[0]] + [indent + line for line in lines[1:]])
    return json.dumps(value, ensure_ascii=False, separators=(",", ":") if not pretty else (", ", ": "))


def set_json_key(text: str, key: str, value) -> str:
    """``text`` with the top-level ``key`` set to ``value``, byte for byte otherwise: the value replaced in place
    (a dict or list laid out under the key's own indentation), or the key added after the last one; the file's own
    line endings kept. ``ValueError`` when ``text`` is not a JSON object."""
    open_at, close_at, spans, order = top_level_spans(text)
    eol = "\r\n" if "\r\n" in text else "\n"
    pretty = "\n" in text[open_at:close_at]
    if key in spans:
        start, end = spans[key]
        return text[:start] + _dump(value, _line_indent(text, start), eol, pretty) + text[end:]
    if order:
        last_end = spans[order[-1]][1]
        indent = _line_indent(text, spans[order[-1]][0])
        sep = eol + indent if pretty else ""
        return text[:last_end] + "," + sep + json.dumps(key) + (": " if pretty else ":") + _dump(value, indent, eol, pretty) + text[last_end:]
    indent = "  "
    return text[:open_at + 1] + eol + indent + json.dumps(key) + ": " + _dump(value, indent, eol, True) + eol + text[close_at:]


# -- the plan --------------------------------------------------------------------------------------------------------

def snapshot_source(record: dict) -> tuple[dict | None, str]:
    """The walkthrough's ``source`` as the project's snapshot holds it (None for a project of another kind, or a
    walkthrough not imported from a course), and the walkthrough's name."""
    if record.get("kind") != "walkthrough":
        return None, ""
    try:
        snapshot = load_snapshot(store.PROJECTS_DIR / record["id"] / record["source_filename"])
    except (ValueError, KeyError, OSError):
        return None, record.get("walkthrough_name") or ""
    rec = snapshot["record"]
    name = (rec.get("name") or snapshot.get("walkthrough_name") or record.get("walkthrough_name") or "").strip()
    source = rec.get("source")
    return (source if walkthroughs.is_workshop_source(source) else None), name


def course_of(record: dict) -> dict | None:
    """``{course, slug, title}`` for a walkthrough project from a course workshop (the FIRST workshop), else None."""
    source, _name = snapshot_source(record)
    if not source:
        return None
    return {"course": str(source["course"]), "slug": course_export.source_slugs(source)[0], "title": source.get("title") or str(source["course"])}


@dataclass
class PlacePlan:
    course_id: str
    course_title: str
    course_dir: Path
    slug: str
    workshop_title: str
    guide: Path
    guide_raw: bytes
    edit: GuideEdit
    link: str
    caption: str
    welcome: bool
    insert: bool
    manifest: Path | None = None
    manifest_raw: bytes | None = None
    manifest_new: bytes | None = None
    course_json: Path | None = None
    course_raw: bytes | None = None
    course_new: bytes | None = None

    @property
    def guide_new(self) -> bytes:
        return self.edit.new_text.encode("utf-8")

    def rel(self, path: Path) -> str:
        return path.relative_to(self.course_dir).as_posix()

    def where(self, path: Path) -> str:
        return f"{self.course_id}/{self.rel(path)}"

    def files(self) -> list[tuple[Path, bytes | None, bytes]]:
        """Every file the place touches: ``(path, its bytes now or None, its new bytes)``, the guide first."""
        out: list[tuple[Path, bytes | None, bytes]] = [(self.guide, self.guide_raw, self.guide_new)]
        if self.manifest is not None and self.manifest_new is not None:
            out.append((self.manifest, self.manifest_raw, self.manifest_new))
        if self.course_json is not None and self.course_new is not None:
            out.append((self.course_json, self.course_raw, self.course_new))
        return out

    @property
    def changed(self) -> bool:
        return any(old != new for _p, old, new in self.files())


def _sha(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data is not None else None


def compute(record: dict, *, caption: str, welcome: bool, insert: bool) -> PlacePlan:
    """The place computed whole and nothing written: the guide's edit, the manifest's and - with ``welcome`` -
    ``course.json``'s. :class:`NotPublished`, :class:`NotFromCourse`; ``workshops.NoCoursesFolder``, ``LookupError``
    and ``workshops.UnreadableCourse`` as the import answers them; ``course_export.ExportRefused`` for a manifest or a
    ``course.json`` that is not a JSON object."""
    vimeo = record.get("vimeo") or {}
    if not vimeo.get("id"):
        raise NotPublished(NOT_PUBLISHED_MESSAGE)
    source, name = snapshot_source(record)
    if not source:
        raise NotFromCourse(NOT_FROM_COURSE_MESSAGE)
    return compute_for_source(source, name, video_link(vimeo), caption=caption, welcome=welcome, insert=insert)


def compute_for_source(source: dict, name: str, link: str, *, caption: str, welcome: bool, insert: bool) -> PlacePlan:
    """:func:`compute` over a walkthrough's ``source`` and name directly - the one plan routine, which Video to course
    also asks before any project exists (where the link WOULD go, with the link it will have or a stand-in); the same
    refusals as :func:`compute` past the two it checks first."""
    course_id = str(source["course"])
    slug = course_export.source_slugs(source)[0]
    root = workshops.courses_root()
    course, parts, course_dir = workshops.load_workshops(root, course_id, [slug])
    workshop, _text = parts[0]
    guide = workshops._own_file(course_dir, f"{slug}/{workshops.GUIDE_FILE}")
    if guide is None:
        raise LookupError(f"The workshop's guide cannot be read: {slug}.")
    # The bytes, decoded here: ``read_text`` would fold CRLF into LF, and the guide keeps its own endings.
    raw = guide.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise workshops.UnreadableCourse(f"{course_id}/{slug}/{workshops.GUIDE_FILE} cannot be read as UTF-8 text.") from None
    plain = [line for line, _e in split_lines(text)]
    figure = find_figure(plain) if find_placeholder(plain) is None else None
    found = None
    if figure is not None:
        at = figure_caption_index(plain, figure)
        if at is not None:
            found = existing_caption(_CAPTION_RE.match(plain[at]).group(2))
    caption = (caption or "").strip() or found or name or ALT
    edit = plan_guide(text, link, caption, insert=insert, slug=slug)
    plan = PlacePlan(course_id, course.get("title") or course_id, course_dir, slug, workshop.get("title") or slug, guide, raw, edit,
                     link, caption, welcome, insert)

    # The manifest and course.json follow the guide: with nowhere for the link (``none``) nothing else changes either.
    manifest = workshops._own_file(course_dir, f"{slug}/{MANIFEST_FILE}")
    if manifest is not None:
        plan.manifest, plan.manifest_raw = manifest, manifest.read_bytes()
        if edit.how != "none":
            try:
                plan.manifest_new = set_json_key(plan.manifest_raw.decode("utf-8"), "hasVideo", True).encode("utf-8")
            except (ValueError, UnicodeDecodeError):
                raise course_export.ExportRefused(f"{course_id}/{slug}/{MANIFEST_FILE} is not a JSON object: nothing is written.") from None
    if welcome and edit.how != "none":
        course_json = workshops._own_file(course_dir, COURSE_FILE)
        if course_json is None:
            raise LookupError(f"The course's {COURSE_FILE} cannot be read: {course_id}.")
        plan.course_json, plan.course_raw = course_json, course_json.read_bytes()
        try:
            course_text = plan.course_raw.decode("utf-8")
            current = json.loads(course_text).get("welcome")
            new_welcome = dict(current) if isinstance(current, dict) else {}
            if not isinstance(current, dict):
                new_welcome["video"] = link
            new_welcome["caption"] = caption_text(caption)
            new_welcome["video"] = link
            plan.course_new = set_json_key(course_text, "welcome", new_welcome).encode("utf-8")
        except (ValueError, UnicodeDecodeError, AttributeError):
            raise course_export.ExportRefused(f"{course_id}/{COURSE_FILE} is not a JSON object: nothing is written.") from None
    return plan


def is_placed(plan: PlacePlan) -> bool:
    """Whether the guide already holds THIS link as the video figure of ours: the plan finds the figure and would
    change nothing (the canvas's "placed in the guide" status, derived from the course's files, never stored)."""
    return plan.edit.how == "figure" and not plan.changed


def file_actions(plan: PlacePlan) -> list[dict]:
    return [{"path": plan.rel(path), "action": "write" if old != new else "unchanged"} for path, old, new in plan.files()]


def plan_view(plan: PlacePlan) -> dict:
    """The plan as the API answers it: nothing written."""
    e = plan.edit
    return {
        "course": plan.course_id, "course_title": plan.course_title, "slug": plan.slug, "workshop": plan.workshop_title,
        "guide": plan.where(plan.guide), "how": e.how, "line": e.line, "heading": e.heading,
        "caption": plan.caption, "existing_caption": e.existing_caption, "link": plan.link, "lines": e.lines, "diff": e.diff,
        "welcome": plan.welcome, "insert": plan.insert,
        "files": file_actions(plan), "changed": plan.changed,
        "guide_sha256": _sha(plan.guide_raw), "manifest_sha256": _sha(plan.manifest_raw), "course_sha256": _sha(plan.course_raw),
        "manifest": plan.where(plan.manifest) if plan.manifest is not None else None,
        "allowed": courses_export_allowed(),
    }


def write(record: dict, *, caption: str, welcome: bool, insert: bool, guide_sha256: str, manifest_sha256: str | None,
          course_sha256: str | None) -> dict:
    """Place the link: the plan computed again, refused with :class:`NoPlaceholder` when the guide has nowhere for it
    and the insertion was not asked, each changed file written through ``course_export.write_course_files`` with its
    sha256 from the preview (:class:`course_export.GuideChanged` naming the file, nothing written). Returns what was
    written and the line for the user."""
    plan = compute(record, caption=caption, welcome=welcome, insert=insert)
    if plan.edit.how == "none":
        raise NoPlaceholder(NO_PLACEHOLDER_MESSAGE)
    given = {plan.guide: guide_sha256}
    if plan.manifest is not None:
        given[plan.manifest] = manifest_sha256
    if plan.course_json is not None:
        given[plan.course_json] = course_sha256
    files = [(path, new) for path, old, new in plan.files() if old != new]
    written = course_export.write_course_files(plan.course_dir, plan.course_id, files, given, failed_message=WRITE_FAILED_MESSAGE,
                                               changed_message_of=changed_message)
    where = plan.where(plan.guide)
    if written:
        message = f"Placed the video in {where}. Review and publish it in the Content Editor."
    else:
        message = f"Nothing to write: {where} already holds this video."
    return {"course": plan.course_id, "slug": plan.slug, "guide": where, "link": plan.link, "written": written,
            "welcome": bool(plan.course_json is not None and plan.rel(plan.course_json) in written), "message": message}


def audit_detail(result: dict, vimeo_id: str) -> str:
    """"<course>/<slug>: vimeo <id>", ", welcome tour" when course.json took it."""
    return f"{result['course']}/{result['slug']}: vimeo {vimeo_id}" + (", welcome tour" if result.get("welcome") else "")
