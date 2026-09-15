# Version

**Current version:** 0.3.0
**Status:** 0.x, in progress — the studio is ported (Projects, the slide editor and its AI
assistant, generation options, transcription, re-voice, studio settings, accounts,
self-update, the Windows desktop installer; see CHANGELOG.md). Still to come: the music
library and the administration screens. The NiceGUI Slide Studio Enterprise remains the
shipping product until parity.

## Where the version string lives

Hand-kept and must be identical across all nine carriers. `tests/test_version.py`
fails when any of them disagrees with `__init__.py`, and when README.md, VERSION.md or
Cargo.toml still carries an older number alongside the current one.

| File | Form |
|------|------|
| `__init__.py` | `__version__ = "x.y.z"` — **source of truth** (`api.__version__`; reported by `/api/system/health` and shown in the sidebar footer from that endpoint — the UI never hard-codes it, `tests/test_version.py` checks) |
| `frontend/package.json` | `"version": "x.y.z"` |
| `desktop/package.json` | `"version": "x.y.z"` |
| `desktop/src-tauri/tauri.conf.json` | `"version": "x.y.z"` — the installer's file name and the Add/Remove Programs entry |
| `desktop/src-tauri/Cargo.toml` | `version = "x.y.z"` — the shell exe's file version |
| `desktop/src-tauri/Cargo.lock` | the `media-studio-desktop` entry (cargo rewrites it on the next build, so bump it with the others or the tree is dirty after a build) |
| `README.md` | the `<b>Version x.y.z</b>` line |
| `VERSION.md` | `**Current version:**` — this file |
| `CHANGELOG.md` | release header `## [x.y.z] - YYYY-MM-DD` |

### The desktop exe's version is compile-time

The installed desktop app updates itself with `git pull` (Settings › Updates). A pull
brings the new `__init__.py`, so the version the API and the UI report changes — but the
shell's file version (`Cargo.toml` / `tauri.conf.json`, compiled into
`media-studio-desktop.exe` and recorded by the installer in Add/Remove Programs) does
not. So a version bump implies a new installer; between installers, an updated install
legitimately shows the new version in Settings and the installer's in Add/Remove
Programs.

## Bump policy

Semantic Versioning while `0.x`:
- **Minor** (`0.x` → `0.x+1`): a new feature, or a removed feature.
- **Patch** (`0.x.y` → `0.x.y+1`): fixes, dependency bumps, docs only.

A bump touches all nine carriers above, and then:

1. `frontend/package.json` is part of the built UI's fingerprint, and `frontend/dist`
   is committed — so run `npm run build` in `frontend/` and commit `frontend/dist`
   with the bump (`tests/test_frontend_dist.py` fails otherwise).
2. Add the `## [x.y.z] - YYYY-MM-DD` header to CHANGELOG.md, keeping `## [Unreleased]`
   above it.
3. Run `venv/Scripts/python -m pytest -q tests/test_version.py tests/test_frontend_dist.py`.
4. Build a new installer (`npm run dist` in `desktop/`) — see the note above.

## Lineage

Media Studio Enterprise is the React + FastAPI successor to **Slide Studio Enterprise**
(NiceGUI, `C:\Projects\slidestudio_enterprise`, last at 0.4.1). The Python media
engine (`core/`, `utils/`, `services/processing.py`) is carried over; the UI is rebuilt
on the OpenSight design system. The NiceGUI app remains the shipping product until this
one reaches feature parity.
