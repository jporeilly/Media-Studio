<h1 align="center">Media Studio Enterprise</h1>

<p align="center"><b>Version 0.4.0</b> — React + FastAPI. Turn slide decks and videos into narrated MP4s.</p>

Media Studio Enterprise is the React + FastAPI successor to **Slide Studio Enterprise**
(NiceGUI). It reuses the same Python media engine — PowerPoint/PDF export, Edge TTS &
Kokoro narration, faster-whisper transcription, moviepy/ffmpeg assembly, Ollama-powered
notes/QA/translation — behind a modern SPA that shares the **OpenSight** design system so
the whole app suite looks and feels the same.

> **Status: in progress — the studio is ported.** Projects (import decks, PDFs and videos),
> a slide editor (thumbnails, speaker notes with undo, per-slide voice and pause, export of
> the edited notes back to PPTX) with an AI assistant on it (generate and enhance notes, QA
> review with per-issue fixes, tone, translation, pacing, analysis and a Q&A document, via a
> local Ollama), narrated-video generation with transitions, intro/outro cards, watermark,
> subtitles, extra formats and Vimeo output presets, video transcription with an editable
> transcript whose sentences can each be nudged or muted before a re-voice (phase 1 of the
> narration timeline — no waveform, no drag), re-voicing in a new voice or a translated
> language that keeps step with the picture, Edge TTS or local Kokoro narration with
> studio-wide defaults, accounts with a
> configurable password policy, projects that belong to whoever imported them with an audit
> log of every change for admins, self-update from Git, and a Windows desktop installer are
> in (see [CHANGELOG.md](CHANGELOG.md)). Still to come: the music and transition-sound
> library, the rest of the narration timeline (per-sentence voice and speed controls,
> hearing one sentence, and the waveform with drag), and the rest of the administration work
> (notifications, a branding lock, an admin page of its own, per-kind job limits). Until parity,
> the NiceGUI app at `C:\Projects\slidestudio_enterprise` remains the shipping product.

## Stack

- **Backend:** FastAPI + SQLite (session-cookie auth), the carried-over Python engine in `core/` / `utils/` / `services/`. Single-process in production — FastAPI serves the built SPA.
- **Frontend:** React 19 + TypeScript + Vite, React Router, TanStack Query, the OpenSight design system (`theme.css` tokens + `os-*` components), lucide icons.

## Develop

```bash
# Backend (port 5680)
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python main.py --no-browser

# Frontend (port 5681, proxies /api -> 5680)
cd frontend
npm install
npm run dev
```

Open http://localhost:5681. Default login is `admin` / `admin`; a fresh database asks you to set a
new password at first login before anything else works. Admins create further accounts under
Settings › Accounts and set the password rules under Settings › Password policy.

## Build (production)

```bash
cd frontend && npm run build      # emits frontend/dist (+ build-info.json) — committed, see below
cd .. && venv\Scripts\python main.py   # FastAPI serves the SPA on port 5680
```

The built UI is **tracked in git**: the installed desktop app is a git checkout that updates
itself with `git pull`, so the build has to travel with the source. After changing anything
under `frontend/`, run `npm run build` there and commit `frontend/dist` with the change.
`npm run build` records a fingerprint of the source in `frontend/dist/build-info.json`, and
`tests/test_frontend_dist.py` fails when the committed build does not match the committed
source.

## Desktop edition

`desktop/` builds a Windows installer — `dist\Media Studio Enterprise_<version>_x64-setup.exe`:
a small Tauri/WebView2 shell with a vendored Python runtime and ffmpeg, so a machine needs
nothing pre-installed. It installs per-user into `%LOCALAPPDATA%\Media Studio Enterprise\`
(no admin rights), and the app inside is a git checkout, so **Settings › Updates** can pull
new commits — backend and UI — and restart in place on a machine that has `git` and access
to the repo. Data and rendered videos live inside that folder (`app\data\`,
`app\assets\finished\`) and survive an uninstall. [INSTALL.md](INSTALL.md) covers install,
first launch, updating and uninstall; [desktop/README.md](desktop/README.md) covers the build.

## Documentation

- [INSTALL.md](INSTALL.md) — install, update and uninstall the Windows desktop edition
- [VERSION.md](VERSION.md) — version carriers and bump policy
- [CHANGELOG.md](CHANGELOG.md) — changes
- [CLAUDE.md](CLAUDE.md) — the development workflow and architecture
- [desktop/README.md](desktop/README.md) — building the desktop installer, and how the shell works
