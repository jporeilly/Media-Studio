"""The in-app documentation routes (``api/routers/docs.py``).

What these tests hold in place:

- **``docs/porting/`` is not reachable.** It is 3,700 lines of internal design
  journal - build phases, traps, review findings, the owner's rulings and the
  paths of files on a developer's machine - and the route serves a WHITELIST
  (the four root documents and the folders of ``docs.SERVED_FOLDERS``:
  ``docs/guides/``, ``docs/admin/``, ``docs/ai/`` and ``docs/reference/``)
  rather than a directory, so a crafted slug has no path to walk down. Every
  spelling anyone here could think of is tried, over HTTP and against the
  resolver itself, against a fake tree AND against the real repository;
- the list derives everything from the files (title, summary, section), each
  served folder is a section in the order the Docs page lists them, the files
  of a folder come in ``docs.READING_ORDER`` (the rest after, by path), and a
  file that cannot be read leaves the list short rather than failing it;
- a served folder that is itself a link elsewhere serves nothing;
- a document comes back with the headings the contents rail is built from;
- in the real repository every served document has a title and a whole
  summary, every guide has its place in the reading order, every relative or
  ``/``-rooted ``.md`` link resolves to another served document, and the
  search finds each guide;
- every route needs a session.

The fake tree is a ``tmp_path`` with the same SHAPE as the repo - four root
documents, a document in each served folder and a ``docs/porting/`` full of
secrets - so the refusals are tested against a porting file that really is
there to be found.
"""

import os
import posixpath
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from api.routers import docs
from services import projects as store
from utils.config import config

#: What a leak would put in the response body.
SECRET = "INTERNAL-PORTING-MATERIAL"

