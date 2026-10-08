"""Screen recordings (T3): from the recorder's WebM chunks to a video project.

The desktop shell records in the page with ``MediaRecorder`` (VP9/Opus WebM,
the only route to system sound without a driver) and hands the chunks here
as they are made, every few seconds, so a crash or a closed window loses at
most the last few seconds: nothing is kept in the page's memory beyond the
chunk in flight. A recording lives under ``data/temp/recordings/<id>/`` as
``meta.json`` plus one ``chunk-NNNNNN.webm`` per chunk until it is saved.

**Chunks.** Each is named by its sequence number and written atomically
(``.part`` then ``os.replace``). Out of order is fine - the names sort. A
duplicate replaces the earlier copy (the recorder resends a chunk whose
upload failed, and the bytes are the same). A chunk is refused (``NoRoom``,
507) when less than :data:`MIN_CHUNK_FREE_BYTES` would be left on the disk,
and (``TooLarge``, 413) when the recording would pass
:data:`MAX_RECORDING_BYTES` - the recorder then stops and what arrived is kept.

**Live.** A recording is LIVE while its recorder is making it: its status is
``recording`` and the server has heard from it - a chunk, or the heartbeat the
recorder sends every few seconds, paused or not - within
:data:`LIVE_WINDOW_SECONDS`. While live it can be saved or discarded only by
its recorder (``from_recorder``); the Projects page cannot take a recording
from under the recorder that is still making it. A recorder that crashed goes
quiet, and its recording becomes savable from the page; one that failed, or
whose chunk was refused for the disk or the cap, lets go of it at once
(:func:`release`), keeping what arrived.

**Saving** is a job (kind ``recording``, one at a time:
``jobs.start_single``), and while that job lives the recording's status is
``finishing`` - only while it lives: a save interrupted by a restart is reset
to ``stopped`` (:func:`reset_stale`, at startup) and offered again. The job
turns the chunks - read in place by ffmpeg's ``concatf:`` protocol, so no
assembled copy is ever written - into the MP4 every other file the app makes
is (H.264 High 4:2:0 at the source size and frame rate, AAC 48 kHz stereo at
192 kbit/s, ``+faststart``: ``core.video_creator``'s own constants), cut to the
region when one was chosen, its last frame held to the sound's end (a screen
still at the end sends no frames), and imports it as a **video project** named after
the window (or "Screen recording <date time>"). The chunks are deleted once
the project exists. The conversion's wait comes from what the server holds
(the chunks times the recorder's timeslice), never from a duration of 0.

**Holes.** A save that names a count of chunks with a gap below it is
refused, naming the gap, so the recorder can resend. A recording whose gap
can no longer be filled can be saved as the part BEFORE the gap - the count
up to the gap - and only with ``accept_loss``, which says the pieces after it
are given up; without it nothing is silently dropped.

The two audio sources - system sound and the microphone - were mixed AT
UNITY in the page; nothing here changes their balance.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from services import jobs
from services import projects as store
from utils.config import FFMPEG_PATH, TEMP_DIR
from utils.helpers import replace_with_retry
from utils.logger import get_logger

log = get_logger("RECORDINGS")

RECORDINGS_DIR = TEMP_DIR / "recordings"
#: The job kind the save runs under; the Projects page follows it.
JOB_KIND = "recording"

GIB = 1024 ** 3
#: A recording is stopped by the recorder at this length (the brief's two
#: hours); the server accepts what it is sent.
MAX_RECORDING_SECONDS = 2 * 60 * 60
#: Refuse to START with less free space than this on the recordings' disk.
MIN_FREE_BYTES = 2 * GIB
#: Refuse a CHUNK that would leave less than this free: the recorder stops and
#: keeps what arrived, and the save still has room for its MP4.
MIN_CHUNK_FREE_BYTES = 1 * GIB
#: One recording's chunks together. The recorder asks for 8 Mbit/s of video
#: and 192 kbit/s of sound - about 7.4 GB for two hours, at 4K too, since the
#: rate is the recorder's, not the picture's - so 16 GiB leaves room for an
#: encoder that overshoots and still bounds the disk a recording can take.
MAX_RECORDING_BYTES = 16 * GIB
#: Bounds on abuse: more chunks than two hours at one a second, and a chunk
#: larger than any few seconds of screen video could be.
MAX_CHUNKS = 8_000
MAX_CHUNK_BYTES = 256 * 1024 * 1024
MAX_NAME_CHARS = 120
#: The recorder's frame rate, clamped to what the MP4 is given.
MIN_FRAME_RATE, MAX_FRAME_RATE = 1, 60
DEFAULT_FRAME_RATE = 30
#: The recorder's chunk length (``TIMESLICE_MS`` in the page), sent at start.
DEFAULT_TIMESLICE_MS = 5000
MIN_TIMESLICE_MS, MAX_TIMESLICE_MS = 1000, 60_000
#: Heard from within this long - a chunk or a heartbeat - a recording is live.
LIVE_WINDOW_SECONDS = 20.0
#: The conversion's wait: a floor plus the recording's own length and a half -
#: a ten-minute 1080p recording converted in 57 s on this machine, so this is
#: far from tight, and a machine that cannot keep up still ends.
TRANSCODE_BASE_SECONDS = 300.0
TRANSCODE_SECONDS_PER_SECOND = 1.5
#: The list of chunk files ffmpeg reads (``concatf:``), in the recording's folder.
CHUNK_LIST = "chunks.txt"

_RID_RE = re.compile(r"^[0-9a-f]{12}$")
_CHUNK_RE = re.compile(r"^chunk-(\d{6})\.webm$")
_UNSAFE = re.compile(r"[^A-Za-z0-9 _.-]+")

# One lock per recording: every read-modify-write of its meta.json (a chunk's
# "last seen", a save's status, a discard) takes it.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(rid: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(rid, threading.Lock())


def _now() -> float:
    return time.time()


class RecordingError(RuntimeError):
    """The recording cannot be saved as asked; the message is for the user."""


class NoRoom(RecordingError):
    """Not enough free disk space (507)."""


class TooLarge(RecordingError):
    """The recording would pass :data:`MAX_RECORDING_BYTES` (413)."""


class Live(RecordingError):
    """The recording is still being made by its recorder (409)."""


class Busy(RecordingError):
    """The recording is being saved (409)."""


class WouldLose(RecordingError):
    """Saving as asked would drop chunks after a gap; ``accept_loss`` says so (409)."""


def _valid(rid: str) -> bool:
    return bool(_RID_RE.fullmatch(rid or ""))


def _dir(rid: str) -> Path:
    return RECORDINGS_DIR / rid


def _meta_path(rid: str) -> Path:
    return _dir(rid) / "meta.json"


def read_meta(rid: str) -> dict | None:
    if not _valid(rid):
        return None
    path = _meta_path(rid)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_meta(rid: str, meta: dict) -> None:
    path = _meta_path(rid)
    tmp = path.with_name(f"meta.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    try:
        replace_with_retry(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def free_bytes(path: Path = RECORDINGS_DIR) -> int:
    """Free space on the disk the recordings go to."""
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def default_name(now: datetime | None = None) -> str:
    """"Screen recording 2026-10-06 11-42" - local time, minute precision,
    with characters every file system takes."""
    now = now or datetime.now()
    return f"Screen recording {now.strftime('%Y-%m-%d %H-%M')}"


def safe_stem(name: str, fallback: str = "screen-recording", limit: int = 60) -> str:
    """A file stem from a window title: what every file system accepts, spaces
    to dashes, trimmed, bounded; ``fallback`` when nothing is left."""
    cleaned = _UNSAFE.sub("", name or "").strip().strip(".")
    cleaned = re.sub(r"\s+", "-", cleaned).strip("-")
    return (cleaned[:limit].rstrip("-.") or fallback)


# -- states --------------------------------------------------------------------------

def is_live(meta: dict, now: float | None = None) -> bool:
    """Being made by its recorder: status ``recording`` and heard from within
    :data:`LIVE_WINDOW_SECONDS`."""
    now = _now() if now is None else now
    return meta.get("status") == "recording" and now - float(meta.get("last_seen") or 0) < LIVE_WINDOW_SECONDS


def is_saving(meta: dict) -> bool:
    """Being saved: status ``finishing`` AND its job is alive. A ``finishing``
    left by a job that no longer exists (a restart, a crash) is not saving."""
    if meta.get("status") != "finishing":
        return False
    job = jobs.get(meta.get("job_id") or "")
    return bool(job and job["status"] in jobs.ACTIVE_STATUSES)


def effective_status(meta: dict, now: float | None = None) -> str:
    """What the page is told: ``finishing`` while a save runs, ``recording``
    while live, ``stopped`` otherwise (savable and discardable)."""
    if is_saving(meta):
        return "finishing"
    if is_live(meta, now):
        return "recording"
    return "stopped"


def reset_stale() -> int:
    """At startup: no job survives a restart, so every ``finishing`` record is
    a save that was interrupted - set back to ``stopped`` so it is offered
    again. Returns how many were reset.

    Best-effort, recording by recording: one whose ``meta.json`` cannot be
    rewritten (read-only, held open) is logged and left as it is - it is
    still offered, since ``effective_status`` reports a ``finishing`` record
    with no live job as ``stopped`` - and the others are reset. Nothing is
    raised: the app's startup calls this, and one recording must never stop
    the app from starting."""
    try:
        entries = list(RECORDINGS_DIR.iterdir()) if RECORDINGS_DIR.is_dir() else []
    except OSError as exc:
        log.warning("Could not list the recordings in %s: %s", RECORDINGS_DIR, exc)
        return 0
    reset = 0
    for d in entries:
        if not _valid(d.name):
            continue
        try:
            with _lock(d.name):
                meta = read_meta(d.name)
                if meta and meta.get("status") == "finishing" and not is_saving(meta):
                    meta["status"], meta["job_id"] = "stopped", None
                    _write_meta(d.name, meta)
                    reset += 1
        except OSError as exc:
            log.warning("Could not reset the interrupted save of recording %s (it is still offered): %s", d.name, exc)
    if reset:
        log.info("Reset %d recording save(s) interrupted by a restart", reset)
    return reset


# -- making -----------------------------------------------------------------------------

def start(*, user_id: str, owner_name: str | None, name: str, mime: str, frame_rate: float,
          width: int, height: int, source: dict, timeslice_ms: int = DEFAULT_TIMESLICE_MS) -> dict:
    """Begin a recording: its directory and ``meta.json``, live from now.
    Refuses a region that is not inside its monitor (``ValueError``: the crop
    would be moved or fail) and, under :data:`MIN_FREE_BYTES` free,
    :class:`NoRoom`."""
    problem = region_problem(source)
    if problem:
        raise ValueError(problem)
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    free = free_bytes()
    if free < MIN_FREE_BYTES:
        raise NoRoom(
            f"Only {free / GIB:.1f} GB free on the disk Media Studio records to; "
            f"at least {MIN_FREE_BYTES // GIB} GB is needed to start a recording."
        )
    rid = uuid.uuid4().hex[:12]
    _dir(rid).mkdir(parents=True, exist_ok=True)
    meta = {
        "id": rid,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "owner_id": user_id,
        "owner_name": owner_name,
        "name": (name or "").strip()[:MAX_NAME_CHARS] or default_name(),
        "mime": mime,
        "frame_rate": float(frame_rate) if frame_rate else DEFAULT_FRAME_RATE,
        "width": int(width),
        "height": int(height),
        "source": source,
        "timeslice_ms": int(min(MAX_TIMESLICE_MS, max(MIN_TIMESLICE_MS, timeslice_ms or DEFAULT_TIMESLICE_MS))),
        "status": "recording",
        "last_seen": _now(),
        "job_id": None,
    }
    _write_meta(rid, meta)
    return meta


def _seqs(rid: str) -> list[int]:
    return [int(_CHUNK_RE.match(p.name).group(1)) for p in chunks_present(rid)]


def put_chunk(rid: str, seq: int, data: bytes) -> dict:
    """Store chunk ``seq`` atomically; a duplicate replaces the earlier copy.
    Refuses a chunk of a recording being saved (:class:`Busy`), one that would
    leave less than :data:`MIN_CHUNK_FREE_BYTES` free (:class:`NoRoom`) or take
    the recording past :data:`MAX_RECORDING_BYTES` (:class:`TooLarge`). Marks
    the recording heard from. Returns ``{"received": <count>, "bytes": <total>}``."""
    if not 0 <= seq < MAX_CHUNKS:
        raise ValueError(f"Chunk numbers run from 0 to {MAX_CHUNKS - 1}.")
    if not data:
        raise ValueError("An empty chunk was sent.")
    if len(data) > MAX_CHUNK_BYTES:
        raise ValueError(f"A chunk may not exceed {MAX_CHUNK_BYTES // 1024 ** 2} MB.")
    with _lock(rid):
        meta = read_meta(rid)
        if meta is None:
            raise LookupError("No such recording.")
        if is_saving(meta):
            raise Busy("This recording is being saved; it takes no more chunks.")
        final = _dir(rid) / f"chunk-{seq:06d}.webm"
        replaced = final.stat().st_size if final.is_file() else 0
        held = sum(p.stat().st_size for p in chunks_present(rid)) - replaced
        if held + len(data) > MAX_RECORDING_BYTES:
            raise TooLarge(
                f"The recording reached {MAX_RECORDING_BYTES // GIB} GB, the most one recording may take; "
                "it was stopped and what arrived is kept."
            )
        if free_bytes() - len(data) < MIN_CHUNK_FREE_BYTES:
            raise NoRoom(
                f"Less than {MIN_CHUNK_FREE_BYTES // GIB} GB is left on the disk Media Studio records to, so the "
                "recording was stopped; what arrived is kept. Free some space, then save it from the Projects page."
            )
        tmp = final.with_suffix(f".{uuid.uuid4().hex[:8]}.part")
        tmp.write_bytes(data)
        replace_with_retry(tmp, final)
        meta["last_seen"] = _now()
        _write_meta(rid, meta)
        present = chunks_present(rid)
        return {"received": len(present), "bytes": sum(p.stat().st_size for p in present)}


def heartbeat(rid: str) -> None:
    """The recorder is still making this recording (paused or between
    chunks): mark it heard from. Ignored once it is no longer ``recording``."""
    with _lock(rid):
        meta = read_meta(rid)
        if meta is None:
            raise LookupError("No such recording.")
        if meta.get("status") == "recording":
            meta["last_seen"] = _now()
            _write_meta(rid, meta)


def release(rid: str) -> None:
    """The recorder let go of this recording without saving it - it failed,
    the disk filled, the cap was reached: it is no longer live, so the
    Projects page offers it at once rather than after
    :data:`LIVE_WINDOW_SECONDS`. Ignored once it is no longer ``recording``."""
    with _lock(rid):
        meta = read_meta(rid)
        if meta is None:
            raise LookupError("No such recording.")
        if meta.get("status") == "recording":
            meta["status"] = "stopped"
            _write_meta(rid, meta)


def chunks_present(rid: str) -> list[Path]:
    """The chunk files on disk, in sequence order."""
    d = _dir(rid)
    if not d.is_dir():
        return []
    found = []
    for p in d.iterdir():
        m = _CHUNK_RE.match(p.name)
        if m and p.is_file():
            found.append((int(m.group(1)), p))
    found.sort()
    return [p for _, p in found]


def missing_chunks(present: list[int], expected: int) -> list[int]:
    """The sequence numbers below ``expected`` that are not in ``present``."""
    have = set(present)
    return [i for i in range(expected) if i not in have]


def contiguous(present: list[int]) -> int:
    """How many chunks run unbroken from 0: the part that can be saved."""
    have, n = set(present), 0
    while n in have:
        n += 1
    return n


def _holes_message(gaps: list[int], expected: int) -> str:
    shown = ", ".join(str(g) for g in gaps[:10]) + (", …" if len(gaps) > 10 else "")
    return f"Chunks {shown} of {expected} never arrived; send them again before finishing."


def assemble(rid: str, expected: int | None) -> Path:
    """Write the list of chunk files ffmpeg reads in place (``concatf:``) and
    return its path: chunk ``0`` up to ``expected`` (all present ones when
    None), in order. No assembled copy of the recording is ever written. A
    hole below ``expected`` is refused with its numbers, never papered over;
    chunks at or beyond ``expected`` are left out."""
    present = chunks_present(rid)
    if not present:
        raise RecordingError("No chunks of this recording have arrived.")
    seqs = [int(_CHUNK_RE.match(p.name).group(1)) for p in present]
    if expected is not None:
        gaps = missing_chunks(seqs, expected)
        if gaps:
            raise RecordingError(_holes_message(gaps, expected))
        present = [p for s, p in zip(seqs, present) if s < expected]
    listing = _dir(rid) / CHUNK_LIST
    listing.write_text("".join(f"{p.name}\n" for p in present), encoding="utf-8")
    return listing


def even(n: int) -> int:
    """The largest even number not above ``n`` (4:2:0 needs even sides)."""
    return max(2, n - (n % 2))


def transcode_command(ffmpeg: str, chunk_list: Path, dst: Path, *, frame_rate: float,
                      crop: dict | None, progress_file: Path, pad_seconds: float) -> list[str]:
    """The ffmpeg argv that turns the recorder's chunks into the app's MP4.
    It reads ``chunk_list`` through the ``concatf:`` protocol, whose entries
    are the chunk files' names, so it runs with the recording's folder as its
    working directory (``finish_work``).

    The picture: constant frame rate at the recorder's rate (the WebM is
    variable - Chromium emits a frame only when the screen changes - and a
    player given that for a narration or an edit timeline would stutter),
    libx264 ``medium``, H.264 High, 4:2:0 (``core.video_creator.h264_params``),
    the region cropped first when there is one - at its exact origin
    (``exact=1``: without it ffmpeg rounds an odd x or y down to the chroma
    grid, a pixel off) - and the sides made even for 4:2:0. A screen that is
    still at the end sends no frames, so the picture would end before the
    sound (a re-voice muxes with ``-shortest`` and would cut the narration
    there): the last frame is held (``tpad`` clone, bounded by
    ``pad_seconds`` - the recording's length at most - so it can never run
    on) and ``-shortest`` ends both at the sound's end. The sound: the unity
    up-mix the whole app uses (a mono mix stays at its level; a stereo one
    passes through), 48 kHz, AAC at the re-voice's 192 kbit/s. ``+faststart``
    puts the index first. Progress goes to ``progress_file`` (ffmpeg's
    ``-progress``), read by the job's poll.
    """
    from core.video_creator import (
        AUDIO_SAMPLE_RATE, FASTSTART, REVOICE_AUDIO_BITRATE, REVOICE_H264_PROFILE, REVOICE_X264_PRESET,
        UPMIX_STEREO, h264_params,
    )

    fps = min(MAX_FRAME_RATE, max(MIN_FRAME_RATE, float(frame_rate or DEFAULT_FRAME_RATE)))
    if crop:
        w, h = even(int(crop["width"])), even(int(crop["height"]))
        picture = f"crop={w}:{h}:{int(crop['x'])}:{int(crop['y'])}:exact=1"
    else:
        picture = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    hold = f"fps={fps:g},tpad=stop_mode=clone:stop_duration={max(1.0, float(pad_seconds)):.0f}"
    return [
        ffmpeg, "-hide_banner", "-nostats", "-loglevel", "error", "-y",
        "-i", f"concatf:{chunk_list.name}",
        "-vf", f"{picture},{hold}",
        "-fps_mode", "cfr", "-r", f"{fps:g}", "-shortest",
        "-c:v", "libx264", "-preset", REVOICE_X264_PRESET, *h264_params(REVOICE_H264_PROFILE),
        "-af", f"{UPMIX_STEREO},aresample={AUDIO_SAMPLE_RATE}",
        "-c:a", "aac", "-b:a", REVOICE_AUDIO_BITRATE,
        *FASTSTART,
        "-progress", str(progress_file),
        str(dst),
    ]


def _progress_seconds(progress_file: Path) -> float | None:
    """The last ``out_time_us`` ffmpeg wrote, in seconds, or None."""
    try:
        text = progress_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    last = None
    for line in text.splitlines():
        if line.startswith("out_time_us="):
            try:
                last = int(line.split("=", 1)[1]) / 1_000_000
            except ValueError:
                pass
    return last


def transcode_timeout(seconds: float) -> float:
    return TRANSCODE_BASE_SECONDS + TRANSCODE_SECONDS_PER_SECOND * max(0.0, seconds)


def recorded_seconds(meta: dict, chunks: int, duration_ms: int | None) -> float:
    """How long the recording is, from what the server holds: the chunks times
    the recorder's timeslice - an upper bound, since the last chunk is short
    and a pause sends none - or the recorder's own clock when that is longer.
    A duration of 0 (the Projects page's re-save knows none) is never taken."""
    timeslice = float(meta.get("timeslice_ms") or DEFAULT_TIMESLICE_MS) / 1000.0
    return max(float(duration_ms or 0) / 1000.0, chunks * timeslice)


def region_problem(source: dict | None) -> str | None:
    """Why a region recording's source cannot be cropped as asked, or None.
    A region is recorded from a share of the screen it is on, so it must lie
    inside that monitor's rectangle (both in physical virtual-screen pixels):
    a crop past the picture's edge is silently moved by ffmpeg, and one wider
    than the picture fails on every retry. Other kinds crop nothing."""
    source = source or {}
    if source.get("kind") != "region":
        return None
    region, monitor = source.get("region"), source.get("monitor")
    if not region or not monitor:
        return "A region recording needs the region and the screen it is on."
    inside = (
        region["x"] >= monitor["x"] and region["y"] >= monitor["y"]
        and region["x"] + region["width"] <= monitor["x"] + monitor["width"]
        and region["y"] + region["height"] <= monitor["y"] + monitor["height"]
    )
    return None if inside else "The region is not inside one screen; choose a region on a single screen."


def region_in_surface(meta: dict) -> dict | None:
    """The crop in the captured picture's own pixels: the region's rectangle
    less the captured monitor's origin, when the source has both. None for a
    whole-screen or window recording (nothing to crop)."""
    source = meta.get("source") or {}
    region, monitor = source.get("region"), source.get("monitor")
    if not region or not monitor:
        return None
    return {
        "x": max(0, int(region["x"]) - int(monitor["x"])),
        "y": max(0, int(region["y"]) - int(monitor["y"])),
        "width": int(region["width"]),
        "height": int(region["height"]),
    }


def _set_stopped(rid: str) -> None:
    with _lock(rid):
        meta = read_meta(rid)
        if meta:
            meta["status"], meta["job_id"] = "stopped", None
            _write_meta(rid, meta)


def finish_work(rid: str, *, user: dict, duration_ms: int, chunks: int,
                progress: Callable[[float, str], None]) -> dict:
    """The save job's body: list the chunks, transcode, import, clean up.
    Returns ``{"project_id", "name", "seconds"}``, or ``{"cancelled": True}``
    when the job's cancel landed. A cancel or a failure keeps the chunks and
    sets the recording ``stopped``, so it is offered again."""
    from core.video_creator import _run_until_done

    with _lock(rid):
        meta = read_meta(rid)
        if meta is None:
            raise RecordingError("No such recording.")
        if not meta.get("job_id"):
            meta["status"], meta["job_id"] = "finishing", jobs.current_job_id()
            _write_meta(rid, meta)
    if not FFMPEG_PATH:
        _set_stopped(rid)
        raise RecordingError("ffmpeg is not available, so the recording cannot be converted.")
    cancelled = jobs.cancel_check_here()
    d = _dir(rid)
    progress(0.02, f"Reading the recording ({chunks} chunk{'s' if chunks != 1 else ''})…")
    try:
        listing = assemble(rid, chunks)
    except RecordingError:
        _set_stopped(rid)
        raise
    # The wait and the hold are measured by what the server holds (never a
    # duration of 0); the progress by the recorder's clock when it sent one.
    bound = recorded_seconds(meta, chunks, duration_ms)
    measure = float(duration_ms or 0) / 1000.0 or bound

    stem = safe_stem(meta.get("name") or "")
    mp4 = d / f"{stem}.mp4"
    progress_file = d / "progress.txt"
    cmd = transcode_command(FFMPEG_PATH, listing, mp4, frame_rate=meta.get("frame_rate") or DEFAULT_FRAME_RATE,
                            crop=region_in_surface(meta), progress_file=progress_file, pad_seconds=bound)
    log_file = d / "ffmpeg.log"

    def on_poll():
        done = _progress_seconds(progress_file)
        if done is not None and measure > 0:
            frac = min(0.99, done / measure)
            progress(0.1 + 0.8 * frac, f"Converting to MP4 ({frac * 100:.0f}%)…")

    progress(0.1, "Converting to MP4…")
    outcome, proc = _run_until_done(cmd, log_file, transcode_timeout(bound), cancelled, on_poll, cwd=d)
    if outcome == "cancelled":
        mp4.unlink(missing_ok=True)
        _set_stopped(rid)
        progress(1.0, "Cancelled; the recording is kept and can be saved again from the Projects page.")
        return {"cancelled": True}
    if outcome == "timeout" or proc.returncode != 0 or not mp4.is_file():
        tail = ""
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            tail = lines[-1] if lines else ""
        except OSError:
            pass
        mp4.unlink(missing_ok=True)
        _set_stopped(rid)
        raise RecordingError(
            "The recording could not be converted to MP4"
            + (" (it took too long)" if outcome == "timeout" else "") + (f": {tail}" if tail else ".")
        )

    # The MP4's own length: the last time ffmpeg reported writing.
    seconds = round(_progress_seconds(progress_file) or measure, 1)
    progress(0.95, "Creating the project…")
    from api.audit import PROJECT_IMPORT, audit
    from api.store import display_name_of

    record = store.import_from_path(mp4, meta["name"], owner_id=user["id"], owner_name=display_name_of(user))
    audit(PROJECT_IMPORT, user=user, entity="project", entity_id=record["id"],
          detail=f"recording: {record['source_filename']} ({seconds:.0f} s)")
    shutil.rmtree(d, ignore_errors=True)
    progress(1.0, f"Saved as {record['name']}")
    return {"project_id": record["id"], "name": record["name"], "seconds": seconds}


def finish(rid: str, *, user: dict, duration_ms: int, chunks: int | None,
           from_recorder: bool = False, accept_loss: bool = False) -> dict:
    """Start the save job for ``rid``: answers ``{"job_id", "chunks"}`` - the
    job and how many chunks it saves (what the audit row names, also when the
    caller sent no count). Everything that can be
    refused is refused BEFORE the job starts:

    - an unknown recording (``LookupError``);
    - one being saved (:class:`Busy`);
    - one still LIVE, unless its recorder asks (:class:`Live`);
    - a gap below ``chunks`` (``RecordingError``, naming it, so the recorder
      can resend);
    - chunks at or beyond ``chunks`` that the save would drop, unless
      ``accept_loss`` (:class:`WouldLose`, saying how many);
    - another recording being saved (``jobs.KindBusy``).
    ``chunks`` None means every chunk present, which must then run unbroken."""
    with _lock(rid):
        meta = read_meta(rid)
        if meta is None:
            raise LookupError("No such recording.")
        if is_saving(meta):
            raise Busy("This recording is being saved already.")
        if is_live(meta) and not from_recorder:
            raise Live("This recording is still being made; stop it in the recorder first.")
        seqs = _seqs(rid)
        if not seqs:
            raise RecordingError("No chunks of this recording have arrived.")
        expected = chunks if chunks is not None else max(seqs) + 1
        gaps = missing_chunks(seqs, expected)
        if gaps:
            raise RecordingError(_holes_message(gaps, expected))
        beyond = [s for s in seqs if s >= expected]
        if beyond and not accept_loss:
            raise WouldLose(
                f"Saving the first {expected} chunk{'s' if expected != 1 else ''} would give up "
                f"{len(beyond)} more after the gap; confirm to save without them."
            )
        job_id = jobs.start_single(
            JOB_KIND,
            lambda progress: finish_work(rid, user=user, duration_ms=duration_ms, chunks=expected, progress=progress),
            user_id=user["id"],
        )
        meta["status"], meta["job_id"] = "finishing", job_id
        _write_meta(rid, meta)
        return {"job_id": job_id, "chunks": expected}


def discard(rid: str, *, from_recorder: bool = False) -> bool:
    """Remove a recording and its chunks. False when there was none. Refused
    while it is being saved (:class:`Busy`) or is LIVE, unless its recorder
    asks (:class:`Live`)."""
    if not _valid(rid) or not _dir(rid).is_dir():
        return False
    with _lock(rid):
        meta = read_meta(rid)
        if meta is not None:
            if is_saving(meta):
                raise Busy("This recording is being saved; wait for the save to finish.")
            if is_live(meta) and not from_recorder:
                raise Live("This recording is still being made; stop it in the recorder first.")
        shutil.rmtree(_dir(rid), ignore_errors=True)
    return True


def list_unfinished() -> list[dict]:
    """Every recording still on disk - live, being saved, stopped but never
    saved, or whose save failed - newest first: its effective status
    (``recording`` / ``finishing`` / ``stopped``), the chunks present, how many
    run unbroken from 0 (``contiguous``: the part that can be saved), the
    highest chunk number plus one (``highest``), their size, and the seconds
    the savable part holds. Everyone's: the API filters by owner."""
    if not RECORDINGS_DIR.is_dir():
        return []
    out = []
    now = _now()
    for d in RECORDINGS_DIR.iterdir():
        if not _valid(d.name):
            continue
        meta = read_meta(d.name)
        if not meta:
            continue
        present = chunks_present(d.name)
        seqs = [int(_CHUNK_RE.match(p.name).group(1)) for p in present]
        run = contiguous(seqs)
        out.append({
            **{k: meta.get(k) for k in ("id", "name", "created_at", "owner_id", "owner_name", "width", "height")},
            "status": effective_status(meta, now),
            "chunks": len(present),
            "contiguous": run,
            "highest": (max(seqs) + 1) if seqs else 0,
            "bytes": sum(p.stat().st_size for p in present),
            "seconds": recorded_seconds(meta, run, 0),
        })
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out
