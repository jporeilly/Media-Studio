<h1 align="center">Media Studio Enterprise</h1>

**Version 0.9.0** — React + FastAPI. Turn slide decks and videos into narrated MP4s.

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
> transcript whose sentences can each be nudged, muted, given their own voice and speed and
> played back one at a time, and a timeline view of those sentences over a filmstrip and the
> original audio's waveform that plays the whole new narration against the picture without
> running a re-voice (phases 1 to 3b of the narration timeline — the drag lives on the edit
> timeline's Narration lane), that transcript downloadable as SRT, TXT or JSON — timed as the
> re-voice will speak it or as it was spoken in the source —, an **edit
> timeline** in Camtasia's shape on a video project — the ranges of its source that are kept,
> one list **per track** (video, narration) that a split, a trim and a ripple delete all
> change, made on the strip itself (select a range with the playhead's green and red handles
> or Ctrl+drag, **Cut**, undo; click a track's name to edit just that channel while the other
> stays locked; `S` splits at the playhead into pieces that can be clicked and cut; drag a
> sentence block along the Narration lane, with snapping, to re-time it) and rendered by
> **Render**, the re-voice with the picture cut first (E1 the model and the render and E2 the
> gesture in 0.7.0; E3 the tracks in 0.8.0, released 2026-09-18 together with the transcript
> download; **E4 the music lane in 0.9.0, released 2026-09-22** — a studio-wide music
> library, clips placed on a fourth lane and moved, trimmed, levelled and faded there,
> auditioned in the browser against the voice and mixed under it by one more render pass;
> trim handles and markers are not built), re-voicing in
> a new voice or a translated language that keeps step with
> the picture, Edge TTS or local Kokoro narration with studio-wide defaults, accounts with a
> configurable password policy, projects that belong to whoever imported them with an audit
> log of every change for admins, self-update from Git, a Windows desktop installer, and a
> **Docs** page in the app that serves this README, the install guide, the changelog, the
> version note and a full guide to the Timeline are
> in (see [CHANGELOG.md](CHANGELOG.md)). Still to come: the transition-sound library, the
> rest of the edit timeline (E5's trim handles, markers, J/K/L and snapping of cuts), and
> the rest of the administration work (notifications, a branding lock, an admin page of its
> own, per-kind job limits). Until parity, the NiceGUI app remains the shipping product; its source lives at
> `jporeilly/slidestudio-enterprise` (no longer checked out on the development machine).

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

`desktop/` builds a Windows installer — `dist\Media-Studio-Enterprise_<version>_x64-setup.exe`:
a small Tauri/WebView2 shell with a vendored Python runtime and a matched ffmpeg and ffprobe
(`app\bin\`), so a machine needs nothing pre-installed. It installs per-user into
`C:\Media-Studio-Enterprise\` (no admin rights), and the app inside is a git checkout, so
**Settings › Updates** can pull new commits — backend and UI — and restart in place on a
machine that has `git` and access to the repo. An update never touches `app\bin\`, so a
release that changes the binaries has to be installed with its installer: 0.9.1, the first
to ship ffprobe, is one. Data and rendered videos live inside that folder (`app\data\`,
`app\assets\finished\`) and survive an uninstall. [INSTALL.md](INSTALL.md) covers install,
first launch, updating and uninstall; [desktop/README.md](desktop/README.md) covers the build.

## Documentation

The app's **Docs** page (in the sidebar) serves this README, INSTALL.md, the changelog,
VERSION.md and the guides under `docs/guides/`. The design journal in `docs/porting/` is
deliberately not served.

- [The Timeline](docs/guides/timeline.md) — the full help for editing a video project: the lanes, cutting, locks, splitting, re-timing, the Music lane and every key
- [INSTALL.md](INSTALL.md) — install, update and uninstall the Windows desktop edition
- [VERSION.md](VERSION.md) — version carriers and bump policy
- [CHANGELOG.md](CHANGELOG.md) — changes
- [CLAUDE.md](CLAUDE.md) — the development workflow and architecture
- [desktop/README.md](desktop/README.md) — building the desktop installer, and how the shell works
