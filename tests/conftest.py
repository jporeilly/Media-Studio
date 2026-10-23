"""Pytest configuration: make the flat backend packages importable, and offer
the route table the anti-rot guards introspect.

The app uses absolute imports (``from services.x``, ``from api.x``). Put the repo
root on ``sys.path`` so the test process resolves them without an install.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@dataclass(frozen=True)
class RouteInfo:
    """One method of one API route, with the path a client actually calls."""

    method: str
    path: str
    endpoint: Callable

    @property
    def name(self) -> str:
        """The endpoint's dotted name - how the guards' exempt lists name it."""
        return f"{self.endpoint.__module__}.{self.endpoint.__name__}"

    def __str__(self) -> str:
        return f"{self.method} {self.path} -> {self.name}"


def _collect(routes, prefix: str) -> list[RouteInfo]:
    """Flatten a router tree into RouteInfo.

    FastAPI 0.141 keeps an included router as a wrapper object rather than
    copying its routes onto the app, so the prefix lives on the wrapper
    (``include_context.prefix``) and the paths inside it are relative. Older
    versions flatten, in which case the first branch sees everything.
    """
    from fastapi.routing import APIRoute

    found: list[RouteInfo] = []
    for route in routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods or ()):
                if method in ("HEAD", "OPTIONS"):
                    continue
                found.append(RouteInfo(method, prefix + route.path, route.endpoint))
            continue
        inner = getattr(route, "original_router", None)
        if inner is not None and hasattr(inner, "routes"):
            context = getattr(route, "include_context", None)
            found += _collect(inner.routes, prefix + (getattr(context, "prefix", "") or ""))
    return found


def api_routes(app) -> list[RouteInfo]:
    """Every API route of ``app``, one entry per method, with full paths."""
    return _collect(app.routes, "")


@pytest.fixture(scope="session")
def routes() -> list[RouteInfo]:
    from api.app import app

    return api_routes(app)


@pytest.fixture(scope="session", autouse=True)
def _app_log_under_basetemp(tmp_path_factory):
    """The checkout's ``data/logs/app.log`` is left as a test run found it (W2d, R7). ``utils.logger`` opens ONE
    file handler at import - every ``get_logger`` logger holds it, and the root's forwarder writes through it - and
    the workshop parser's warnings landed in the real file on every run. For the session the handler is pointed at
    a file under the basetemp: its own file, so its rotation and every logger that holds it are untouched (the
    stream is closed and ``baseFilename`` moved; the next record reopens it there), and the module's ``LOG_FILE`` and
    ``LOG_DIR`` follow, so the logging tests read what was written. Put back when the session ends."""
    from utils import logger as app_logging

    handler = app_logging._file
    real = (app_logging.LOG_FILE, app_logging.LOG_DIR, handler.baseFilename)
    folder = tmp_path_factory.mktemp("app-log")

    def point_at(path: str) -> None:
        handler.acquire()
        try:
            handler.flush()
            stream, handler.stream = handler.stream, None
            if stream is not None:
                stream.close()
            handler.baseFilename = path
        finally:
            handler.release()

    point_at(str(folder / "app.log"))
    app_logging.LOG_FILE, app_logging.LOG_DIR = folder / "app.log", folder
    try:
        yield
    finally:
        point_at(real[2])
        app_logging.LOG_FILE, app_logging.LOG_DIR = real[0], real[1]


@pytest.fixture(autouse=True)
def _capture_dirs(tmp_path_factory, monkeypatch):
    """The app's startup clears the frozen screen frames and resets recording
    saves a restart cut short (``api.app.lifespan``). Every test that starts
    the app does that to folders of its own, never to the checkout's
    ``data/temp`` - where a dev server's recordings may be."""
    from api.routers import capture as capture_routes
    from services import recordings

    base = tmp_path_factory.mktemp("capture-dirs")
    monkeypatch.setattr(capture_routes, "SCREEN_DIR", base / "capture")
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", base / "recordings")
