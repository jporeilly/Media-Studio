"""Centralized logging for the application.

Replaces scattered print() calls with structured logging.
Log output goes to both console and a rotating log file in data/logs/.
"""

import logging
import logging.handlers

from utils.config import CONFIG_DIR

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
