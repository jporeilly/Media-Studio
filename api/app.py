"""FastAPI application factory: REST API under /api, React SPA served from frontend/dist.

Mirrors OpenSight's ``api/app.py``: the API routers are registered from the
registry in ``api/routers/__init__.py`` under ``/api``, the built frontend is
mounted as static files with a path-traversal-guarded SPA catch-all, and a
lifespan seeds the auth database on startup.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api import __version__, store
from services import jobs
from utils.config import config
from utils.logger import attach_to_uvicorn

APP_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = APP_DIR / "frontend" / "dist"

# The engine Config has no public getter, so read optional web keys from its
# backing dict (an admin can add "brand_name"/"cors_origins" to data/config.json).
BRAND_NAME = config._config.get("brand_name") or "Media Studio Enterprise"
# Default to the frontend dev server (port 5681 per CLAUDE.md; 5173 is Vite's own
# default) so cross-origin dev requests are allowed; override via config.json.
CORS_ORIGINS = config._config.get("cors_origins") or [
    "http://localhost:5681", "http://127.0.0.1:5681",
    "http://localhost:5173", "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database + admin seed. Kept small for now; grows as features land.
    store.init_db()
    store.seed_admin()
    # Screen capture (T3): the frozen frames an overlay left behind (a crash
    # between the freeze and the overlay's answer keeps pictures of the
    # desktop nobody asked to keep), and recording saves a restart cut short
    # (no job survives one: each is offered again, never shown as saving).
    from api.routers.capture import SCREEN_DIR
    from services import capture, recordings
    capture.clear_frozen(SCREEN_DIR)
    recordings.reset_stale()
    # uvicorn's warnings and errors - the traceback of an unhandled 500 among
    # them - into app.log too. Here rather than at import: uvicorn.run applies
    # its own logging config after main.py has imported this module, and that
    # replaces the handlers of its loggers.
    attach_to_uvicorn()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=BRAND_NAME,
        version=__version__,
        description="Media Studio Enterprise API",
        docs_url="/api/swagger",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from api.routers import API_ROUTERS
    for router in API_ROUTERS:
        app.include_router(router, prefix="/api")

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # One job per project at a time (services.jobs.require_idle / start): the
    # generate, re-voice, transcribe, render and AI routes and every slide
    # write answer a busy project with the same 409.
    @app.exception_handler(jobs.ProjectBusy)
    async def _project_busy(request: Request, exc: jobs.ProjectBusy):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    if FRONTEND_DIST.exists():
        assets = FRONTEND_DIST / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            """Serve a built file, else index.html so the SPA router can take over.

            The path is resolved and checked against the build folder first: an
            encoded ``..`` in the URL would otherwise walk out of it and read any
            file the process can.
            """
            root = FRONTEND_DIST.resolve()
            candidate = (root / full_path).resolve()
            if full_path and root in candidate.parents and candidate.is_file():
                return FileResponse(str(candidate))
            # index.html must never be cached. Its asset names carry a content
            # hash, so the assets themselves cache forever, but the page that
            # names them has one URL for the life of the install: the desktop
            # shell's WebView2 held on to a copy from an earlier version and
            # went on rendering that whole UI - version line and all - against
            # an updated backend, because the old hashed files were still on
            # disk beside the new ones.
            return FileResponse(
                str(root / "index.html"),
                headers={"Cache-Control": "no-store, must-revalidate"},
            )
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({
                "message": f"{BRAND_NAME} API is running but the frontend is not built.",
                "hint": "cd frontend && npm install && npm run build",
                "docs": "/api/swagger",
            })

    return app


app = create_app()
