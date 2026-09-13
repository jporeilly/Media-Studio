<h1 align="center">Media Studio Enterprise</h1>

<p align="center"><b>Version 0.1.0</b> — React + FastAPI. Turn slide decks and videos into narrated MP4s.</p>

Media Studio Enterprise is the React + FastAPI successor to **Slide Studio Enterprise**
(NiceGUI). It reuses the same Python media engine — PowerPoint/PDF export, Edge TTS &
Kokoro narration, faster-whisper transcription, moviepy/ffmpeg assembly, Ollama-powered
notes/QA/translation — behind a modern SPA that shares the **OpenSight** design system so
the whole app suite looks and feels the same.

> **Status: scaffold / in progress.** The backend skeleton, the design-system frontend
> shell, auth, and a dashboard landing state are in place. Screens (sidebar, preview/editor,
> generation, settings, admin) are being ported from the NiceGUI app. Until parity, the
> NiceGUI app at `C:\Projects\slidestudio_enterprise` remains the shipping product.

## Stack

- **Backend:** FastAPI + SQLite (session-cookie auth), the carried-over Python engine in `core/` / `utils/` / `services/`. Single-process in production — FastAPI serves the built SPA.
- **Frontend:** React 19 + TypeScript + Vite, React Router, TanStack Query, the OpenSight design system (`theme.css` tokens + `os-*` components), lucide icons.

## Develop

```bash
# Backend (port 5680)
python -m venv venv
venv\Scripts\python -m pip install -r requirements.txt
venv\Scripts\python main.py --no-browser

# Frontend (port 5273, proxies /api -> 5680)
cd frontend
npm install
npm run dev
```

Open http://localhost:5273. Default login is `admin` / `admin` (change it).

## Build (production)

```bash
cd frontend && npm run build      # emits frontend/dist
cd .. && venv\Scripts\python main.py   # FastAPI serves the SPA on port 5680
```

## Documentation

- [VERSION.md](VERSION.md) — version carriers and bump policy
- [CHANGELOG.md](CHANGELOG.md) — changes
- [CLAUDE.md](CLAUDE.md) — the development workflow and architecture
