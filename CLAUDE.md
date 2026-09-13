# CLAUDE.md — Media Studio Enterprise

React + FastAPI rebuild of Slide Studio Enterprise. Same agentic workflow as the
predecessor: Claude is the **supervisor**. No feature is complete until all three
gates pass — **Developer → Reviewer → Documentation Writer** — and Claude never
skips a gate to save time.

---

## Workflow

```
User task → Claude (clarify, decompose)
  → Developer agent (implement + tests, both backend and frontend)
  → Claude validates against the Developer checklist
  → Reviewer agent (APPROVED / CHANGES REQUIRED); fixes go back to Developer
  → Claude validates the review
  → Documentation Writer agent (docs, examples, changelog)
  → Claude validates and closes the task
```

Handoff checklists are as in the predecessor repo. Reviewer may not implement
fixes. `APPROVED` is invalid while any Developer-checklist item fails.

---

## Architecture

A single-repo app: **FastAPI + SQLite backend at the repo root**, **React + TypeScript +
Vite SPA in `frontend/`**. In production the built SPA is served as static files by the
same FastAPI process (one port). Design system, auth model, and conventions mirror
**OpenSight** (`C:\Projects\OpenSight`) — that is the reference implementation and the
source of the shared look & feel.

```
main.py              CLI launcher (uvicorn; default port 5680)
api/
  app.py             create_app() factory: CORS, router registry, SPA static serving
  deps.py            current_user / optional_user (session cookie "ms_session"), require_roles
  routers/           one module per area; registered in routers/__init__.py under /api
core/                the media engine, carried over from Slide Studio (NiceGUI-free)
utils/               config (JSON-backed), helpers, logging, ssml
services/
  processing.py      VideoProcessor pipeline orchestrator (generate / prepare / re-voice)
frontend/            React + Vite + TS SPA (see frontend/ conventions below)
data/                runtime data + SQLite (git-ignored)
```

The Python engine (`core/`, `utils/`, `services/`) is imported with **absolute** package
paths (`from core.x import y`, `from utils.x import y`) — it was converted from the
predecessor's package-relative imports during the port. Keep it that way.

---

## Stack-specific configuration

```
Backend tests:   pytest — venv/Scripts/python -m pytest -q tests
Backend lint:    ruff   — venv/Scripts/python -m ruff check --select F401,F841 <files>
Frontend tests:  vitest (unit, pure helpers) + Playwright (e2e) — from frontend/: npm run test / npm run e2e
Frontend lint:   eslint — from frontend/: npm run lint  (build is typecheck-gated: tsc --noEmit && vite build)
Version:         hand-kept; see VERSION.md. Keep __init__.py, frontend/package.json,
                 CHANGELOG.md and VERSION.md in agreement.
Dev run:         backend  python main.py --no-browser   (port 5680)
                 frontend cd frontend && npm run dev     (port 5273, proxies /api -> 5680)
Prod:            npm --prefix frontend run build  then  python main.py  (FastAPI serves frontend/dist)
Live check:      UI behaviour is verified in the browser by the owner, not the agents — say so in reports.
```

### frontend/ conventions (from OpenSight)
- Data layer is **@tanstack/react-query** only (no Redux); the API client is the thin
  `src/api/client.ts` fetch wrapper (relative `/api`, `credentials:"include"`, global 401 event).
- Styling is **hand-authored CSS custom properties** in `src/styles/theme.css` (the `os-*`
  classes + token layer) driven by `src/context/ThemeContext.tsx`. No Tailwind, no UI kit.
- Icons: `lucide-react`. Markdown: `react-markdown` + `remark-gfm`.
- Route-level code splitting via `React.lazy`; `RequireAuth` wraps the authed routes in `<Shell/>`.

---

## Commit convention

`feat|fix|test|docs|refactor|chore(scope): summary`. Production code ships with its tests.

## Escalation

Blocked, conflicting, or ambiguous → stop, report what's done and what's needed, wait for
the user. Do not resolve ambiguity by assumption.
