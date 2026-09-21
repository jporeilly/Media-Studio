"""The music library: the files a project's music clips refer to by name.

One directory, ``services.styles.MUSIC_DIR`` (``<repo>/assets/music``, beside
``assets/finished`` where it survives a reinstall as the projects do; ``assets/``
is gitignored), and one index beside the files::

    assets/music/index.json
    {"files": {"<name>": {"size", "duration", "sample_rate", "channels",
                          "uploaded_at", "uploaded_by"}}}

The library is STUDIO-WIDE - shared by every account like the voices and the
studio settings; the audit row says who uploaded or deleted what (spec §12.3).

**A name is immutable and unique.** A clip (``services.edit``) refers to a file
by name, so an upload of an existing name is refused (``MusicExists``) rather
than replacing the file under every project that uses it, and a delete is
refused while any project's edit still names the file (``MusicInUse``, naming
the projects). A name that has gone is not an error on read: the edit reports
the clip as ``missing`` and the render refuses (trap 25) - never silence.

**Unique CASE-INSENSITIVELY, looked up EXACTLY.** Windows cannot hold
``bed.mp3`` and ``BED.mp3`` side by side, so an upload whose name matches an
indexed one, an on-disk one or one mid-upload under ``str.casefold`` is a
``MusicExists`` - on Linux as well, so the two behave the same and the index
can never hold two names differing only by case. Every lookup is then exact:
the indexed name is THE name, and a request that matches only by case is
``MusicNotFound`` and never touches the other file. (The delete's escape
hatch for a file left on disk without a row fires only for an on-disk name
that is EXACTLY the one asked for - ``Path.is_file()`` is true for
``BED.mp3`` when ``bed.mp3`` is there, and deleting by path on that evidence
removed a file a project's clip still named, leaving its index row behind.)

**Decoded once, at upload, and never probed** (traps 2 and 30). The file is
decoded through :func:`decode_audio` - the one seam - to prove it is audio and
to measure it: its length, sample rate and channel count go into the index and
its peaks (the waveform strip's own 125 ms buckets, ``services.waveform``) are
cached as ``<name>.peaks.json`` for the lane to draw inside a clip. Nothing
later opens the file to ask anything of it; the render reads the index and
hands ffmpeg the path.

The seam runs the resolved ``FFMPEG_PATH`` itself, into a scratch WAV the
stdlib :mod:`wave` module then reads, rather than ``pydub.AudioSegment.from_file``:
for anything but a ``.wav`` pydub first runs **ffprobe** (``mediainfo_json``,
with no fallback when the binary is absent), and the packaged app has no
ffprobe. One decode, one binary, the same numbers.

Every write is a ``.part`` published with ``utils.helpers.replace_with_retry``
(trap 10), and the index is read and written under one module lock.
"""

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from services import projects as store
from services import waveform
from services.styles import MUSIC_DIR
from utils import config as config_module
from utils.helpers import replace_with_retry, sanitize_filename
from utils.logger import get_logger

logger = get_logger("MUSIC")

# What an upload may be, by extension (case-insensitive; the stored name keeps
# the extension in lower case) and how each is served. The list is what ffmpeg
# decodes and a browser's ``decodeAudioData`` plays, which the audition needs.
ALLOWED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

# A bound on abuse, not on use: a five-minute bed is a few MB. The route
# answers 413 above it; the service refuses too, for a caller that is not the
# route.
MAX_MUSIC_BYTES = 100 * 1024 * 1024

# The name as stored, extension included.
MAX_NAME_CHARS = 120

# What a name may look like on the routes and in the index: no path separator
# either way round and no control character. Written without look-arounds so
# FastAPI's ``Path(pattern=...)`` (pydantic's Rust regex engine) can take the
# same string; the leading-dot, ``..`` and extension rules live in
# :func:`_name_problem`, which the service applies as well.
_NAME_CHARS = r"[^/\\\x00-\x1f\x7f]"
NAME_PATTERN = f"^{_NAME_CHARS}{{1,{MAX_NAME_CHARS}}}$"
_NAME_RE = re.compile(f"^{_NAME_CHARS}+$")

