"""The in-app documentation routes (``api/routers/docs.py``).

What these tests hold in place:

- **``docs/porting/`` is not reachable.** It is 3,700 lines of internal design
  journal - build phases, traps, review findings, the owner's rulings and the
  paths of files on a developer's machine - and the route serves a WHITELIST
  (the four root documents and ``docs/guides/``) rather than a directory, so a
  crafted slug has no path to walk down. Every spelling anyone here could think
  of is tried, over HTTP and against the resolver itself, against a fake tree
  AND against the real repository;
- the list derives everything from the files (title, summary, section, order)
  and a file that cannot be read leaves the list short rather than failing it;
- a document comes back with the headings the contents rail is built from;
- every route needs a session.

The fake tree is a ``tmp_path`` with the same SHAPE as the repo - four root
documents, ``docs/guides/`` and a ``docs/porting/`` full of secrets - so the
refusals are tested against a porting file that really is there to be found.
"""

import os
import re
import subprocess
from pathlib import Path

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

    (root / "docs" / "porting" / "edit-timeline.md").write_text(
        f"# Edit timeline (internal)\n\n{SECRET}: trap 19, the owner's ruling, C:\\Projects\\...\n", encoding="utf-8")
    (root / "docs" / "porting" / "narration-timeline.md").write_text(f"# Internal\n\n{SECRET}\n", encoding="utf-8")
    # A document one level OUTSIDE the checkout: the classic `..` target.
    (tmp_path / "outside.md").write_text(f"# Outside\n\n{SECRET}\n", encoding="utf-8")

    monkeypatch.setattr(docs, "APP_DIR", root)
    monkeypatch.setattr(docs, "DOCS_DIR", root / "docs")
    monkeypatch.setattr(docs, "GUIDES_DIR", root / "docs" / "guides")
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


# ── the list ──────────────────────────────────────────────────────────────────

def test_the_list_derives_title_section_and_order_from_the_files(client, tree):
    r = client.get("/api/docs")
    assert r.status_code == 200, r.text
    payload = r.json()
    assert [s["name"] for s in payload["sections"]] == ["Start here", "Using Media Studio", "Project"]
    assert [(i["section"], i["slug"], i["title"]) for i in _items(payload)] == [
        ("Start here", "README", "Media Studio Enterprise"),
        ("Start here", "INSTALL", "Install"),
        ("Using Media Studio", "docs/guides/no-heading", "No Heading"),
        ("Using Media Studio", "docs/guides/timeline", "The Timeline"),
        ("Project", "CHANGELOG", "Changelog"),
        ("Project", "VERSION", "Version"),
    ], "the root documents in ROOT_DOCS order, the guides sorted, the sections in SECTION_ORDER"
    assert payload["count"] == 6


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
        assert item["path"] in docs.ROOT_DOCS or item["path"].startswith("docs/guides/"), item["path"]
    assert not any("porting" in item["path"] for item in _items(client.get("/api/docs").json()))


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
                                              "docs/guides/timeline.md"]
        assert docs._resolve("guides/sneaky/edit-timeline") is None
        assert docs._resolve("docs/guides/sneaky/edit-timeline.md") is None
        assert all("sneaky" not in item["path"] for item in docs._all_docs())
    finally:
        # Remove the JUNCTION, never what it points at.
        link.rmdir()


# ── the real repository, not a fake tree ──────────────────────────────────────

def test_the_real_checkout_serves_the_guides_and_not_the_porting_journal(client):
    """No fixture: the router pointed at the repository it ships in."""
    payload = client.get("/api/docs").json()
    paths = [item["path"] for item in _items(payload)]
    assert "docs/guides/timeline.md" in paths, "the Timeline guide is served"
    assert not any(p.startswith("docs/porting/") for p in paths), paths
    for item in _items(payload):
        assert item["path"] in docs.ROOT_DOCS or item["path"].startswith("docs/guides/"), item["path"]


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
