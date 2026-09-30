<h1 align="center">Media Studio Enterprise</h1>

**Version 0.11.0** — React + FastAPI. Turn slide decks and videos into narrated MP4s.

Media Studio Enterprise is the React + FastAPI successor to **Slide Studio Enterprise**
(NiceGUI). It reuses the same Python media engine — PowerPoint/PDF export, Edge TTS &
Kokoro narration, faster-whisper transcription, moviepy/ffmpeg assembly, Ollama-powered
notes/QA/translation — behind a modern SPA that shares the **OpenSight** design system so
the whole app suite looks and feels the same.

## What it does

- **A deck or a PDF becomes a narrated video.** Import a PowerPoint deck or a PDF, write
  or edit the speaker notes slide by slide, and render an MP4 narrated in the voice you
  choose, with transitions, intro and outro cards, a watermark, subtitles, WebM, GIF and
  MP3 copies, and presets for YouTube, LinkedIn and Teams, Zoom and Vimeo. A deck's edited
  notes can be exported back into the deck.
- **A video gets a new voice.** Import a video and transcribe it. Fix the words;
  nudge, mute or give any sentence its own voice and speed; then re-voice the video with a
  new narration that keeps step with the picture, in the original language or translated
  into another. The transcript downloads as SRT, TXT or JSON.
- **An edit timeline.** Cut the picture and the narration together or on their own lanes,
  split, trim and re-time sentences, lay music from a library the whole studio shares
  under the voice, and drop markers that become chapters in the MP4, all heard against
  the picture before anything is rendered.
- **An AI assistant on the slides.** A local Ollama model writes and rewrites speaker
  notes, reviews them for grammar, tone, flow and transitions and fixes what it finds,
  adapts them to an audience, translates them, paces them, scores the deck for video and
  drafts a Q&A document.
- **Narration online or on the machine.** Microsoft's Edge TTS voices over the internet,
  or Kokoro's voices running locally.
- **For a team.** Accounts with two roles, a password policy, projects that belong to
  whoever imported them, an audit log of every change, and updates pulled from Git from
  inside the app.

The app's **Docs** page has a guide for each of these. What changed in each release, and
when, is in [CHANGELOG.md](CHANGELOG.md).

**Still to come:** the transition-sound library, and the rest of the administration
work: notifications, a branding lock, an administration page of its own, and per-kind job
limits. Until parity, the NiceGUI app remains the shipping product; its source lives at
`jporeilly/slidestudio-enterprise` (no longer checked out on the development machine).

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
new password at first login before anything else works. Admins create further accounts on the
**Accounts** card of the Settings page and set the password rules on its **Password policy** card.

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
the **Updates** card on the Settings page can pull new commits — backend and UI — and restart
in place on a machine that has `git` and access to the repo. An update never touches
`app\bin\`, so a release that changes the binaries has to be installed with its installer:
0.9.1, the first to ship ffprobe, is one. Data lives inside that folder and survives an
uninstall: accounts, settings and every project with its rendered videos in `app\data\`, the
music library in `app\assets\music\`. [INSTALL.md](INSTALL.md) covers install, first launch,
updating and uninstall; `desktop/README.md` (in the repository) covers the build.

## Documentation

The app's **Docs** page (in the sidebar) serves this README, INSTALL.md, the changelog,
VERSION.md and the guides in four sections: `docs/guides/` (Using Media Studio),
`docs/admin/` (Administration), `docs/ai/` (Narration, transcription and AI) and
`docs/reference/` (Reference). The design journal in `docs/porting/` is deliberately not
served.

- [Getting started](docs/guides/getting-started.md) — the first launch, the three kinds of project, and where to go next
- [The Timeline](docs/guides/timeline.md) — the full help for editing a video project: the lanes, cutting, locks, splitting, re-timing, the Music lane and every key
- [Data and backup](docs/admin/data-and-backup.md) — where everything lives, and how to back it up
- [Troubleshooting](docs/admin/troubleshooting.md) — symptoms, causes and fixes
- [INSTALL.md](INSTALL.md) — install, update and uninstall the Windows desktop edition
- [VERSION.md](VERSION.md) — version carriers and bump policy
- [CHANGELOG.md](CHANGELOG.md) — changes
- `CLAUDE.md` and `desktop/README.md` (in the repository, not served by the app's Docs page) — the development workflow and architecture; building the desktop installer, and how the shell works