INDEX_NAME = "index.json"
PEAKS_SUFFIX = ".peaks.json"

# A decode is bounded by the file's length, not the output's, and a 100 MB
# upload is at most a couple of hours of audio; ten minutes is generous.
DECODE_TIMEOUT_SECONDS = 600
# A real-time scanner can hold a just-written file for an instant and ffmpeg
# then reports "Permission denied" (``core.audio_mixer._load_audio_with_retry``
# exists for the same reason). Retried briefly; anything else fails at once.
_DECODE_ATTEMPTS = 3
_DECODE_RETRY_SECONDS = 0.3

# ffmpeg prefixes each diagnostic with the component that printed it and its
# address (``[png @ 0000021b4c2f3bc0] chunk too big``); the prefix is noise to
# whoever uploaded the file and the address is noise to everyone.
_FFMPEG_TAG_RE = re.compile(r"^\[[^\]]*@[^\]]*\]\s*")
# A line carrying a filesystem path - the scratch WAV's, the ``.part``'s, any.
# The message names the UPLOAD and never a path on the server.
_PATH_LIKE_RE = re.compile(r"[A-Za-z]:[\\/]|\\|(?:^|\s)/\S*/")

# The index is read and written under this; an upload holds it only around the
# name check (which reserves the name with the ``.part``) and the publish, so a
# multi-second decode never blocks a listing.
_lock = threading.Lock()


class BadName(ValueError):
    """The upload's name is not one the library can store (400)."""


class NotAudio(ValueError):
    """The upload did not decode as audio (400)."""


class TooLarge(ValueError):
    """The upload is over ``MAX_MUSIC_BYTES`` (413 at the route)."""


class MusicExists(Exception):
    """A file of that name is already in the library (409): names are what
    clips refer to, so a silent replacement would change every project that
    uses it."""


class MusicNotFound(LookupError):
    """No file of that name in the library (404)."""


class MusicInUse(Exception):
    """The file is named by a project's edit and cannot be deleted (409).
    ``projects`` are the names of the projects that use it."""

    def __init__(self, name: str, projects: list[str]):
        self.name = name
        self.projects = projects
        count = len(projects)
        super().__init__(
            f"'{name}' is used by {count} project{'' if count == 1 else 's'} ({', '.join(projects)}); "
            "remove its clips from them first."
        )


@dataclass(frozen=True)
class Decoded:
    """What one decode of a file establishes: its length in seconds (to 3 dp),
    its sample rate and channel count as recorded in the index, and one 16-bit
    sample per frame for the peaks - mixed down to mono by the rule the
    waveform strip uses for a multi-channel WAV (per frame, the channel with
    the larger magnitude, sign kept), so the peaks a stereo file gets here
    are exactly the ones ``services.waveform.compute_peaks`` would draw for
    it."""

    duration: float
    sample_rate: int
    channels: int
    samples: np.ndarray


# -- the decode ----------------------------------------------------------------

def _decode_reason(stderr: str, *paths: str) -> str:
    """The first line of ffmpeg's stderr that says WHY, with its
    ``[png @ 0x…]`` prefix stripped and never a path.

    The message reaches the browser, where ``chunk too big`` is the whole of
    what is useful: the scratch WAV's name, the ``.part``'s and the server's
    directories mean nothing to whoever uploaded the file and are not theirs
    to see. A stderr that says nothing path-free leaves the refusal with no
    detail at all rather than a leak."""
    for raw in (stderr or "").splitlines():
        line = _FFMPEG_TAG_RE.sub("", raw.strip())
        if not line or _PATH_LIKE_RE.search(line) or any(held and held in line for held in paths):
            continue
        return line[:200]
    return ""


