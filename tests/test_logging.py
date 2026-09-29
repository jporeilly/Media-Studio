"""Every module's warnings reach ``app.log``, each exactly once (E7 #4).

Only loggers made by ``utils.logger.get_logger`` used to write to the file:
``core.translator``, ``services.updater`` and the AI engine modules under
``core`` log through a plain ``logging.getLogger``, whose records propagate to
the root logger - which had no handler, so Python's last resort printed them
on stderr and ``app.log`` never saw them. The root now forwards WARNING and
above to the same file handler; a ``get_logger`` logger does not propagate,
so its lines are still written once, by its own handlers, at every level they
always were.

Read back from the real ``data/logs/app.log``: that file is the claim.
"""

import io
import logging
import uuid

import pytest

from utils import logger as app_logging


def _flush() -> None:
    for handler in (app_logging._file, app_logging._console):
        handler.flush()


def _occurrences(marker: str) -> int:
    """How many lines of ``app.log`` (and its rotated backups, in case the
    file rolled over mid-test) carry ``marker``."""
    _flush()
    files = [app_logging.LOG_FILE] + sorted(app_logging.LOG_DIR.glob("app.log.*"))
    return sum(
        path.read_text(encoding="utf-8", errors="replace").count(marker)
        for path in files if path.is_file()
    )


def test_a_plain_module_logger_s_warning_lands_in_app_log_once():
    marker = f"e7-plain-{uuid.uuid4().hex}"
    logging.getLogger("core.translator").warning("Translation failed (%s): Ollama unreachable", marker)
    assert _occurrences(marker) == 1


def test_a_plain_module_logger_s_error_lands_too_and_its_info_still_does_not():
    """WARNING and above: the root's level decides what a plain logger emits
    at all, and that is unchanged - its INFO was never written anywhere."""
    error, info = f"e7-error-{uuid.uuid4().hex}", f"e7-info-{uuid.uuid4().hex}"
    plain = logging.getLogger("services.updater")
    plain.error("git pull failed (%s)", error)
    plain.info("fetching (%s)", info)
    assert _occurrences(error) == 1
    assert _occurrences(info) == 0


def test_a_get_logger_warning_is_still_written_exactly_once():
    """Its own handlers write it; it never reaches the root, so the root's
    handler cannot write it a second time."""
    marker = f"e7-named-{uuid.uuid4().hex}"
    app_logging.get_logger("E7TEST").warning("A named warning (%s)", marker)
    assert _occurrences(marker) == 1


def test_what_a_get_logger_logger_writes_is_unchanged():
    """DEBUG and INFO from a ``get_logger`` logger still reach the file, in
    the file's format with the logger's name - nothing about those loggers
    changed."""
    marker = f"e7-debug-{uuid.uuid4().hex}"
    named = app_logging.get_logger("E7TEST")
    named.debug("A named debug line (%s)", marker)
    assert _occurrences(marker) == 1
    line = next(
        line for line in app_logging.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        if marker in line
    )
    assert "[mediastudio.E7TEST] DEBUG:" in line
    # (pytest's own capture handlers may sit beside these two during a test.)
    assert named.propagate is False
    assert app_logging._console in named.handlers and app_logging._file in named.handlers


def test_the_root_carries_one_forwarder_however_often_it_is_attached():
    """The root's handler is installed at import; installing it again (a
    re-import) replaces it rather than stacking another, which would write
    every plain warning twice."""
    app_logging._attach_to_root()
    app_logging._attach_to_root()
    marked = [h for h in logging.getLogger().handlers if getattr(h, "_mediastudio_app_log", False)]
    assert len(marked) == 1
    marker = f"e7-again-{uuid.uuid4().hex}"
    logging.getLogger("core.translator").warning("Attached again (%s)", marker)
    assert _occurrences(marker) == 1


# ── the console, once (the review's n8) ───────────────────────────────────────

@pytest.fixture
def console():
    """What the app's console handler prints, captured for the test."""
    buffer = io.StringIO()
    previous = app_logging._console.setStream(buffer)
    try:
        yield buffer
    finally:
        app_logging._console.setStream(previous)


def test_a_library_logger_with_its_own_handler_prints_once_and_still_reaches_app_log(console):
    """``kokoro_onnx``, ``phonemizer`` and ``huggingface_hub`` attach their own
    handler and still propagate: their own handler prints the line, so the
    root's forwarder must not print it again - but the file gets it, once."""
    marker = f"e7-lib-{uuid.uuid4().hex}"
    own = io.StringIO()
    library = logging.getLogger(f"e7lib{uuid.uuid4().hex[:6]}")
    handler = logging.StreamHandler(own)
    library.addHandler(handler)
    try:
        library.warning("library warning (%s)", marker)
    finally:
        library.removeHandler(handler)
    assert own.getvalue().count(marker) == 1, "printed by its own handler"
    assert console.getvalue().count(marker) == 0, "not printed a second time by the app's"
    assert _occurrences(marker) == 1, "in app.log once"


