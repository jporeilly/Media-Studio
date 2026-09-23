"""The version string is hand-kept in several files; this test keeps them equal.

Source of truth: the repo-root ``__init__.py`` (``__version__``), read through
``api.__version__``. Every other carrier - the two package.json files and their
lockfiles, the Tauri config, the crate and its lockfile, README, VERSION.md and
the CHANGELOG release header - must match it exactly. See VERSION.md for the
bump policy.
"""

import json
import re
from pathlib import Path

import pytest

from api import __version__ as VERSION

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_version_is_plain_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSION), VERSION


@pytest.mark.parametrize(
    "rel, pattern",
    [
        ("__init__.py", r'^__version__ = "{v}"$'),
        ("frontend/package.json", r'^  "version": "{v}",$'),
        ("desktop/package.json", r'^  "version": "{v}",$'),
        ("desktop/src-tauri/tauri.conf.json", r'^  "version": "{v}",$'),
        ("desktop/src-tauri/Cargo.toml", r'^version = "{v}"$'),
        # The lockfile records the crate's own version; cargo rewrites it on the
        # next build, so a bump that skips it leaves the tree dirty after a build.
        ("desktop/src-tauri/Cargo.lock", r'^name = "media-studio-desktop"\nversion = "{v}"$'),
        ("README.md", r"\*\*Version {v}\*\*"),
        ("VERSION.md", r"^\*\*Current version:\*\* {v}$"),
        ("CHANGELOG.md", r"^## \[{v}\] - \d{{4}}-\d{{2}}-\d{{2}}$"),
    ],
)
def test_version_string_agrees_everywhere(rel, pattern):
    text = _read(rel)
    regex = pattern.format(v=re.escape(VERSION))
    assert re.search(regex, text, re.MULTILINE), f"{rel} does not carry version {VERSION} (expected /{regex}/)"


@pytest.mark.parametrize(
    "rel, pattern",
    [
        # A leftover older number in any carrier means a bump missed a file.
        ("README.md", r"\*\*Version (\d+\.\d+\.\d+)\*\*"),
        ("VERSION.md", r"^\*\*Current version:\*\* (\d+\.\d+\.\d+)$"),
        ("desktop/src-tauri/Cargo.toml", r'^version = "(\d+\.\d+\.\d+)"$'),
    ],
)
def test_no_other_app_version_numbers_in_carriers(rel, pattern):
    numbers = set(re.findall(pattern, _read(rel), re.MULTILINE))
    assert numbers == {VERSION}, f"{rel} carries {sorted(numbers)}, expected only {VERSION}"


@pytest.mark.parametrize("rel", ["frontend/package-lock.json", "desktop/package-lock.json"])
def test_npm_lockfiles_carry_the_version(rel):
    """``npm install`` copies the version from package.json into the lockfile's
    two root entries. A lockfile left behind (both sat at 0.1.0 from the scaffold
    until 0.3.1) is rewritten by the next install and dirties the tree in the
    middle of a build - which the staging gate then refuses. Same trap as
    Cargo.lock above, so the same rule: bump it with the others.

    Read as JSON rather than by line: a dependency may carry any version string,
    and only these two keys describe the app itself.
    """
    data = json.loads(_read(rel))
    assert data.get("version") == VERSION, f"{rel}: root version is {data.get('version')!r}"
    own = data.get("packages", {}).get("", {})
    assert own.get("version") == VERSION, f'{rel}: packages[""] version is {own.get("version")!r}'


def test_frontend_source_never_hard_codes_a_version():
    """The UI shows the version the API reports (Settings › Updates). A literal in
    the source is a tenth carrier nobody bumps: the sidebar footer once printed
    0.1.0 inside the 0.2.0 build."""
    pattern = re.compile(r"""\bVERSION\s*=\s*["']\d+\.\d+\.\d+["']|["']v\d+\.\d+\.\d+["']""")
    offenders = []
    for path in (ROOT / "frontend" / "src").rglob("*"):
        if path.suffix in {".ts", ".tsx"} and pattern.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"hard-coded version strings in the frontend source: {offenders}"


def test_changelog_has_an_unreleased_section_above_the_release():
    text = _read("CHANGELOG.md")
    unreleased = text.find("## [Unreleased]")
    release = text.find(f"## [{VERSION}]")
    assert unreleased != -1, "CHANGELOG.md has no [Unreleased] section"
    assert release != -1, f"CHANGELOG.md has no [{VERSION}] release header"
    assert unreleased < release, "[Unreleased] must sit above the current release"