#: Spellings that must never reach a document outside the served set. Tried
#: over HTTP where a client could really send them, and against ``_resolve``
#: for the ones httpx would refuse to put in a URL.
TRAVERSALS = [
    "..",
    "../porting/edit-timeline",
    "../../porting/edit-timeline",
    "..%2Fporting%2Fedit-timeline",
    "..%2f..%2fporting%2fedit-timeline",
    "%2e%2e/porting/edit-timeline",
    "..\\porting\\edit-timeline",
    "docs\\porting\\edit-timeline",
    "docs/porting/edit-timeline",
    "docs/porting/edit-timeline.md",
    "porting/edit-timeline",
    "porting/edit-timeline.md",
    "./docs/porting/edit-timeline",
    "docs/./porting/edit-timeline",
    "docs/guides/../porting/edit-timeline",
    "docs//porting/edit-timeline",
    "/docs/porting/edit-timeline",
    "DOCS/PORTING/EDIT-TIMELINE",
    "docs/porting/edit-timeline#",
]
#: The same question asked of the resolver, with the spellings a URL cannot
#: carry: a NUL and a control character, an absolute path and a drive letter.
RESOLVER_ONLY = [
    "",
    "   ",
    "/",
    "docs/porting/edit\x00-timeline",
    "docs/porting/\x1fedit-timeline",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\secret.md",
    "../../../../../../Windows/win.ini",
    "x" * 300,
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A fake checkout with the repo's shape, pointed at by the router."""
    root = tmp_path / "app"
    (root / "docs" / "guides").mkdir(parents=True)
    (root / "docs" / "porting").mkdir(parents=True)

    (root / "README.md").write_text(
        "<h1 align=\"center\">Media Studio Enterprise</h1>\n\nTurns decks into narrated MP4s.\n", encoding="utf-8")
    (root / "INSTALL.md").write_text("# Install\n\nOne Windows installer.\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text("# Changelog\n\nEvery release.\n", encoding="utf-8")
    (root / "VERSION.md").write_text("# Version\n\n**Current version:** 0.9.0\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Not served\n\nThe agent workflow.\n", encoding="utf-8")

    (root / "docs" / "guides" / "timeline.md").write_text(
        "# The Timeline\n\n| Lane | What |\n| --- | --- |\n| Video | frames |\n\n"
        "The Timeline is where a video project is edited.\n\n"
        "## The four lanes\n\n```\n## not a heading, it is fenced\n```\n\n### Music\n\nThe fourth lane.\n",
        encoding="utf-8")
    # No heading at all: the title falls back to the file's own name.
    (root / "docs" / "guides" / "no-heading.md").write_text("Just a paragraph, nothing declared.\n", encoding="utf-8")
    # Not markdown: never served, never listed.
    (root / "docs" / "guides" / "notes.txt").write_text("not a document\n", encoding="utf-8")
    # Not UTF-8: it must not 500 the list, and it must not be decoded as cp1252
    # into mojibake either (the Windows `open()` trap).
    (root / "docs" / "guides" / "broken.md").write_bytes(b"# Broken\n\n\xff\xfe not utf-8 \x81\n")

    # One document in each of the other served folders: each is a section.
    for folder, name, title, lead in (
        ("admin", "users", "Users", "Who may do what."),
        ("ai", "voices", "Voices", "The narration engines."),
        ("reference", "keys", "Keys", "Every key."),
    ):
        (root / "docs" / folder).mkdir(parents=True)
        (root / "docs" / folder / f"{name}.md").write_text(f"# {title}\n\n{lead}\n", encoding="utf-8")

    (root / "docs" / "porting" / "edit-timeline.md").write_text(
        f"# Edit timeline (internal)\n\n{SECRET}: trap 19, the owner's ruling, C:\\Projects\\...\n", encoding="utf-8")
    (root / "docs" / "porting" / "narration-timeline.md").write_text(f"# Internal\n\n{SECRET}\n", encoding="utf-8")
    # A document one level OUTSIDE the checkout: the classic `..` target.
    (tmp_path / "outside.md").write_text(f"# Outside\n\n{SECRET}\n", encoding="utf-8")

    monkeypatch.setattr(docs, "APP_DIR", root)
    monkeypatch.setattr(docs, "DOCS_DIR", root / "docs")
    # No reading order: the fake folders list by path, so the tests below do
    # not depend on the real guides' order (which has tests of its own).
    monkeypatch.setattr(docs, "READING_ORDER", {})
    return root


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _items(payload: dict) -> list[dict]:
    return [item for section in payload["sections"] for item in section["items"]]


def _served_path(path: str) -> bool:
    """Whether ``path`` is one the whitelist may hold: a root document, or a
    file under one of the served folders."""
    return path in docs.ROOT_DOCS or any(path.startswith(f"docs/{folder}/") for folder in docs.SERVED_FOLDERS)


#: The sections, in the order the Docs page lists them (the D1 brief, §1).
EXPECTED_SECTIONS = [
    "Start here", "Using Media Studio", "Administration", "Narration, transcription and AI", "Reference", "Project",
]


# ── the sections ──────────────────────────────────────────────────────────────

def test_every_served_folder_is_a_section_and_the_order_is_the_briefs():
    assert docs.SERVED_FOLDERS == ("guides", "admin", "ai", "reference")
    assert docs.SECTIONS == {
        "guides": "Using Media Studio",
        "admin": "Administration",
        "ai": "Narration, transcription and AI",
        "reference": "Reference",
    }
    assert docs.SECTION_ORDER == EXPECTED_SECTIONS
    # Every folder's section has a place in the order, so none falls to the end at 99.
    assert all(docs.SECTIONS[folder] in docs.SECTION_ORDER for folder in docs.SERVED_FOLDERS)
    assert "porting" not in docs.SERVED_FOLDERS
    # Every served folder has a reading order, and nothing else does.
    assert set(docs.READING_ORDER) == set(docs.SERVED_FOLDERS)
    # A path that is neither a root document nor under docs/<folder>/ cannot
    # be served; if one ever were, it would not land in a real section.
    assert docs._section("stray.md") == "Other" and "Other" not in docs.SECTION_ORDER


# ── the list ──────────────────────────────────────────────────────────────────

def test_the_list_derives_title_section_and_order_from_the_files(client, tree):
    r = client.get("/api/docs")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert [s["name"] for s in payload["sections"]] == EXPECTED_SECTIONS
    assert [(i["section"], i["slug"], i["title"]) for i in _items(payload)] == [
        ("Start here", "README", "Media Studio Enterprise"),
        ("Start here", "INSTALL", "Install"),
        ("Using Media Studio", "docs/guides/no-heading", "No Heading"),
        ("Using Media Studio", "docs/guides/timeline", "The Timeline"),
        ("Administration", "docs/admin/users", "Users"),
        ("Narration, transcription and AI", "docs/ai/voices", "Voices"),
        ("Reference", "docs/reference/keys", "Keys"),
        ("Project", "CHANGELOG", "Changelog"),
        ("Project", "VERSION", "Version"),
    ], "the root documents in ROOT_DOCS order, each folder sorted, the sections in SECTION_ORDER"
    assert payload["count"] == 9


def test_a_folder_lists_its_named_files_in_reading_order_and_the_rest_after_by_path(client, tree, monkeypatch):
    """``docs.READING_ORDER`` puts the files it names first, in its order; a
    file it does not name is still served, after them, sorted by path, and a
    name with no file behind it is simply skipped."""
    for name in ("a-later.md", "zeta.md"):
        (tree / "docs" / "guides" / name).write_text(f"# {name}\n\nA guide.\n", encoding="utf-8")
    monkeypatch.setattr(docs, "READING_ORDER", {"guides": ("zeta.md", "not-there.md", "timeline.md")})
    guides = [i["slug"] for i in _items(client.get("/api/docs").json()) if i["slug"].startswith("docs/guides/")]
    assert guides == ["docs/guides/zeta", "docs/guides/timeline", "docs/guides/a-later", "docs/guides/no-heading"]
    assert [p for p in docs._served_paths() if p.startswith("docs/guides/")] == [
        "docs/guides/zeta.md", "docs/guides/timeline.md",
        "docs/guides/a-later.md", "docs/guides/broken.md", "docs/guides/no-heading.md",
    ], "the unreadable file is still in the whitelist, in its place by path; the list leaves it out"


def test_the_summary_is_the_first_line_that_is_prose(client, tree):
    found = {i["slug"]: i["summary"] for i in _items(client.get("/api/docs").json())}
    # The heading, the table's rows and its separator are all skipped.
    assert found["docs/guides/timeline"] == "The Timeline is where a video project is edited."
    assert found["README"] == "Turns decks into narrated MP4s."
    assert found["docs/guides/no-heading"] == "", "nothing after the first line is nothing to summarise"


def test_a_file_that_cannot_be_read_leaves_the_list_short_rather_than_failing_it(client, tree):
    payload = client.get("/api/docs").json()
    slugs = [i["slug"] for i in _items(payload)]
    assert "docs/guides/broken" not in slugs, "a file that is not UTF-8 is left out"
    assert "docs/guides/notes" not in slugs, "only .md is a document"
    assert "CLAUDE" not in slugs, "only the four ROOT_DOCS are served from the root"
    assert client.get("/api/docs").status_code == 200, "and the list itself still answers"


def test_the_word_count_is_the_document_s_own(client, tree):
    found = {i["slug"]: i["words"] for i in _items(client.get("/api/docs").json())}
    assert found["docs/guides/no-heading"] == 5
    assert found["docs/guides/timeline"] > 20


# ── one document ──────────────────────────────────────────────────────────────

def test_a_document_comes_back_with_its_headings_for_the_contents_rail(client, tree):
    r = client.get("/api/docs/docs/guides/timeline")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["slug"], body["title"], body["section"], body["path"]) == (
        "docs/guides/timeline", "The Timeline", "Using Media Studio", "docs/guides/timeline.md")
    assert body["content"].startswith("# The Timeline")
    assert body["headings"] == [
        {"level": 1, "text": "The Timeline"},
        {"level": 2, "text": "The four lanes"},
        {"level": 3, "text": "Music"},
    ], "a # inside a fenced block is code, not a heading"


def test_the_short_form_without_the_docs_prefix_reaches_the_same_document(client, tree):
    short = client.get("/api/docs/guides/timeline")
    assert short.status_code == 200, short.text
    assert short.json()["slug"] == "docs/guides/timeline"
    # With or without the extension, and with stray slashes around it.
    assert client.get("/api/docs/docs/guides/timeline.md").status_code == 200
    assert client.get("/api/docs/guides/timeline/").status_code == 200


@pytest.mark.parametrize("short, slug, section", [
    ("admin/users", "docs/admin/users", "Administration"),
    ("ai/voices", "docs/ai/voices", "Narration, transcription and AI"),
    ("reference/keys", "docs/reference/keys", "Reference"),
])
def test_the_short_form_works_for_every_served_folder(client, tree, short, slug, section):
    r = client.get(f"/api/docs/{short}")
    assert r.status_code == 200, r.text
    assert (r.json()["slug"], r.json()["section"]) == (slug, section)
    assert client.get(f"/api/docs/{slug}").json()["slug"] == slug


def test_an_unknown_slug_is_a_404_that_names_it(client, tree):
    r = client.get("/api/docs/guides/does-not-exist")
    assert r.status_code == 404
    assert "does-not-exist" in r.json()["detail"]


def test_a_document_that_is_not_utf8_is_a_404_rather_than_a_500(client, tree):
    r = client.get("/api/docs/guides/broken")
    assert r.status_code == 404
    assert "UTF-8" in r.json()["detail"]


# ── the search ────────────────────────────────────────────────────────────────

def test_the_search_finds_a_guide_and_never_an_internal_document(client, tree):
    hits = client.get("/api/docs/search", params={"q": "lane"}).json()
    assert [h["slug"] for h in hits] == ["docs/guides/timeline"]
    assert hits[0]["matches"] >= 2 and "lane" in hits[0]["snippet"].lower()
    # A word that appears ONLY in docs/porting matches nothing at all.
    assert client.get("/api/docs/search", params={"q": SECRET}).json() == []
    assert client.get("/api/docs/search", params={"q": "trap 19"}).json() == []


def test_an_empty_search_matches_nothing(client, tree):
    assert client.get("/api/docs/search", params={"q": ""}).json() == []
    assert client.get("/api/docs/search", params={"q": "   "}).json() == []


# ── the session ───────────────────────────────────────────────────────────────

def test_every_route_needs_a_session(client, tree):
    from api.app import app

    anonymous = TestClient(app)
    assert anonymous.get("/api/docs").status_code == 401
    assert anonymous.get("/api/docs/search", params={"q": "lane"}).status_code == 401
    assert anonymous.get("/api/docs/docs/guides/timeline").status_code == 401


# ── the porting folder is not reachable ───────────────────────────────────────

def test_nothing_outside_the_served_set_is_ever_listed(client, tree):
    for item in _items(client.get("/api/docs").json()):
        assert _served_path(item["path"]), item["path"]
    assert not any("porting" in item["path"] for item in _items(client.get("/api/docs").json()))


def test_a_folder_that_is_not_whitelisted_is_never_served(client, tree):
    """A folder dropped under ``docs/`` is not a section by being there: the
    whitelist names its folders on purpose (``docs.SERVED_FOLDERS``)."""
    (tree / "docs" / "drafts").mkdir()
    (tree / "docs" / "drafts" / "plan.md").write_text(f"# Plan\n\n{SECRET}\n", encoding="utf-8")
    assert all("drafts" not in item["path"] for item in _items(client.get("/api/docs").json()))
    assert docs._resolve("drafts/plan") is None and docs._resolve("docs/drafts/plan") is None
    r = client.get("/api/docs/docs/drafts/plan")
    assert r.status_code == 404 and SECRET not in r.text
    assert client.get("/api/docs/search", params={"q": SECRET}).json() == []


def _document(r) -> dict | None:
    """The document a response carries, or None when it is not one.

    A URL holding ``..`` is normalised by the CLIENT before it is sent - httpx
    does it, and so does every browser - so such a request never reaches the
    docs route at all: it lands on the SPA catch-all and comes back as
    ``index.html``. That is a 200 and it is not a leak, so the invariant these
    tests assert is "no spelling ever answers with a document from outside the
    served set", not "no spelling ever answers 200".
    ``test_the_route_function_refuses_every_traversal_spelling`` is what proves
    the SERVER refuses the dot segments as well, for a client that does not
    normalise them away.
    """
    if r.status_code != 200 or "json" not in r.headers.get("content-type", ""):
        return None
    body = r.json()
    return body if isinstance(body, dict) and "content" in body else None


@pytest.mark.parametrize("slug", TRAVERSALS)
def test_no_spelling_of_a_slug_reaches_the_porting_folder_over_http(client, tree, slug):
    r = client.get(f"/api/docs/{slug}")
    assert SECRET not in r.text, f"{slug!r} leaked internal material"
    served = _document(r)
    assert served is None or served["path"].startswith("docs/guides/") or served["path"] in docs.ROOT_DOCS, \
        f"{slug!r} was SERVED: {served['path'] if served else ''}"
    if r.status_code != 200:
        assert r.status_code in (400, 404, 405), f"{slug!r} -> {r.status_code}"


@pytest.mark.parametrize("slug", TRAVERSALS + RESOLVER_ONLY)
def test_the_route_function_refuses_every_traversal_spelling(tree, slug):
    """The route itself, with no client in the way to normalise a ``..``
    first - so this is the one that sees the dot segments arrive."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        docs.get_doc(slug, user={"id": "u", "role": "admin"})
    assert caught.value.status_code in (400, 404), f"{slug!r} -> {caught.value.status_code}"


@pytest.mark.parametrize("slug", TRAVERSALS + RESOLVER_ONLY)
def test_the_resolver_itself_refuses_every_spelling(tree, slug):
    assert docs._resolve(slug) is None, f"{slug!r} resolved to {docs._resolve(slug)}"


def test_the_resolver_answers_the_documents_it_does_serve(tree):
    assert docs._resolve("docs/guides/timeline")[0] == "docs/guides/timeline.md"
    assert docs._resolve("guides/timeline")[0] == "docs/guides/timeline.md"
    assert docs._resolve("README")[0] == "README.md"
    assert docs._resolve("README.md")[0] == "README.md"
    assert docs._resolve("admin/users")[0] == "docs/admin/users.md"
    assert docs._resolve("ai/voices.md")[0] == "docs/ai/voices.md"
    assert docs._resolve("docs/reference/keys")[0] == "docs/reference/keys.md"


def test_the_slug_rule_names_what_is_wrong_with_a_slug():
    assert docs._slug_problem("docs/guides/timeline") is None
    assert docs._slug_problem("README.md") is None
    assert "'.' or '..'" in docs._slug_problem("../porting/x")
    assert "'.' or '..'" in docs._slug_problem("docs/./porting/x")
    assert "backslash" in docs._slug_problem("docs\\porting\\x")
    assert "backslash" in docs._slug_problem("docs/porting/x\x00")
    assert "path on the server" in docs._slug_problem("C:/Windows/win.ini")
    assert docs._slug_problem("") and docs._slug_problem("   ") and docs._slug_problem("/")
    assert docs._slug_problem(None) and docs._slug_problem("x" * 300)


def test_a_symlinked_guide_pointing_out_of_the_folder_is_not_served(tree, tmp_path):
    """The whitelist is built from disk, so a link planted in ``docs/guides/``
    is the one way a file from elsewhere could join it. It does not."""
    link = tree / "docs" / "guides" / "leak.md"
    try:
        link.symlink_to(tmp_path / "outside.md")
    except (OSError, NotImplementedError):  # Windows without developer mode
        pytest.skip("this account cannot create a symlink")
    assert docs._resolve("guides/leak") is None
    assert "docs/guides/leak.md" not in docs._served_paths()


def _link_directory(link: Path, target: Path) -> bool:
    """Point ``link`` at the directory ``target``: a **junction** on Windows,
    which needs no administrator and is the reason this test exists, and a
    symlink everywhere else. False when neither can be made."""
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                              capture_output=True, text=True)
        return made.returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


def test_a_directory_junction_into_the_porting_folder_serves_nothing(tree):
    """A LINK is not a spelling, and the whitelist is built by walking disk.

    A Windows directory junction at ``docs/guides/sneaky`` pointing at
    ``docs/porting`` needs no administrator, ``rglob`` walks into it, and every
    path it yields has an ``abspath`` inside ``docs/guides`` - so with
    ``abspath`` in :func:`docs._under` the journal really was listed and served
    under ``guides/sneaky/...``. That was proved here before it was fixed; the
    check is ``realpath`` now.
    """
    link = tree / "docs" / "guides" / "sneaky"
    if not _link_directory(link, tree / "docs" / "porting"):
        pytest.skip("this account cannot link a directory")
    try:
        assert list(docs._served_paths()) == ["README.md", "INSTALL.md", "CHANGELOG.md", "VERSION.md",
                                              "docs/guides/broken.md", "docs/guides/no-heading.md",
                                              "docs/guides/timeline.md", "docs/admin/users.md",
                                              "docs/ai/voices.md", "docs/reference/keys.md"]
        assert docs._resolve("guides/sneaky/edit-timeline") is None
        assert docs._resolve("docs/guides/sneaky/edit-timeline.md") is None
        assert all("sneaky" not in item["path"] for item in docs._all_docs())
    finally:
        # Remove the JUNCTION, never what it points at.
        link.rmdir()


@pytest.mark.parametrize("folder", ["admin", "ai", "reference"])
def test_a_directory_junction_in_any_served_folder_serves_nothing(tree, folder):
    """The realpath check is made per folder, against the folder the file was
    found under: a junction into ``docs/porting`` planted in any of them is
    walked by ``rglob`` and refused file by file."""
    link = tree / "docs" / folder / "sneaky"
    if not _link_directory(link, tree / "docs" / "porting"):
        pytest.skip("this account cannot link a directory")
    try:
        assert all("sneaky" not in path and "porting" not in path for path in docs._served_paths())
        assert docs._resolve(f"{folder}/sneaky/edit-timeline") is None
        assert docs._resolve(f"docs/{folder}/sneaky/edit-timeline.md") is None
    finally:
        link.rmdir()


@pytest.mark.parametrize("target", ["porting", "outside"])
@pytest.mark.parametrize("folder", ["guides", "admin", "ai", "reference"])
def test_a_served_folder_that_is_itself_a_junction_serves_nothing(client, tree, tmp_path, folder, target):
    """The folder ITSELF replaced by a link (the D1 review's m13). The
    per-file check resolves the folder too, so with ``docs/admin`` a junction
    to ``docs/porting`` every journal file really does sit inside the
    resolved ``docs/admin`` - it was listed, served and searchable. The
    folder has to BE ``docs/<folder>``, so the whole folder is skipped: a
    link into the journal or out of the checkout lists, resolves, serves and
    finds nothing under it."""
    if target == "porting":
        destination = tree / "docs" / "porting"
    else:
        destination = tmp_path / "stash"
        destination.mkdir()
        (destination / "edit-timeline.md").write_text(f"# Stash\n\n{SECRET}\n", encoding="utf-8")
    folder_path = tree / "docs" / folder
    for path in sorted(folder_path.iterdir()):
        path.unlink()
    folder_path.rmdir()
    if not _link_directory(folder_path, destination):
        pytest.skip("this account cannot link a directory")
    try:
        assert [path for path in docs._served_paths() if path.startswith(f"docs/{folder}/")] == []
        listed = _items(client.get("/api/docs").json())
        assert not any(item["path"].startswith(f"docs/{folder}/") for item in listed)
        assert SECRET not in client.get("/api/docs").text
        assert docs._resolve(f"{folder}/edit-timeline") is None
        assert docs._resolve(f"docs/{folder}/edit-timeline.md") is None
        for slug in (f"{folder}/edit-timeline", f"docs/{folder}/edit-timeline"):
            r = client.get(f"/api/docs/{slug}")
            assert r.status_code == 404 and SECRET not in r.text, f"{slug} -> {r.status_code}"
        assert client.get("/api/docs/search", params={"q": SECRET}).json() == []
        # The other folders are untouched: only the linked one is skipped.
        assert any(path.startswith("docs/") and not path.startswith(f"docs/{folder}/") for path in docs._served_paths())
    finally:
        # Remove the LINK, never what it points at (a junction goes with
        # rmdir; a POSIX symlink is a file to unlink).
        if folder_path.is_symlink():
            folder_path.unlink()
        else:
            folder_path.rmdir()


# ── the real repository, not a fake tree ──────────────────────────────────────

def test_the_real_checkout_serves_the_guides_and_not_the_porting_journal(client):
    """No fixture: the router pointed at the repository it ships in."""
    payload = client.get("/api/docs").json()
    paths = [item["path"] for item in _items(payload)]
    assert "docs/guides/timeline.md" in paths, "the Timeline guide is served"
    assert not any(p.startswith("docs/porting/") for p in paths), paths
    for item in _items(payload):
        assert _served_path(item["path"]), item["path"]


@pytest.mark.parametrize("slug", TRAVERSALS)
def test_the_real_porting_documents_are_unreachable_too(client, slug):
    r = client.get(f"/api/docs/{slug}")
    # The porting documents' own words, not a marker planted by a fixture.
    assert "trap 19" not in r.text and "porting vertical" not in r.text
    served = _document(r)
    assert served is None or served["path"].startswith("docs/guides/") or served["path"] in docs.ROOT_DOCS, \
        f"{slug!r} was SERVED from the real tree: {served['path'] if served else ''}"


def test_the_real_timeline_guide_says_what_the_code_does(client):
    """The document is user-facing now, so the two rulings that are easiest to
    get backwards are pinned here as well as in the component's own tests."""
    body = client.get("/api/docs/guides/timeline").json()
    text = body["content"]
    assert body["title"] == "The Timeline"
    assert len([h for h in body["headings"] if h["level"] == 2]) >= 8, "enough headings for the contents rail"
    assert "rides the picture" in text
    assert re.search(r"disabled when Music is the only unlocked lane", text)
    assert "only selects it" in text and "unlike a sentence block" in text


def test_the_real_sections_are_all_listed_in_order(client):
    """Every served folder has guides in the repository, so the Docs page lists
    all six sections, in the brief's order."""
    payload = client.get("/api/docs").json()
    assert [section["name"] for section in payload["sections"]] == EXPECTED_SECTIONS
    assert payload["count"] == len(_items(payload))


#: The reading order the Docs page lists, section by section (the D1 review,
#: lens 8): Getting started first, the Timeline before the lanes it describes.
EXPECTED_READING = {
    "Start here": ["README", "INSTALL"],
    "Using Media Studio": [f"docs/guides/{name}" for name in (
        "getting-started", "projects", "slide-editor", "ai-assistant", "generate-video", "transcript",
        "re-voice", "timeline", "music", "markers-and-chapters", "jobs")],
    "Administration": [f"docs/admin/{name}" for name in (
        "accounts-and-roles", "password-policy", "studio-settings", "updates", "audit-log",
        "data-and-backup", "troubleshooting")],
    "Narration, transcription and AI": [f"docs/ai/{name}" for name in ("narration-engines", "transcription", "ollama")],
    "Reference": [f"docs/reference/{name}" for name in ("keyboard-shortcuts", "output-presets", "limits")],
    "Project": ["CHANGELOG", "VERSION"],
}


def test_the_real_sections_list_their_documents_in_reading_order(client):
    payload = client.get("/api/docs").json()
    assert {section["name"]: [item["slug"] for item in section["items"]] for section in payload["sections"]} \
        == EXPECTED_READING
    assert list(EXPECTED_READING) == EXPECTED_SECTIONS


def test_every_real_guide_has_its_place_in_the_reading_order():
    """A guide added to a served folder is still served (after the named ones),
    but it fails here until it is given its place; a name whose file has gone
    fails here too."""
    served = [rel for rel in docs._served_paths() if rel.startswith("docs/")]
    for rel in served:
        _, folder, name = rel.split("/", 2)
        assert name in docs.READING_ORDER[folder], f"{rel} has no place in READING_ORDER[{folder!r}]"
    for folder, names in docs.READING_ORDER.items():
        for name in names:
            assert f"docs/{folder}/{name}" in served, f"READING_ORDER[{folder!r}] names {name}, which is not served"


def test_every_real_document_has_a_title_and_a_summary(client):
    """The page shows a document by its title and its summary - the first
    ``# `` line (or the README's HTML ``<h1>``) and the first line of prose -
    so a document without either would be listed as its file name with
    nothing under it. The title must be one the document declares, not the
    fallback made from its file name."""
    for item in _items(client.get("/api/docs").json()):
        text = (docs.APP_DIR / item["path"]).read_text(encoding="utf-8")
        declared = any(
            line == f"# {item['title']}" or re.match(rf"^\s*<h1[^>]*>{re.escape(item['title'])}</h1>", line)
            for line in text.splitlines()
        )
        assert item["title"] and declared, f"{item['path']}: no title of its own ({item['title']!r})"
        assert item["summary"].strip(), f"{item['path']}: no summary - the first prose line is missing"
        # The whole lead, not a cut: the page shows at most docs._SUMMARY_CHARS
        # of it, so a longer lead would end mid-word under the title.
        lead = next(
            re.sub(r"[*_`]", "", line.strip()) for line in text.splitlines()[1:docs._SUMMARY_LINES]
            if line.strip() and line.strip()[0] not in "#|->*<"
        )
        assert lead == item["summary"], \
            f"{item['path']}: the lead is {len(lead)} characters; the page cuts it at {docs._SUMMARY_CHARS}"


# ── the links between documents ───────────────────────────────────────────────

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_INLINE_CODE_RE = re.compile(r"`[^`]*`")
#: ``[text](target)`` and ``[text](<target> "title")``, not an image's ``![alt](...)``.
_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
#: A reference definition: ``[label]: target``.
_REF_RE = re.compile(r"^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?")


def _markdown_links(text: str) -> list[tuple[int, str]]:
    """Every link target in a document with its line number, skipping fenced
    code and inline code - a ``[x](y.md)`` inside backticks is shown, not
    followed, by the Docs page."""
    found: list[tuple[int, str]] = []
    fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_RE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        bare = _INLINE_CODE_RE.sub("", line)
        found += [(number, match.group(1)) for match in _LINK_RE.finditer(bare)]
        ref = _REF_RE.match(bare)
        if ref:
            found.append((number, ref.group(1)))
    return found


def _broken_links(served: dict[str, Path]) -> list[str]:
    """Every ``.md`` link in a served document that does not resolve to a
    served document, as ``path:line: href -> resolved``.

    Resolved the way the Docs page resolves one (``frontend/src/lib/docs.ts``,
    ``resolveDocLink``), the ``#anchor`` and any ``?query`` dropped: a relative
    link against the folder of the document it is written in, a ``/``-rooted
    one as a slug the document route looks up (with or without the ``docs/``
    prefix, as ``docs._resolve`` accepts it). An external link (a scheme or
    ``//``), an anchor within the page and a link to anything that is not
    ``.md`` are not this check's business: the page does not follow those as
    documents."""
    problems: list[str] = []
    for rel, path in served.items():
        text = docs._read(path)
        if text is None:
            continue
        for number, href in _markdown_links(text):
            if re.match(r"^[a-z][a-z0-9+.-]*:", href, re.IGNORECASE) or href.startswith(("#", "//")):
                continue
            target = unquote(href.split("#", 1)[0].split("?", 1)[0])
            if not target.lower().endswith(".md"):
                continue
            if target.startswith("/"):
                key = target.lstrip("/")[:-3] + ".md"
                if key not in served and f"docs/{key}" not in served:
                    problems.append(f"{rel}:{number}: {href} -> {key}")
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(rel), target))
            if resolved not in served:
                problems.append(f"{rel}:{number}: {href} -> {resolved}")
    return problems


