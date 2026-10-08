# Version

**Current version:** 0.14.0

Media Studio Enterprise is at 0.x: the studio is ported, and its version string is
kept by hand in the eleven files listed below. What each release changed, and when, is in
[CHANGELOG.md](CHANGELOG.md).

## What it does

Import a slide deck or a PDF and render it into a narrated video; import a video to
transcribe it, give it a new or a translated narration, and edit it on a timeline with music
and chapters; let a local AI model write and review the speaker notes; and run it for a
team, with accounts and roles, an audit log and updates from inside the app. The
[README](README.md) says a little more about each, the app's Docs page has a guide for each,
and the README's **Still to come** is the one list of what is not in yet. The NiceGUI Slide
Studio Enterprise remains the shipping product until parity.

## Where the version string lives

Hand-kept and must be identical across all eleven carriers. `tests/test_version.py`
fails when any of them disagrees with `__init__.py`, and when README.md, VERSION.md or
Cargo.toml still carries an older number alongside the current one.

| File | Form |
|------|------|
| `__init__.py` | `__version__ = "x.y.z"` — **source of truth** (`api.__version__`; reported by `/api/system/health` and shown in the sidebar footer from that endpoint — the UI never hard-codes it, `tests/test_version.py` checks) |
| `frontend/package.json` | `"version": "x.y.z"` |
| `frontend/package-lock.json` | the two root entries (`version` and `packages[""].version`) — npm rewrites them from package.json on the next install, so bump them with the others or the tree is dirty mid-build |
| `desktop/package.json` | `"version": "x.y.z"` |
| `desktop/package-lock.json` | the two root entries, as above |
| `desktop/src-tauri/tauri.conf.json` | `"version": "x.y.z"` — the installer's file name and the Add/Remove Programs entry |
| `desktop/src-tauri/Cargo.toml` | `version = "x.y.z"` — the shell exe's file version |
| `desktop/src-tauri/Cargo.lock` | the `media-studio-desktop` entry (cargo rewrites it on the next build, so bump it with the others or the tree is dirty after a build) |
| `README.md` | the `**Version x.y.z**` line (plain markdown, since the Docs page renders README) |
| `VERSION.md` | `**Current version:**` — this file |
| `CHANGELOG.md` | release header `## [x.y.z] - YYYY-MM-DD` |

### The desktop exe's version is compile-time

The installed desktop app updates itself with `git pull` (the Updates card on the Settings
page). A pull brings the new `__init__.py`, so the version the API and the UI report changes —
but the shell's file version (`Cargo.toml` / `tauri.conf.json`, compiled into
`media-studio-desktop.exe` and recorded by the installer in Add/Remove Programs) does
not. So a version bump implies a new installer; between installers, an updated install
legitimately shows the new version in the app's sidebar and the installer's in Add/Remove
Programs.

## Bump policy

Semantic Versioning while `0.x`:
- **Minor** (`0.x` → `0.x+1`): a new feature, or a removed feature.
- **Patch** (`0.x.y` → `0.x.y+1`): fixes, dependency bumps, docs only.

A bump touches all eleven carriers above, and then:

1. `frontend/package.json` is part of the built UI's fingerprint, and `frontend/dist`
   is committed — so run `npm run build` in `frontend/` and commit `frontend/dist`
   with the bump (`tests/test_frontend_dist.py` fails otherwise).
2. Add the `## [x.y.z] - YYYY-MM-DD` header to CHANGELOG.md, keeping `## [Unreleased]`
   above it.
3. Run `venv/Scripts/python -m pytest -q tests/test_version.py tests/test_frontend_dist.py`.
4. Build a new installer (`npm run dist` in `desktop/`) — see the note above.

## Lineage

Media Studio Enterprise is the React + FastAPI successor to **Slide Studio Enterprise**
(NiceGUI, last at 0.4.1; source at `jporeilly/slidestudio-enterprise`, no longer
checked out locally). The Python media
engine (`core/`, `utils/`, `services/processing.py`) is carried over; the UI is rebuilt
on the OpenSight design system. The NiceGUI app remains the shipping product until this
one reaches feature parity.