def _read_decoded(path: Path, label: str) -> Decoded:
    """A scratch WAV ffmpeg has just written, read whole into one mono int16
    array. Read in chunks so the interleaved bytes and the mix-down never
    hold more than one chunk over the answer itself. ``label`` is the name a
    refusal calls the file by - the upload's, never the scratch WAV's."""
    try:
        wf = wave.open(str(path), "rb")
    except Exception as exc:  # wave.Error, EOFError, OSError - all mean the same here
        # The exception text carries the scratch path; the log gets it, the
        # caller gets the upload's own name.
        logger.warning("The decode of %s could not be read back: %s", label, exc)
        raise NotAudio(f"'{label}' did not decode to audio this app can read.") from exc
    with wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        if width != 2 or channels < 1:
            raise NotAudio(f"'{label}' did not decode to 16-bit PCM.")
        frames = waveform.real_frame_count(wf, wf.getnframes(), width * channels)
        if not rate or frames <= 0:
            raise NotAudio(f"'{label}' decodes to no audio at all.")
        wf.rewind()
        mono = np.empty(frames, dtype=np.int16)
        done = 0
        while done < frames:
            raw = wf.readframes(min(waveform.CHUNK_FRAMES, frames - done))
            got = len(raw) // (width * channels)
            if got == 0:
                break
            data = np.frombuffer(raw[: got * width * channels], dtype="<i2")
            if channels > 1:
                data = data.reshape(-1, channels)
                # abs() first, then the louder channel per frame - the strip's
                # rule (``waveform._samples``); int32 because abs(-32768) does
                # not fit an int16.
                loudest = np.abs(data.astype(np.int32)).argmax(axis=1)
                data = data[np.arange(got), loudest]
            mono[done:done + got] = data
            done += got
        if done < frames:
            mono = mono[:done]
            frames = done
    return Decoded(round(frames / rate, 3), int(rate), int(channels), mono)


