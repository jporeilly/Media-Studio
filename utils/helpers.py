"""Utility helper functions."""

import os
import time
import hashlib
import functools
from pathlib import Path
from utils.config import CACHE_DIR, FFMPEG_PATH
from utils.logger import get_logger

_retry_logger = get_logger("RETRY")


def retry(max_attempts: int = 3, base_delay: float = 2.0, exceptions: tuple = (Exception,)):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        _retry_logger.warning(
                            "%s attempt %d/%d failed: %s — retrying in %.1fs",
                            func.__name__, attempt, max_attempts, exc, delay,
                        )
                        time.sleep(delay)
                    else:
                        _retry_logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_attempts, exc,
                        )
            raise last_exc
        return wrapper
    return decorator


def get_cache_path(
    text: str, voice_id: str, extension: str = ".mp3", speed: float = 1.0,
    stability: float = 0.5, similarity_boost: float = 0.75, style: float = 0.0,
) -> Path:
    """Generate a cache file path based on text, voice ID, and voice settings.

    The same text + voice + settings combination always maps to the same file,
    so re-processing a presentation with unchanged notes reuses cached audio.
    """
    content = f"{text}:{voice_id}:{speed:.2f}:{stability:.2f}:{similarity_boost:.2f}:{style:.2f}"
    hash_value = hashlib.md5(content.encode()).hexdigest()
    return CACHE_DIR / f"{hash_value}{extension}"


def format_duration(seconds: float) -> str:
    """Format duration in seconds to MM:SS format."""
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def sanitize_filename(filename: str) -> str:
    """Remove invalid characters from filename."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")
    return filename.strip()


def get_output_filename(input_path: Path, output_dir: Path) -> Path:
    """Generate output filename for video based on input PowerPoint.

    Overwrites the previous video if it exists (same PPTX = same output).
    """
    base_name = sanitize_filename(input_path.stem)
    return output_dir / f"{base_name}.mp4"


def check_ffmpeg_installed() -> bool:
    """Check if FFmpeg is available (system PATH or imageio-ffmpeg bundle)."""
    return FFMPEG_PATH is not None


def check_ollama_installed() -> bool:
    """Check if Ollama is reachable at the configured URL."""
    try:
        import requests
        from utils.config import config
        url = config.ollama_url.rstrip("/") + "/api/tags"
        resp = requests.get(url, timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def check_libreoffice_installed() -> bool:
    """Check if LibreOffice is installed."""
    return get_libreoffice_path() is not None


_ppt_check_cache = [None]

def check_powerpoint_installed() -> bool:
    """Check if Microsoft PowerPoint is installed (Windows only).

    Attempts to instantiate PowerPoint via COM automation. If it succeeds,
    animations can be exported; otherwise the app falls back to static images.
    Result is cached after the first check.
    """
    if _ppt_check_cache[0] is not None:
        return _ppt_check_cache[0]

    if os.name != 'nt':
        _ppt_check_cache[0] = False
        return False

    try:
        import win32com.client
        ppt = win32com.client.Dispatch("PowerPoint.Application")
        ppt.Quit()
        _ppt_check_cache[0] = True
        return True
    except Exception:
        _ppt_check_cache[0] = False
        return False


import shutil as _shutil_helpers

_libre_check_cache: list = [None]

def get_libreoffice_path() -> str | None:
    """Find the LibreOffice soffice executable."""
    if _libre_check_cache[0] is not None:
        return _libre_check_cache[0]
    path = _shutil_helpers.which("soffice")
    if path:
        _libre_check_cache[0] = path
        return path
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "LibreOffice" / "program" / "soffice.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "LibreOffice" / "program" / "soffice.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                _libre_check_cache[0] = str(candidate)
                return str(candidate)
    for p in ["/usr/bin/soffice", "/usr/local/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"]:
        if Path(p).exists():
            _libre_check_cache[0] = p
            return p
    _libre_check_cache[0] = None
    return None