def test_the_link_check_finds_a_broken_link_and_only_that(tree):
    """The checker itself, on the fake tree: good links of every shape pass,
    and the two that go nowhere served - a file that is not there, and the
    porting journal - are named with their lines."""
    (tree / "docs" / "guides" / "links.md").write_text(
        "# Links\n\n"
        "[the timeline](timeline.md), [install](../../INSTALL.md#install), [users](../admin/users.md)\n"
        "[keys](<../reference/keys.md> \"Every key\"), [an anchor](#links), [a site](https://example.com/x.md)\n"
        "[a picture](shot.png), `[in code](nowhere.md)`, ![an image](nowhere.md)\n"
        "```\n[fenced](nowhere.md)\n```\n"
        "[gone](missing.md)\n"
        "[the journal](../porting/edit-timeline.md)\n"
        "[rooted](/docs/guides/timeline.md), [rooted short](/guides/timeline.md#music), [readme](/README.md)\n"
        "[rooted journal](/docs/porting/edit-timeline.md)\n",
        encoding="utf-8")
    assert _broken_links(docs._served_paths()) == [
        "docs/guides/links.md:9: missing.md -> docs/guides/missing.md",
        "docs/guides/links.md:10: ../porting/edit-timeline.md -> docs/porting/edit-timeline.md",
        "docs/guides/links.md:12: /docs/porting/edit-timeline.md -> docs/porting/edit-timeline.md",
    ]


