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
from utils.config import config

APP_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIST = APP_DIR / "frontend" / "dist"

# The engine Config has no public getter, so read optional web keys from its
# backing dict (an admin can add "brand_name"/"cors_origins" to data/config.json).
BRAND_NAME = config._config.get("brand_name") or "Media Studio Enterprise"
# Default to the frontend dev server (port 5273 per CLAUDE.md; 5173 is Vite's own
# default) so cross-origin dev requests are allowed; override via config.json.
CORS_ORIGINS = config._config.get("cors_origins") or [
    "http://localhost:5273", "http://127.0.0.1:5273",
    "http://localhost:5173", "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database + admin seed. Kept small for now; grows as features land.
    store.init_db()
    store.seed_admin()
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
            return FileResponse(str(root / "index.html"))
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
