"""frontend/dist (the built UI) is COMMITTED, so the installed app's ``git pull``
updates the UI as well as the backend (decided 2026-09-14).

That only holds while the committed build matches the committed source. ``npm run
build`` writes ``dist/build-info.json`` with a fingerprint of every file that can
change the bundle (``frontend/scripts/build-info.mjs``); this test recomputes it
with the same rule, so a source change committed without ``npm run build`` and a
commit of ``frontend/dist`` fails here rather than shipping a stale UI.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"

# Keep IDENTICAL to frontend/scripts/build-info.mjs.
EXTRA = ["index.html", "package.json", "package-lock.json", "vite.config.ts", "tsconfig.json", "tsconfig.node.json"]
TEST_FILE = re.compile(r"\.test\.tsx?$")

REBUILD_HINT = "run `npm run build` in frontend/ and commit frontend/dist"


def fingerprint() -> tuple[str, int]:
    files: list[Path] = []
    for d in ("src", "public"):
        p = FRONTEND / d
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file()]
    for name in EXTRA:
        p = FRONTEND / name
        if p.is_file():
            files.append(p)
    rels = sorted({f.relative_to(FRONTEND).as_posix() for f in files if not TEST_FILE.search(f.name)})
    h = hashlib.sha256()
    for rel in rels:
        h.update(rel.encode("utf-8") + b"\0")
        h.update((FRONTEND / rel).read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest(), len(rels)


def test_built_ui_is_present():
    assert (DIST / "index.html").is_file(), f"frontend/dist/index.html is missing - {REBUILD_HINT}"


def test_built_ui_matches_the_source():
    info_file = DIST / "build-info.json"
    assert info_file.is_file(), f"frontend/dist/build-info.json is missing - {REBUILD_HINT}"
    info = json.loads(info_file.read_text(encoding="utf-8"))
    expected, count = fingerprint()
    assert info.get("srcHash") == expected, (
        f"frontend/dist is STALE: it was built from a different source tree "
        f"({count} files fingerprinted) - {REBUILD_HINT}"
    )


def test_index_references_only_assets_that_exist():
    """A partial commit of dist (index.html without its hashed chunks) would 404 at runtime."""
    html = (DIST / "index.html").read_text(encoding="utf-8")
    refs = re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
    assert refs, "index.html references no assets - is this a real Vite build?"
    missing = [r for r in refs if not (DIST / r).is_file()]
    assert missing == [], f"index.html references assets that are not in frontend/dist: {missing}"


def test_built_ui_is_tracked_by_git():
    r = subprocess.run(["git", "ls-files", "--error-unmatch", "frontend/dist/index.html"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 and "not a git repository" in (r.stderr or ""):
        pytest.skip("not a git checkout")
    assert r.returncode == 0, "frontend/dist is not tracked - the installed app's git pull could never update the UI"