def test_every_relative_link_in_the_real_documents_resolves_to_a_served_document():
    """No link on the Docs page leads to "No such document". A relative or
    ``/``-rooted ``.md`` link in any served document - the root documents and
    every guide - must name another served document, so a link to a file the route does not
    serve (``desktop/README.md``, ``docs/porting/``) is a failure here rather
    than a dead end for the reader."""
    served = docs._served_paths()
    assert len(served) > len(docs.ROOT_DOCS), "the guides are being checked"
    assert _broken_links(served) == []


# ── the search finds each guide ───────────────────────────────────────────────

#: One distinctive term per guide: each hit list must include that guide.
GUIDE_TERMS = [
    ("docs/guides/getting-started", "Accent colour"),
    ("docs/guides/projects", "hexadecimal"),
    ("docs/guides/slide-editor", "sparkle"),
    ("docs/guides/ai-assistant", "anticipated"),
    ("docs/guides/generate-video", "Regenerate"),
    ("docs/guides/transcript", "Save transcript"),
    ("docs/guides/re-voice", "Separate tracks"),
    ("docs/guides/music", "Hear the voice alone"),
    ("docs/guides/markers-and-chapters", "untitled chapter"),
    ("docs/guides/jobs", "cooperative"),
    ("docs/admin/accounts-and-roles", "demoted"),
    ("docs/admin/password-policy", "attacker"),
    ("docs/admin/studio-settings", "prefills"),
    ("docs/admin/updates", "Latest upstream"),
    ("docs/admin/audit-log", "Retention is yours to set"),
    ("docs/admin/data-and-backup", "no backup feature"),
    ("docs/admin/troubleshooting", "liveness"),
    ("docs/ai/narration-engines", "kokoro-onnx"),
    ("docs/ai/transcription", "voice-activity"),
    ("docs/ai/ollama", "MEDIA_STUDIO_OLLAMA_ENABLED"),
    ("docs/reference/keyboard-shortcuts", "quarter-second"),
    ("docs/reference/output-presets", "webinars"),
    ("docs/reference/limits", "enforces"),
]


def test_every_guide_in_the_folders_has_a_search_term():
    """A guide added to a served folder gets a term here too."""
    guides = {docs._slug(rel) for rel in docs._served_paths() if rel.startswith("docs/")} - {"docs/guides/timeline"}
    assert guides == {slug for slug, _ in GUIDE_TERMS}


@pytest.mark.parametrize("slug, term", GUIDE_TERMS)
def test_the_search_finds_each_guide(client, slug, term):
    hits = client.get("/api/docs/search", params={"q": term}).json()
    assert slug in [hit["slug"] for hit in hits], f"{term!r} does not find {slug}"
    hit = next(hit for hit in hits if hit["slug"] == slug)
    assert hit["matches"] >= 1 and term.lower() in hit["snippet"].lower()