def decode_audio(path: Path, display_name: str | None = None) -> Decoded:
    """Decode ``path`` once and measure it, or ``NotAudio``.

    ``FFMPEG_PATH`` (never the bare name, trap 3) writes the file as 16-bit
    PCM at its own rate and channel count into a scratch WAV under
    ``utils.config.TEMP_DIR``, which is read and removed here; no ffprobe is
    ever run (see the module docstring for why pydub is not used). ``-vn``
    drops an attached picture (an MP3's cover art is a video stream to the
    WAV muxer) and ``-map_metadata -1`` keeps the tags out of the WAV.

    ``display_name`` is the name a refusal calls the file by. An upload is
    decoded as ``<name>.part``, so without it every 400 named a scratch file
    the user never chose; the message says ``'bed.mp3' is not an audio file
    this app can decode (chunk too big)`` and carries no server path.
    """
    path = Path(path)
    label = display_name or path.name
    ffmpeg = config_module.FFMPEG_PATH
    if not ffmpeg:
        raise NotAudio("ffmpeg is not available, so the file cannot be decoded.")
    config_module.TEMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="music-decode-", dir=str(config_module.TEMP_DIR)) as scratch:
        wav = Path(scratch) / "decoded.wav"
        cmd = [
            ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
            "-i", str(path), "-vn", "-map_metadata", "-1",
            "-c:a", "pcm_s16le", "-f", "wav", str(wav),
        ]
        for attempt in range(_DECODE_ATTEMPTS):
            try:
                run = subprocess.run(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, errors="replace", timeout=DECODE_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                raise NotAudio(
                    f"'{label}' took longer than {DECODE_TIMEOUT_SECONDS:.0f} s to decode and was given up on."
                ) from None
            except OSError as exc:
                raise NotAudio(f"ffmpeg could not be started: {exc}") from exc
            if run.returncode == 0:
                break
            tail = (run.stderr or "").strip()[-300:]
            if "permission denied" in tail.lower() and attempt < _DECODE_ATTEMPTS - 1:
                time.sleep(_DECODE_RETRY_SECONDS * (attempt + 1))
                continue
            logger.info("%s did not decode (exit %s): %s", label, run.returncode, tail)
            reason = _decode_reason(run.stderr, str(path), str(wav), scratch)
            raise NotAudio(
                f"'{label}' is not an audio file this app can decode" + (f" ({reason})" if reason else "") + "."
            )
        return _read_decoded(wav, label)


# -- names and paths -----------------------------------------------------------

def _name_problem(name: str) -> str | None:
    """Why ``name`` cannot be a library name, or None when it can. The one
    rule the upload, the routes and every path lookup share."""
    if not name:
        return "A music file needs a name."
    if not _NAME_RE.fullmatch(name):
        return "A music file's name may not contain a path separator or a control character."
    if len(name) > MAX_NAME_CHARS:
        return f"A music file's name is limited to {MAX_NAME_CHARS} characters."
    stem, ext = os.path.splitext(name)
    if ext.lower() not in ALLOWED_EXTENSIONS:
        return f"'{name}' is not a music file this app accepts; use one of {', '.join(ALLOWED_EXTENSIONS)}."
    if not stem.strip():
        return "A music file needs a name before its extension."
    if stem.startswith("."):
        return "A music file's name may not start with a dot."
    return None


def valid_name(name: str) -> bool:
    """Whether ``name`` could be a library file at all - what the routes and
    the path lookups check before ``MUSIC_DIR`` is ever joined to it."""
    return isinstance(name, str) and _name_problem(name) is None


def checked_name(raw: str) -> str:
    """The name an upload is stored under: ``utils.helpers.sanitize_filename``
    over what the client sent (separators and the other characters Windows
    refuses become ``_``), the extension lower-cased, and then the rules of
    :func:`_name_problem` - or ``BadName``."""
    name = sanitize_filename(str(raw or ""))
    stem, ext = os.path.splitext(name)
    name = f"{stem}{ext.lower()}"
    problem = _name_problem(name)
    if problem:
        raise BadName(problem)
    return name


def media_type_of(name: str) -> str:
    return MEDIA_TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")


def _path_for(name: str) -> Path:
    """``MUSIC_DIR/name``, confirmed to sit directly inside the directory;
    anything else is answered as "no such file".

    The directory is created FIRST and the path resolved ONCE. Two
    ``Path.resolve()`` calls around a directory that is springing into
    existence disagree on Windows - the second answers in the
    ``\\\\?\\``-prefixed extended-length form - so a perfectly ordinary name
    was "no such file" for as long as the window lasted: 108 of 150
    concurrent first uploads into an ``assets/music`` that did not exist yet,
    each a 500 from ``POST /api/music``. ``normcase(abspath(...))`` compares
    the one form; a validated name carries no separator and is never ``.``
    or ``..``, so the check is belt to that and must never fire for a good
    name."""
    if not valid_name(name):
        raise MusicNotFound(f"No music file named '{name}'.")
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    path = Path(os.path.abspath(str(MUSIC_DIR / name)))
    if os.path.normcase(str(path.parent)) != os.path.normcase(os.path.abspath(str(MUSIC_DIR))):
        raise MusicNotFound(f"No music file named '{name}'.")
    return path


def _peaks_path(name: str) -> Path:
    return _path_for(name).with_name(f"{name}{PEAKS_SUFFIX}")


def _on_disk_names() -> list[str]:
    """The library directory's entries as they are spelled ON DISK. Windows
    keeps the case a file was created with and reports it here, which is what
    makes an exact-name check possible on a file system that matches without
    one."""
    try:
        return os.listdir(MUSIC_DIR)
    except OSError:  # no directory yet, or it cannot be listed: nothing is there
        return []


def _is_on_disk(name: str) -> bool:
    """Whether a file spelled EXACTLY ``name`` is in the library directory.
    ``Path.is_file()`` is not this question on Windows: it answers True for
    ``BED.mp3`` when ``bed.mp3`` is what is there."""
    return name in _on_disk_names()


def _name_is_taken(name: str, files: dict) -> bool:
    """Whether ``name`` collides with an indexed name, a file on disk or an
    upload in flight (its ``.part``) - compared with ``str.casefold``, since
    Windows cannot hold two names that differ only by case and the index must
    not either, whatever the platform. The caller holds ``_lock``."""
    folded = name.casefold()
    if any(held.casefold() == folded for held in files):
        return True
    for entry in _on_disk_names():
        held = entry[:-len(".part")] if entry.endswith(".part") else entry
        if held.casefold() == folded:
            return True
    return False


def _index_path() -> Path:
    return MUSIC_DIR / INDEX_NAME


# -- the index -----------------------------------------------------------------

def _read_index() -> dict:
    """``{name: entry}``; the caller holds ``_lock``. A missing index is an
    empty library; an unreadable one is answered the same way with a warning
    rather than a 500 on every route (the files are still on disk, a
    re-upload of a name they hold is refused by the file's presence, and a
    delete removes them)."""
    path = _index_path()
    if not path.is_file():
        return {}
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        files = held.get("files") if isinstance(held, dict) else None
        if isinstance(files, dict):
            return files
    except (OSError, ValueError):
        pass
    logger.warning("%s is unreadable; the library reads as empty until the next upload rewrites it", path)
    return {}


def _write_json(target: Path, payload: dict) -> None:
    """Write aside and publish (trap 10)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.part")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    replace_with_retry(tmp, target)


def _write_index(files: dict) -> None:
    """The caller holds ``_lock``."""
    _write_json(_index_path(), {"files": files})


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


# -- the library ---------------------------------------------------------------

def add_file(name: str, data: bytes, uploaded_by: str) -> dict:
    """Store ``data`` as ``name`` (sanitised, see :func:`checked_name`) and
    return its index entry with ``name`` on it.

    Refuses a bad name (``BadName``), a body over the cap (``TooLarge``), an
    existing name (``MusicExists`` - a name in the index, on disk, or being
    uploaded right now, matched CASE-INSENSITIVELY so the library never holds
    two names Windows cannot tell apart) and anything that does not decode
    (``NotAudio``).
    The body is written to ``<name>.part`` under the lock, which reserves the
    name; the decode runs outside the lock; the peaks are cached; then the
    file is published and the index row written under the lock again. A
    failure at any point removes what this call wrote and leaves the index
    as it was.
    """
    name = checked_name(name)
    if len(data) > MAX_MUSIC_BYTES:
        raise TooLarge(f"A music file is limited to {MAX_MUSIC_BYTES // (1024 * 1024)} MB.")
    path = _path_for(name)
    part = path.with_name(f"{name}.part")
    peaks_path = _peaks_path(name)

    with _lock:
        if _name_is_taken(name, _read_index()):
            raise MusicExists(f"'{name}' is already in the library; rename the file or delete the old one.")
        part.write_bytes(data)  # _path_for created the directory

    published = False
    try:
        decoded = decode_audio(part, name)
        peaks = waveform.peaks_from_samples(decoded.samples, decoded.sample_rate)
        _write_json(peaks_path, waveform.peaks_payload(peaks, decoded.duration, decoded.sample_rate))
        entry = {
            "size": len(data),
            "duration": decoded.duration,
            "sample_rate": decoded.sample_rate,
            "channels": decoded.channels,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "uploaded_by": uploaded_by,
        }
        with _lock:
            replace_with_retry(part, path)
            published = True
            files = _read_index()
            files[name] = entry
            _write_index(files)
    except BaseException:
        _discard(part)
        _discard(peaks_path)
        if published:
            _discard(path)
        raise
    logger.info("Music added: %s (%.3fs, %d Hz, %d ch, %d bytes) by %s",
                name, decoded.duration, decoded.sample_rate, decoded.channels, len(data), uploaded_by)
    return {"name": name, **entry}


def list_files() -> list[dict]:
    """Every index entry with its ``name``, sorted by name."""
    with _lock:
        files = _read_index()
    return [{"name": name, **files[name]} for name in sorted(files)]


def library() -> dict[str, float]:
    """``{name: duration}`` - what a clip list is validated against
    (``services.edit.validate_music``)."""
    with _lock:
        files = _read_index()
    return {name: float(entry.get("duration") or 0.0) for name, entry in files.items()}


def duration_of(name: str) -> float | None:
    """The file's recorded length, or None when it is not in the library."""
    return library().get(name)


def get_path(name: str) -> Path:
    """The file on disk for a name in the index, or ``MusicNotFound``.

    Exact, both ways: the name must be the one the index is keyed by AND the
    one the file is spelled with on disk, so a request for ``BED.mp3`` is a
    404 rather than ``bed.mp3`` served (or, at the delete, removed) under a
    name the library never held."""
    path = _path_for(name)
    with _lock:
        known = name in _read_index()
    if not known or not _is_on_disk(name):
        raise MusicNotFound(f"No music file named '{name}'.")
    return path


def peaks(name: str) -> dict:
    """The cached peaks payload (the waveform response shape). Computed at
    upload; recomputed from the file - one decode - only if the cache has
    gone or cannot be read, and re-cached best-effort."""
    path = get_path(name)
    cache = _peaks_path(name)
    if cache.is_file():
        try:
            held = json.loads(cache.read_text(encoding="utf-8"))
            return {k: held[k] for k in ("buckets", "bucket_seconds", "duration", "sample_rate", "peaks")}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    decoded = decode_audio(path, name)
    payload = waveform.peaks_payload(
        waveform.peaks_from_samples(decoded.samples, decoded.sample_rate), decoded.duration, decoded.sample_rate,
    )
    try:
        _write_json(cache, payload)
    except OSError:
        pass  # the answer is computed and correct; failing to memoise it is not a reason to fail
    return payload


def references(name: str) -> list[str]:
    """The names of the projects whose edit has a music clip on ``name`` -
    what a delete is refused with. A scan of every record's ``edit.music``
    (a few files, milliseconds); a record whose edit is not readable as a
    clip list simply names nothing."""
    found: list[str] = []
    for record in store.list_projects():
        held = record.get("edit")
        clips = held.get("music") if isinstance(held, dict) else None
        if not isinstance(clips, list):
            continue
        if any(isinstance(clip, dict) and clip.get("file") == name for clip in clips):
            found.append(str(record.get("name") or record.get("id") or "?"))
    return found


def remove(name: str) -> None:
    """Delete ``name``: its file, its peaks and its index row. Refused with
    ``MusicInUse`` (naming the projects) while any project's edit names it;
    ``MusicNotFound`` when there is nothing of that name in the index or on
    disk.

    A file left on disk without a row - an interrupted upload, or an index
    that could not be read - is removable, so a name never gets stuck; but
    only when the on-disk name is EXACTLY the one asked for. ``is_file()``
    was that evidence once, and on Windows it is true for ``BED.mp3`` when
    ``bed.mp3`` is what is there: the row check ("not in the index") passed,
    ``references`` compared the string nobody stores and found nothing, and
    the file a project's clip named was unlinked by path - a 204, a deleted
    file and a phantom row."""
    path = _path_for(name)
    with _lock:
        files = _read_index()
        if name not in files and not _is_on_disk(name):
            raise MusicNotFound(f"No music file named '{name}'.")
        used = references(name)
        if used:
            raise MusicInUse(name, used)
        _discard(path)
        _discard(_peaks_path(name))
        if name in files:
            files.pop(name)
            _write_index(files)
    logger.info("Music removed: %s", name)
