"""Utility helper functions."""

import os
import shutil
import time
import hashlib
import functools
import uuid
from pathlib import Path
from utils.config import CACHE_DIR, FFMPEG_PATH
from utils.logger import get_logger

_retry_logger = get_logger("RETRY")

# The rename that publishes an atomically-written file, retried on a transient
# Windows refusal. Worst case ~0.6 s before the write really fails.
_REPLACE_ATTEMPTS = 8
_REPLACE_BACKOFF_SECONDS = 0.02


def replace_with_retry(tmp: Path, path: Path) -> None:
    """``os.replace(tmp, path)``, retried briefly on a Windows sharing refusal.

    The repo's ONE write-a-temp-then-rename idiom, and a second copy of a retry
    loop is a second thing to get wrong: ``services.projects.save_project``
    publishes project.json with it, ``services.narration`` publishes a finished
    preview clip onto its cache path with it, and :func:`publish_to_cache` below
    publishes the render's own clips with it.

    It lives HERE rather than in ``services.projects`` (which re-exports it, so
    every existing caller and every test that patches ``store.replace_with_retry``
    is unaffected) because the TTS generators in ``core`` need it too, and
    ``core`` must not import ``services``: the engine is the layer underneath.

    On Windows the rename fails with ERROR_ACCESS_DENIED (WinError 5) whenever
    anything else holds a handle to either file for the instant it takes: a
    real-time virus scanner opening the file we have just written (IObit and
    Defender both do - see ``core.audio_mixer._load_audio_with_retry``, which
    exists for the same reason), the search indexer, or a reader that opened
    the destination without FILE_SHARE_DELETE. It is transient and uncommon -
    2 in 60 in a concurrent loop on the development machine - and it is not a
    reason to fail a save: a few milliseconds later it succeeds. POSIX never
    takes this path.

    Retrying exposes nothing partial: the destination is either the old file or
    the new one throughout, and only the rename is repeated.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))


def publish_to_cache(source: Path, cache_path: Path) -> bool:
    """Put a finished clip at its shared TTS cache key, ATOMICALLY.

    Both generators used to mirror their output into the cache with a plain
    ``shutil.copy``, which is a byte-by-byte write straight onto the shared key.
    The only completeness check anywhere is that the file exists, so an
    interrupted copy - a crash, a full disk, a cancelled job, the process being
    closed - leaves a TRUNCATED entry at that key **forever**: nothing sweeps
    ``data/cache``, every later preview serves it, and every later re-voice
    copies it out, sees a file, and muxes the stump into the video as a
    successful sentence. Written aside and renamed, the key names either nothing
    or a complete clip.

    It was always a race the render could lose; what makes it worth fixing now
    is that the narration timeline drives it from a browser, so a re-voice, a
    row's Play and a whole-transcript audition can all be filling the same keys
    at once.

    Never raises. The cache is an optimisation: failing to memoise a clip that
    has already been synthesised successfully must not fail the synthesis, and
    before this a copy error was caught by the generator's own blanket
    ``except`` and turned into "this sentence produced no audio".

    Returns True when the entry is in place (including when another writer got
    there first with the same audio - the key is derived from the text, the
    voice and the speed, so an entry already sitting there is the same clip by
    construction).
    """
    source, cache_path = Path(source), Path(cache_path)
    tmp = cache_path.with_name(f"{cache_path.stem}.{uuid.uuid4().hex[:8]}.part")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, tmp)
        replace_with_retry(tmp, cache_path)
        return True
    except OSError as exc:
        if cache_path.exists() and cache_path.stat().st_size > 0:
            return True  # a racing writer published the same audio first
        _retry_logger.warning("Could not cache %s: %s", cache_path.name, exc)
        return False
    finally:
        # A no-op after a successful rename; the cleanup on every failure, so a
        # half-copied scratch file is never left in the cache directory.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


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


_libre_check_cache: list = [None]

def get_libreoffice_path() -> str | None:
    """Find the LibreOffice soffice executable."""
    if _libre_check_cache[0] is not None:
        return _libre_check_cache[0]
    path = shutil.which("soffice")
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


