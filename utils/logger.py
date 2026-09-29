"""Centralized logging for the application.

Replaces scattered print() calls with structured logging.
Log output goes to both console and a rotating log file in data/logs/:
everything a ``get_logger`` logger writes (DEBUG and above), and every other
module's warnings and errors, which reach the file through the root logger
(``_EveryModuleToAppLog``).
"""

import logging
import logging.handlers

from utils.config import CONFIG_DIR, FFMPEG_PAIR_WARNING, FFPROBE_MISSING_WARNING

# Log directory inside the portable data folder
LOG_DIR = CONFIG_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# Formatter: [TAG-LEVEL] timestamp - message
_FMT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

# Console handler — INFO and above
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("[%(name)s] %(message)s"))

# File handler — DEBUG and above, rotates at 5 MB, keeps 3 backups
_file = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
)
_file.setLevel(logging.DEBUG)
_file.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))


def get_logger(name: str) -> logging.Logger:
    """Get a named logger with console + file handlers pre-attached."""
    logger = logging.getLogger(f"mediastudio.{name}")
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        logger.addHandler(_console)
        logger.addHandler(_file)
        logger.propagate = False
    return logger


class _EveryModuleToAppLog(logging.Handler):
    """The ROOT logger's handler: every record at WARNING and above that
    reaches the root is written to ``app.log`` (and the console) through the
    same two handlers ``get_logger`` attaches.

    Some modules log with a plain ``logging.getLogger`` - ``core.translator``,
    ``services.updater``, the AI engine modules under ``core`` - and a plain
    logger has no handler of its own: its records propagate to the root,
    which had none either, so Python's last-resort handler printed their
    warnings on stderr and ``app.log`` never saw them. This forwards those
    records to ``_file`` and ``_console`` rather than opening the file a
    second time (one handler object owns the file and its rotation).

    Exactly once: a ``get_logger`` logger does not propagate, so its records
    never reach the root and are written by its own handlers as before; a
    plain logger has none, so it is written here and only here. Below
    WARNING nothing changes - a plain logger inherits the root's WARNING
    level, so its INFO and DEBUG records were never emitted at all.

    Once on the console too: a library logger that brings its own handler
    and still propagates (``kokoro_onnx``, ``phonemizer``,
    ``huggingface_hub``) has already printed the line by the time it reaches
    the root, so for such a record only the file is written here; and a
    record ``uvicorn.error``'s file-only handler has already written is not
    written again (``_handled_on_the_way``).
    """

    # Marks the instance so a re-import of this module replaces it rather
    # than stacking a second one (which would write every record twice).
    MARK = "_mediastudio_app_log"

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        setattr(self, self.MARK, True)

    def emit(self, record: logging.LogRecord) -> None:
        # ``handle`` takes each handler's own lock and filters; the level was
        # already decided by this handler's.
        shown, written = _handled_on_the_way(record)
        if not shown:
            _console.handle(record)
        if not written:
            _file.handle(record)


def _handled_on_the_way(record: logging.LogRecord) -> tuple[bool, bool]:
    """What the handlers between the record's own logger and the root already
    did with it on its way up (it propagated, or it would not be here):
    ``(shown, written)`` - printed by a handler of its own (a library's), and
    written to ``app.log`` (``uvicorn.error``'s file-only handler, when uvicorn
    has not stopped that logger propagating)."""
    shown = written = False
    logger = logging.getLogger(record.name) if record.name != "root" else None
    root = logging.getLogger()
    while logger is not None and logger is not root:
        for handler in logger.handlers:
            if handler is _file or getattr(handler, _ToAppLogOnly.MARK, False):
                written = True
            else:
                shown = True
        logger = logger.parent
    return shown, written


def _attach_to_root() -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _EveryModuleToAppLog.MARK, False):
            root.removeHandler(handler)
    root.addHandler(_EveryModuleToAppLog())


_attach_to_root()


class _ToAppLogOnly(logging.Handler):
    """uvicorn's ``uvicorn.error`` logger - the traceback of an unhandled 500
    ("Exception in ASGI application") and the server's own warnings - into
    ``app.log`` at WARNING and above. Only the file: uvicorn's own handler
    already prints it on the console, and the logger does not propagate, so
    the root's handler never sees it."""

    MARK = "_mediastudio_uvicorn_to_app_log"

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        setattr(self, self.MARK, True)

    def emit(self, record: logging.LogRecord) -> None:
        _file.handle(record)


UVICORN_ERROR = "uvicorn.error"


def attach_to_uvicorn() -> None:
    """Send ``uvicorn.error``'s warnings and errors to ``app.log`` as well.

    Called from the app's lifespan (``api.app``), i.e. AFTER ``uvicorn.run``
    has applied its logging config: that ``dictConfig`` replaces the handlers
    of every uvicorn logger, so a handler attached when this module is
    imported (``main.py`` imports the app before it starts uvicorn) would be
    gone by the first request. Idempotent: a second call replaces the handler
    rather than adding another, so a line is never written twice."""
    target = logging.getLogger(UVICORN_ERROR)
    for handler in list(target.handlers):
        if getattr(handler, _ToAppLogOnly.MARK, False):
            target.removeHandler(handler)
    target.addHandler(_ToAppLogOnly())


# The startup warnings utils.config works out but cannot log itself (this
# module imports it, so it cannot import this one back). Emitted here, once per
# process, into app.log as well as the console. Both describe a 0.9.0 install
# updated in place: running the shipped ffmpeg with a prober from somewhere
# else, or with no prober at all.
if FFMPEG_PAIR_WARNING:
    get_logger("CONFIG").warning(FFMPEG_PAIR_WARNING)
if FFPROBE_MISSING_WARNING:
    get_logger("CONFIG").warning(FFPROBE_MISSING_WARNING)
