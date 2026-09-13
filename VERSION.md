# Version

**Current version:** 0.1.0
**Status:** Scaffold — React + FastAPI rebuild of Slide Studio Enterprise, in progress.

## Where the version string lives

Hand-kept and must be identical across:

| File | Form |
|------|------|
| `__init__.py` | `__version__ = "0.1.0"` — **source of truth** |
| `frontend/package.json` | `"version": "0.1.0"` |
| `CHANGELOG.md` | release header `## [0.1.0] - YYYY-MM-DD` |
| `VERSION.md` | this file |

## Bump policy

Semantic Versioning while `0.x`:
- **Minor** (`0.x` → `0.x+1`): a new feature, or a removed feature.
- **Patch** (`0.x.y` → `0.x.y+1`): fixes, dependency bumps, docs only.

## Lineage

Media Studio Enterprise is the React + FastAPI successor to **Slide Studio Enterprise**
(NiceGUI, `C:\Projects\slidestudio_enterprise`, last at 0.4.1). The Python media
engine (`core/`, `utils/`, `services/processing.py`) is carried over; the UI is rebuilt
on the OpenSight design system. The NiceGUI app remains the shipping product until this
one reaches feature parity.