def test_a_plain_module_logger_prints_once_on_the_console(console):
    marker = f"e7-plain-console-{uuid.uuid4().hex}"
    logging.getLogger("core.translator.child").warning("plain (%s)", marker)
    assert console.getvalue().count(marker) == 1
    assert _occurrences(marker) == 1


# ── uvicorn's errors and a failed job's traceback (the review's m3) ──────────

@pytest.fixture
def uvicorn_logging():
    """uvicorn's own logging config applied as ``uvicorn.run`` applies it, and
    its loggers put back as they were afterwards."""
    import logging.config

    import uvicorn.config

    names = ("uvicorn", "uvicorn.error", "uvicorn.access")
    saved = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).level, logging.getLogger(n).propagate) for n in names}
    logging.config.dictConfig(uvicorn.config.LOGGING_CONFIG)
    try:
        yield
    finally:
        for name, (handlers, level, propagate) in saved.items():
            target = logging.getLogger(name)
            target.handlers[:] = handlers
            target.setLevel(level)
            target.propagate = propagate


def test_uvicorn_errors_reach_app_log_once_after_the_app_starts(uvicorn_logging, tmp_path, monkeypatch):
    """The traceback of an unhandled 500 is logged by ``uvicorn.error``, which
    does not propagate; the app's lifespan - after uvicorn has applied its own
    config, which replaces its loggers' handlers - adds the file. Started
    twice, it still writes the line once."""
    from fastapi.testclient import TestClient

    from api import store as auth_store
    from api.app import app

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    before = f"e7-uvicorn-before-{uuid.uuid4().hex}"
    logging.getLogger("uvicorn.error").warning("before the app started (%s)", before)
    assert _occurrences(before) == 0, "uvicorn's config alone writes nothing to app.log"

    with TestClient(app):
        pass
    with TestClient(app):
        marker = f"e7-uvicorn-{uuid.uuid4().hex}"
        try:
            raise RuntimeError(marker)
        except RuntimeError:
            logging.getLogger("uvicorn.error").exception("Exception in ASGI application (%s)", marker)
    _flush()
    lines = [line for line in app_logging.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines() if marker in line]
    assert len([line for line in lines if "Exception in ASGI application" in line]) == 1, lines
    assert len([line for line in lines if line.startswith("RuntimeError:")]) == 1, "its traceback, once"


def test_uvicorn_errors_are_written_once_when_nothing_stops_them_propagating(tmp_path, monkeypatch, console):
    """Served without uvicorn's own config (the test client, or ``log_config=None``),
    ``uvicorn.error`` propagates to the root: its file-only handler and the
    root's forwarder must not both write the line, and the console prints it
    once."""
    from fastapi.testclient import TestClient

    from api import store as auth_store
    from api.app import app

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    target, parent = logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn")
    saved = (list(target.handlers), target.propagate, list(parent.handlers), parent.propagate)
    target.handlers[:] = []
    parent.handlers[:] = []
    target.propagate = parent.propagate = True
    try:
        with TestClient(app):
            marker = f"e7-uvicorn-bare-{uuid.uuid4().hex}"
            target.warning("bare uvicorn (%s)", marker)
        assert _occurrences(marker) == 1
        assert console.getvalue().count(marker) == 1
    finally:
        target.handlers[:] = saved[0]
        target.propagate = saved[1]
        parent.handlers[:] = saved[2]
        parent.propagate = saved[3]


def test_a_failed_job_s_traceback_reaches_app_log_once():
    """A job that raises is logged through the app's logger with its
    traceback - ``traceback.print_exc()`` reached the console only."""
    import time

    from services import jobs

    marker = f"e7-job-{uuid.uuid4().hex}"

    def work(progress):
        raise RuntimeError(marker)

    job_id = jobs.submit("test", work)
    deadline = time.time() + 5
    while jobs.get(job_id)["status"] != "error":
        assert time.time() < deadline, "the job did not fail in time"
        time.sleep(0.01)
    time.sleep(0.05)
    _flush()
    text = app_logging.LOG_FILE.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in text.splitlines() if marker in line]
    assert len([line for line in lines if f"Job {job_id} (test) failed" in line]) == 1, lines
    assert len([line for line in lines if line.startswith("RuntimeError:")]) == 1, "its traceback, once"
    assert "[mediastudio.JOBS] ERROR" in next(line for line in lines if f"Job {job_id}" in line)
