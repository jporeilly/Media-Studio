# Version

**Current version:** 0.9.0
**Status:** 0.x, in progress — the studio is ported (Projects, the slide editor and its AI
assistant, generation options, transcription, re-voice, per-sentence narration adjustments
on a transcript — phases 1 to 3b of the narration timeline, so an offset, a mute, a voice, a
speed, a per-sentence preview, a timeline view with a filmstrip, a waveform and live
audition of the whole narration against the picture, the drag of a sentence along its
lane, and the transcript downloadable as SRT, TXT or JSON in either timing view —, an edit
timeline in
Camtasia's shape on a video project — the kept ranges of its source, one list per track,
selected on the strip with the playhead's green and red handles, Cut, undone, and rendered by
Render as the re-voice with the picture cut first; E1 the model and the render and E2 the
gesture in 0.7.0 (released 2026-09-17); E3 the tracks — lock a track and cut the others,
split at the playhead into pieces, drag the narration — and the transcript download in 0.8.0
(released 2026-09-18); E4 the music lane — a studio-wide music library, clips placed on a
fourth lane and moved, trimmed, levelled and faded there, auditioned in the browser and
mixed under the voice by one more render pass — in 0.9.0 (released 2026-09-22) —, studio settings,
accounts, project ownership and the audit log, self-update, the Windows desktop installer;
see CHANGELOG.md). Still to come: the rest of the edit timeline (E5's trim
handles, markers, J/K/L and snapping of cuts; E4c, so that a music file leaving the library
leaves the editor usable rather than only the render honest), and the
rest of the administration work — notifications,
a branding lock, an admin page of its own, per-kind job limits. The NiceGUI Slide Studio
Enterprise remains the shipping product until parity.

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
