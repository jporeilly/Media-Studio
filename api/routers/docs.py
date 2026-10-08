"""In-app documentation: the USER-FACING markdown this app ships with.

Ported from OpenSight's ``api/routers/docs.py`` — the reference implementation
(CLAUDE.md) — so that someone who knows one knows the other: nothing is
declared in the files themselves (no front matter), everything is derived. A
document's TITLE is its first ``# `` line, its SUMMARY the first line that is
not a heading, a table row or a list item, and its SECTION the folder it sits
in, through :data:`SECTIONS` and ordered by :data:`SECTION_ORDER`.

**One deliberate difference from OpenSight: this route serves a WHITELIST.**
OpenSight serves its whole ``docs/`` tree; this repo's ``docs/porting/`` is
3,700 lines of internal design journal — build phases, traps, review findings,
the owner's rulings and the paths of files on a developer's machine. It is not
documentation, it is not written for a reader outside this workflow, and it
would swamp the guides in the sidebar. So the served set is enumerated from
disk — the four root documents and the folders named in
:data:`SERVED_FOLDERS` (``docs/guides/``, ``docs/admin/``, ``docs/ai/`` and
``docs/reference/``, each a section of the Docs page) — and a request is
answered by LOOKING ITS SLUG UP IN THAT SET (:func:`_resolve`), never by
joining the slug onto a directory. A spelling that is not a served document is
refused whatever it is: there is no path for a crafted slug to walk down,
because no path is ever built from one. The shape check
(:func:`_slug_problem`) sits in front of it as the second lock, the way
``services/music.py`` checks a name before ``MUSIC_DIR`` is ever joined to it.

Three routes, as OpenSight has:

- ``GET /api/docs`` - the sections, each with its items;
- ``GET /api/docs/search?q=`` - the documents that mention ``q``, with a snippet;
- ``GET /api/docs/{slug:path}`` - one document, with its headings for the
  contents rail.

Every route needs a session and none is project-scoped: the documents are the
app's own, shared by every account like the music library, so the routes carry
no ``{pid}`` and are in the ownership sweep's exclusion list
(``tests/test_project_ownership.py``) with the reason.

Nothing is cached: the files are small, they are read per request, and an
install that updates itself with ``git pull`` (the app is a git checkout, see
``desktop/scripts/stage-app.ps1``) must never serve a document from an index
written before the pull.
"""

import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api.deps import current_user
from utils.config import APP_DIR

router = APIRouter(prefix="/docs", tags=["docs"])

DOCS_DIR = APP_DIR / "docs"
#: The folders under ``docs/`` that are served, in the order their sections
#: are listed. ``docs/porting/`` is NOT one of them (see the module docstring);
#: a new folder of guides is added here on purpose, never by being dropped on
#: disk. Each is confirmed to be that folder of ``docs/`` and not a link to
#: another (:func:`_is_own_folder`), then walked on its own, every file
#: confirmed to REALLY sit inside it (:func:`_under`).
SERVED_FOLDERS = ("guides", "admin", "ai", "reference")

#: The documents at the root of the checkout, in the order they are listed.
ROOT_DOCS = ["README.md", "INSTALL.md", "CHANGELOG.md", "VERSION.md"]
#: The ones that introduce the app, as against the ones that record it.
START_HERE = ("README.md", "INSTALL.md")

#: Folder under ``docs/`` -> the section it is listed under. One entry per
#: served folder; a folder that is served but not named here would fall back
#: to its own name, title-cased, which is the same rule OpenSight applies.
SECTIONS = {
    "guides": "Using Media Studio",
    "admin": "Administration",
    "ai": "Narration, transcription and AI",
    "reference": "Reference",
}
#: The sections in the order the Docs page lists them: the two root documents
#: that introduce the app first, the four folders, and the two root documents
#: that record it last.
SECTION_ORDER = [
    "Start here", "Using Media Studio", "Administration", "Narration, transcription and AI", "Reference", "Project",
]
#: The reading order inside each served folder, by file name relative to the
#: folder: the order the Docs page lists the guides in, and so the order its
#: Previous / Next pager walks. A file that is not named here is still served,
#: after the named ones, sorted by path - so a new guide is never hidden, and
#: ``tests/test_docs.py`` fails until it is given its place.
READING_ORDER: dict[str, tuple[str, ...]] = {
    "guides": (
        "getting-started.md", "projects.md", "capture.md", "slide-editor.md", "ai-assistant.md", "generate-video.md",
        "transcript.md", "re-voice.md", "timeline.md", "music.md", "markers-and-chapters.md", "jobs.md",
    ),
    "admin": (
        "accounts-and-roles.md", "password-policy.md", "studio-settings.md", "updates.md", "audit-log.md",
        "data-and-backup.md", "troubleshooting.md",
    ),
    "ai": ("narration-engines.md", "transcription.md", "ollama.md"),
    "reference": ("keyboard-shortcuts.md", "output-presets.md", "limits.md"),
}

#: A slug's shape: no backslash, no control character, and short enough to be a
#: path on any file system. The ``..``/``.`` segment rule is in
#: :func:`_slug_problem`, which every lookup applies.
MAX_SLUG_CHARS = 200
_SLUG_RE = re.compile(r"^[^\\\x00-\x1f\x7f]+$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

#: How far into a document the summary is looked for, and how long it may be.
_SUMMARY_LINES = 12
_SUMMARY_CHARS = 180
#: The contents rail is built from these levels.
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


# ── names and paths ───────────────────────────────────────────────────────────

def _slug_problem(slug: str) -> str | None:
    """Why ``slug`` cannot name a document, or None when it could.

    The shape rule, in ONE place, applied before anything is looked up. It does
    not decide whether the document exists — :func:`_resolve` does that, from a
    set enumerated on disk — it refuses the spellings that are trying to be a
    path rather than a name: a ``..`` segment, a backslash (a Windows filename
    check is not a name check — the E4a review's lesson), a control character,
    an absolute path and a drive letter.
    """
    if not isinstance(slug, str):
        return "A document is named by its slug."
    name = slug.strip().strip("/")
    if not name:
        return "A document is named by its slug."
    if len(name) > MAX_SLUG_CHARS:
        return f"A document's slug is limited to {MAX_SLUG_CHARS} characters."
    if not _SLUG_RE.fullmatch(name):
        return "A document's slug may not contain a backslash or a control character."
    if _DRIVE_RE.match(name):
        return "A document is named by its slug, not by a path on the server."
    if any(part in ("", ".", "..") for part in name.split("/")):
        return "A document's slug may not contain '.' or '..'."
    return None


def _under(path: Path, directory: Path) -> bool:
    """Whether ``path`` REALLY sits inside ``directory`` — links resolved.

    ``realpath`` rather than ``abspath``, on both sides, because a link is not
    a spelling and the whitelist is built from disk: a Windows **directory
    junction** at ``docs/guides/sneaky`` pointing at ``docs/porting`` needs no
    administrator to create, ``rglob`` walks straight into it, and every path
    it yields has an ``abspath`` that sits happily inside ``docs/guides``. With
    ``abspath`` here the journal was listed and served under
    ``guides/sneaky/...`` (proved, then fixed). A POSIX symlink is the same
    trick.

    Both sides go through the same call, so a checkout that is ITSELF reached
    through a link still compares equal. (``services/music.py::_path_for``
    warns against resolving a directory twice around its own creation, which is
    a different case: this one only ever runs over a directory that exists.)
    """
    root = os.path.normcase(os.path.realpath(str(directory)))
    here = os.path.normcase(os.path.realpath(str(path)))
    return here.startswith(root + os.sep)


def _is_own_folder(directory: Path, folder: str) -> bool:
    """Whether the served folder ``docs/<folder>`` REALLY is that folder of
    ``docs/`` — not itself a link to somewhere else.

    :func:`_under` compares each file with the folder it was found under,
    resolving BOTH, so it cannot see through the folder itself: with
    ``docs/admin`` a junction to ``docs/porting`` every journal file really
    does sit inside the resolved ``docs/admin``, and was listed, served and
    searchable (the D1 review's m13, proved on a temporary tree). So the
    folder is compared with where it has to be: ``realpath(DOCS_DIR)/folder``,
    both sides case-folded the way Windows compares paths.
    """
    real = os.path.normcase(os.path.realpath(str(directory)))
    expected = os.path.normcase(os.path.join(os.path.realpath(str(DOCS_DIR)), folder))
    return real == expected


def _reading_key(folder: str, name: str) -> tuple[int, int, str]:
    """Where a file sits in its folder's list: the named files in
    :data:`READING_ORDER`'s order, then every other file by its path."""
    order = READING_ORDER.get(folder, ())
    return (0, order.index(name), name) if name in order else (1, 0, name)


def _served_paths() -> dict[str, Path]:
    """Every document this route serves: ``{relative posix path: file}``, in
    listing order — the root documents as :data:`ROOT_DOCS` spells them, then
    each folder of :data:`SERVED_FOLDERS` in turn, in :data:`READING_ORDER`.

    This is the whitelist. It is built by LOOKING AT DISK, never from anything
    a caller sent. A served folder that is itself a link elsewhere is skipped
    whole (:func:`_is_own_folder`), and every guide is confirmed to REALLY sit
    inside the folder it was found under (:func:`_under`, which resolves
    links), so a junction or a symlink cannot smuggle the journal in, whether
    it is planted inside a folder or replaces one. A folder that is not on
    disk is simply an empty section.
    """
    found: dict[str, Path] = {}
    for name in ROOT_DOCS:
        path = APP_DIR / name
        if path.is_file():
            found[name] = path
    for folder in SERVED_FOLDERS:
        directory = DOCS_DIR / folder
        if not directory.is_dir() or not _is_own_folder(directory, folder):
            continue
        files = [path for path in directory.rglob("*.md") if path.is_file() and _under(path, directory)]
        for path in sorted(files, key=lambda p: _reading_key(folder, p.relative_to(directory).as_posix())):
            found[path.relative_to(APP_DIR).as_posix()] = path
    return found


def _resolve(slug: str) -> tuple[str, Path] | None:
    """The served document a slug names, as ``(relative path, file)``, or None.

    A LOOKUP, not a join: the slug is normalised to a relative path and asked
    for by key, so there is no way to spell one that reaches a file the
    whitelist does not hold. ``guides/timeline`` is accepted for
    ``docs/guides/timeline`` — the short form a help link uses — exactly as
    OpenSight accepts it, and the same short form works for every served
    folder (``admin/updates``, ``ai/ollama``, ``reference/limits``).
    """
    if _slug_problem(slug):
        return None
    key = slug.strip().strip("/")
    if not key.endswith(".md"):
        key = f"{key}.md"
    served = _served_paths()
    for candidate in (key, f"docs/{key}"):
        path = served.get(candidate)
        if path is not None:
            return candidate, path
    return None


def _slug(rel: str) -> str:
    return rel[:-3] if rel.endswith(".md") else rel


def _read(path: Path) -> str | None:
    """A document's text, or None when it cannot be read as UTF-8.

    Total on purpose: a file with a byte that is not UTF-8 (Windows Python's
    ``open()`` would otherwise decode it as cp1252 and MANUFACTURE mojibake)
    and a file the process cannot open both answer None, so one bad file in the
    folder leaves the list short rather than answering 500 to every request.
    ``UnicodeDecodeError`` is a ``ValueError``.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _title(path: Path, text: str) -> str:
    """The first ``# `` line, else the file's name in words. Nothing is
    declared in the file: OpenSight's rule, so the documents stay ordinary
    markdown that reads correctly outside the app."""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
        # This repo's README opens with a centred HTML <h1> rather than a
        # markdown one, and "Readme" is a poor name for the first thing in the
        # sidebar. The tag is read only when there is no markdown heading.
        html = re.match(r"^\s*<h1[^>]*>(.+?)</h1>", line, re.IGNORECASE)
        if html:
            return html.group(1).strip()
    return path.stem.replace("-", " ").replace("_", " ").title()


def _summary(text: str) -> str:
    """The first line that is prose: not a heading, a table row, a list item or
    a block quote, with the inline markdown taken out and cut to
    :data:`_SUMMARY_CHARS`."""
    for line in text.splitlines()[1:_SUMMARY_LINES]:
        stripped = line.strip()
        if stripped and stripped[0] not in "#|->*<":
            return re.sub(r"[*_`]", "", stripped)[:_SUMMARY_CHARS]
    return ""


def _section(rel: str) -> str:
    """Which section a document is listed under: the root documents by name,
    everything else by the folder it sits in (:data:`SECTIONS`)."""
    if rel in ROOT_DOCS:
        return "Start here" if rel in START_HERE else "Project"
    parts = rel.split("/")
    if len(parts) >= 3 and parts[0] == "docs":
        folder = parts[1]
        return SECTIONS.get(folder, folder.replace("-", " ").title())
    # Unreachable for a served path (every one is a root document or under
    # docs/<folder>/); named so that it can never land in a real section.
    return "Other"


def _headings(text: str) -> list[dict]:
    """The ``#`` to ``###`` headings, skipping fenced code — the contents rail's
    entries. The frontend derives the same list (``lib/docs.ts``) to give each
    heading its anchor; this one is what the document route reports."""
    out: list[dict] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            out.append({"level": len(match.group(1)), "text": match.group(2).strip()})
    return out


def _all_docs() -> list[dict]:
    """Every served document, as the list and the search report it: slug,
    title, section, summary, word count and relative path, sorted by section
    (:data:`SECTION_ORDER`) and then by the order :func:`_served_paths` found
    them in. A file that cannot be read is left out rather than failing the
    call."""
    items: list[dict] = []
    for position, (rel, path) in enumerate(_served_paths().items()):
        text = _read(path)
        if text is None:
            continue
        items.append({
            "slug": _slug(rel), "title": _title(path, text), "section": _section(rel),
            "summary": _summary(text), "words": len(text.split()), "path": rel, "_at": position,
        })
    order = {name: i for i, name in enumerate(SECTION_ORDER)}
    items.sort(key=lambda d: (order.get(d["section"], 99), d["_at"]))
    for item in items:
        item.pop("_at")
    return items


# ── the routes ────────────────────────────────────────────────────────────────

@router.get("")
def list_docs(user: dict = Depends(current_user)):
    """The sections, each with its documents, plus how many there are in all."""
    items = _all_docs()
    sections: dict[str, list] = {}
    for item in items:
        sections.setdefault(item["section"], []).append(item)
    return {"sections": [{"name": name, "items": docs} for name, docs in sections.items()], "count": len(items)}


@router.get("/search")
def search_docs(q: str, user: dict = Depends(current_user)):
    """The served documents that mention ``q``, most matches first, each with a
    snippet around the first hit. An empty query matches nothing rather than
    everything."""
    needle = q.lower().strip()
    if not needle:
        return []
    served = _served_paths()
    hits = []
    for item in _all_docs():
        path = served.get(item["path"])
        text = _read(path) if path is not None else None
        if text is None:
            continue
        lowered = text.lower()
        count = lowered.count(needle)
        if not count and needle not in item["title"].lower():
            continue
        at = lowered.find(needle)
        snippet = text[max(0, at - 80): at + 120].replace("\n", " ") if at >= 0 else item["summary"]
        hits.append({**item, "matches": count, "snippet": snippet})
    hits.sort(key=lambda hit: -hit["matches"])
    return hits[:30]


@router.get("/{slug:path}")
def get_doc(slug: str, user: dict = Depends(current_user)):
    """One document: its markdown and the headings the contents rail is built
    from. 400 for a slug that is not a name (a ``..`` segment, a backslash, a
    control character, an absolute path), 404 for one that names nothing this
    route serves — which is every document outside :data:`SERVED_FOLDERS` and
    the four root files, ``docs/porting/`` first among them."""
    problem = _slug_problem(slug)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    found = _resolve(slug)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No document named '{slug.strip().strip('/')}'.")
    rel, path = found
    text = _read(path)
    if text is None:
        raise HTTPException(status_code=404, detail=f"'{_slug(rel)}' is not readable as UTF-8 text.")
    return {"slug": _slug(rel), "title": _title(path, text), "section": _section(rel),
            "content": text, "headings": _headings(text), "path": rel}
