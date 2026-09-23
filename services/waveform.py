"""Peaks for the narration timeline's waveform strip.

Reads the project's own ``audio.wav`` - the 16 kHz mono file the transcription
step already extracts - with the stdlib :mod:`wave` module and numpy, and
reduces it to one magnitude per time bucket for the editor to draw.

**No ffmpeg and no ffprobe.** Both are deliberate. The duration the strip is
drawn against must be the duration of the file actually being drawn, and that
file is our own PCM WAV, so it comes from the WAV header (``nframes /
framerate``): exact for this file, read in microseconds with no process
spawned, where any probe would be slower and would measure something else -
and it does not care what the host has installed. Not ``record["duration"]``
either, which is faster-whisper's own measurement and can differ by a frame
or two.

Resolution is fixed at 125 ms per bucket rather than taken from the caller, so
there is exactly one cache entry per project no matter how wide the strip is
drawn; the client max-pools down to its own pixel width, which is exact and
cheap. A ``?buckets=`` parameter was considered and rejected: more cache
entries, no visual gain.

The file is read in chunks. A two-hour recording is 115 MB of PCM and must
never be loaded whole to produce ~58 kB of JSON.

The bucket arithmetic itself - how many buckets, where their edges fall, the
max-pool and the scaling to a byte - lives once, in :func:`peaks_from_samples`
and the helpers under it, because the music library (``services.music``) draws
the same strip inside a clip from samples it has just decoded, and a second
copy of the rule would be free to drift from this one (spec §12.3).
"""

import json
import wave
from pathlib import Path

import numpy as np

from services import projects as store
from utils.logger import get_logger

logger = get_logger("WAVEFORM")

# 8 buckets a second = 125 ms each. Floors and ceilings keep a very short clip
# from being drawn as a handful of bars and a very long one from producing a
# payload nobody can use: 341 s -> 2728 buckets (~11 kB of JSON), and anything
# past ~25 minutes caps at 12000 (~58 kB).
BUCKETS_PER_SECOND = 8
BUCKET_SECONDS = 1 / BUCKETS_PER_SECOND
MIN_BUCKETS = 800
MAX_BUCKETS = 12000

# Frames per read. 1M frames of 16-bit mono is 2 MB, and at 16 kHz covers a
# little over a minute - so even a two-hour file is ~115 reads.
CHUNK_FRAMES = 1_000_000

# Written beside the audio it describes. data/ is gitignored and
# ``delete_project`` removes every child before the record, so this needs no
# cleanup path of its own.
CACHE_NAME = "audio.peaks.json"

# The transcription step writes this beside the source video.
AUDIO_NAME = "audio.wav"

# Full-scale magnitude per sample width, used to scale a peak into 0-255.
_FULL_SCALE = {1: 128.0, 2: 32768.0, 4: 2147483648.0}


class NoAudio(Exception):
    """The project has no extracted audio yet (it has not been transcribed)."""


class UnreadableAudio(Exception):
    """``audio.wav`` exists but is not PCM this module can read."""


# What is said when the extracted audio is not there yet - by the waveform (a
# 404) and by the edit (a 409: without the audio's length there is nothing to
# check a cut against). One string, so the two never drift apart.
NO_AUDIO_MESSAGE = "Transcribe the video first - its audio is extracted then."


def audio_path(pid: str) -> Path:
    return store.PROJECTS_DIR / pid / AUDIO_NAME


def _cache_path(pid: str) -> Path:
    return store.PROJECTS_DIR / pid / CACHE_NAME


def bucket_count(duration: float, bucket_seconds: float = BUCKET_SECONDS) -> int:
    """How many buckets a clip of ``duration`` seconds is drawn with."""
    want = round(duration / bucket_seconds)
    return max(MIN_BUCKETS, min(MAX_BUCKETS, int(want)))


def _bucket_edges(frames: int, buckets: int) -> np.ndarray:
    """Frame index where each bucket starts, plus the end: ``buckets + 1``
    edges over ``frames`` frames. Strictly increasing as long as there are at
    least as many frames as buckets, which every caller guarantees."""
    return np.linspace(0, frames, buckets + 1).astype(np.int64)


def _scaled(peaks: np.ndarray, full_scale: float) -> list[int]:
    """Raw per-bucket maxima as bytes, 0-255 of full scale."""
    return np.clip(peaks * 255.0 / full_scale, 0, 255).astype(np.uint8).tolist()


def peaks_payload(peaks: list[int], duration: float, sample_rate: int) -> dict:
    """The response shape: the peaks with the scale they were drawn at, so a
    client can line them up with anything else drawn in seconds. One builder
    for the strip's cache and the music library's."""
    buckets = len(peaks)
    return {
        "buckets": buckets,
        "bucket_seconds": duration / buckets if buckets else 0.0,
        "duration": duration,
        "sample_rate": int(sample_rate),
        "peaks": peaks,
    }


def peaks_from_samples(samples, sample_rate: int, bucket_seconds: float = BUCKET_SECONDS) -> list[int]:
    """The peaks of an in-memory clip: ``samples`` is one 16-bit value per
    frame (mono, already mixed down), and the answer is exactly what
    :func:`compute_peaks` gives the same frames from a file - the same bucket
    count, the same edges, the loudest sample in each bucket scaled to a byte.
    For a clip already decoded into memory; the strip's own file is read in
    chunks by ``compute_peaks`` because it can be two hours long."""
    magnitudes = np.abs(np.asarray(samples).astype(np.int32))
    frames = int(magnitudes.size)
    if frames == 0 or not sample_rate:
        return []
    buckets = min(bucket_count(frames / sample_rate, bucket_seconds), frames)
    edges = _bucket_edges(frames, buckets)
    return _scaled(np.maximum.reduceat(magnitudes, edges[:-1]), _FULL_SCALE[2])


def _samples(raw: bytes, width: int, channels: int) -> np.ndarray:
    """One absolute magnitude per FRAME, mixed down from however many channels.

    8-bit WAV is unsigned by the format's own definition, which is why it is
    shifted rather than simply viewed.
    """
    if width == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2")
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4")
    else:  # pragma: no cover - guarded by the caller
        raise UnreadableAudio(f"Unsupported sample width: {width} bytes.")

    # abs() first, then the per-frame max across channels: taking the max of
    # the signed values would prefer a positive channel over a louder negative
    # one. int32 throughout because abs(-32768) does not fit in an int16.
    magnitudes = np.abs(data.astype(np.int32))
    if channels > 1:
        usable = (magnitudes.size // channels) * channels
        magnitudes = magnitudes[:usable].reshape(-1, channels).max(axis=1)
    return magnitudes


def _has_frame(wf, index: int, frame_bytes: int) -> bool:
    """Whether frame ``index`` is really in the file (not merely claimed)."""
    wf.setpos(index)
    return len(wf.readframes(1)) == frame_bytes


def real_frame_count(wf, claimed: int, frame_bytes: int) -> int:
    """How many frames the file ACTUALLY holds, which is not always what its
    header says.

    A WAV header carries the data chunk's size; a file truncated after it was
    written - a copy interrupted, a disk that filled, a transfer that dropped -
    keeps the original claim. ``readframes`` then returns short, and the tail of
    the strip was drawn as a run of zeroes: a confident flat band saying "the
    speaker was silent here", over a stretch of recording we simply do not have.
    That is the one thing a waveform must never do, because the whole feature is
    aiming a sentence at what the strip shows.

    So the end is FOUND rather than trusted: the last claimed frame is checked,
    and if it is not there the real end is bisected out. Each probe is a seek
    and a single frame, about 25 of them for a two-hour file, so this costs
    nothing next to the read that follows.
    """
    if claimed <= 0:
        return 0
    if _has_frame(wf, claimed - 1, frame_bytes):
        return claimed
    if not _has_frame(wf, 0, frame_bytes):
        return 0
    # lo is known present, hi known absent.
    lo, hi = 0, claimed - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _has_frame(wf, mid, frame_bytes):
            lo = mid
        else:
            hi = mid
    return lo + 1


def compute_peaks(path: Path) -> dict:
    """Read ``path`` and return the peaks payload for it.

    Raises :class:`UnreadableAudio` for anything the :mod:`wave` module cannot
    open or whose sample width is not 8, 16 or 32-bit PCM.
    """
    try:
        wf = wave.open(str(path), "rb")
    except Exception as exc:  # wave.Error, EOFError, OSError - all mean the same here
        raise UnreadableAudio(f"Could not read {path.name}: {exc}") from exc

    with wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        frames = wf.getnframes()

        if width not in _FULL_SCALE:
            raise UnreadableAudio(f"Unsupported sample width: {width} bytes.")
        if not rate or frames <= 0:
            raise UnreadableAudio("The audio has no frames.")

        # What the file really holds, not what its header claims. A truncated
        # recording is drawn (and reported) at the length that is actually
        # there, rather than padded out with a flat band of invented silence.
        held = real_frame_count(wf, frames, width * channels)
        if held <= 0:
            raise UnreadableAudio("The audio has no frames.")
        if held < frames:
            logger.warning(
                "%s claims %d frames but holds %d - drawing the %.1fs that are really there",
                path.name, frames, held, held / rate,
            )
            frames = held
        wf.rewind()

        duration = frames / rate
        # Never ask for more buckets than there are frames: an empty bucket has
        # no maximum, and reduceat on a repeated index does not mean what it
        # looks like it means. A clip this short is a test fixture, not a
        # recording, but it must not crash.
        buckets = min(bucket_count(duration), frames)
        edges = _bucket_edges(frames, buckets)

        peaks = np.zeros(buckets, dtype=np.int64)
        first = 0
        while first < buckets:
            # Take whole buckets at a time so no bucket straddles two reads.
            last = first
            while last < buckets and edges[last + 1] - edges[first] <= CHUNK_FRAMES:
                last += 1
            if last == first:  # one bucket bigger than a chunk: read it whole
                last = first + 1

            start, stop = int(edges[first]), int(edges[last])
            wf.setpos(start)
            magnitudes = _samples(wf.readframes(stop - start), width, channels)
            if magnitudes.size == 0:
                break
            # reduceat wants offsets relative to this chunk. Clipped because a
            # short final read must not index past what was actually returned.
            offsets = np.clip(edges[first:last] - start, 0, magnitudes.size - 1)
            peaks[first:last] = np.maximum.reduceat(magnitudes, offsets)
            first = last

    return peaks_payload(_scaled(peaks, _FULL_SCALE[width]), duration, rate)


def duration_for(pid: str) -> float | None:
    """How long the project's extracted audio is, from the WAV header alone.

    ``nframes / framerate`` - no decode and no probe: the header of the file
    being drawn is exact for it and costs no process, where a probe would be
    slower and would measure something else. Not ``record["duration"]``
    either: the timeline strip and everything drawn over it must share one
    scale, and it has to be the scale of the file being drawn.

    ``None`` rather than an exception when there is no audio or it cannot be
    read, because a caller that only wants the length (the audition plan) has a
    usable fallback and should not fail for want of a waveform.
    """
    path = audio_path(pid)
    if not path.is_file():
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            frames = wf.getnframes()
            if not rate or frames <= 0:
                return None
            # The frames really there, for the same reason ``compute_peaks``
            # checks: the strip and everything drawn over it must share ONE
            # scale, so a truncated file must not be a longer timeline here
            # than it is a waveform there.
            frames = real_frame_count(wf, frames, wf.getsampwidth() * wf.getnchannels())
    except Exception:
        return None
    if frames <= 0:
        return None
    return frames / rate


def peaks_for(pid: str) -> dict:
    """The peaks payload for ``pid``, computed once and cached beside the audio.

    The cache is keyed on the source file's ``mtime_ns`` and size, so a
    re-transcription that rewrites ``audio.wav`` invalidates it without anyone
    having to remember to.
    """
    path = audio_path(pid)
    if not path.is_file():
        raise NoAudio(NO_AUDIO_MESSAGE)

    stat = path.stat()
    cache = _cache_path(pid)
    if cache.is_file():
        try:
            held = json.loads(cache.read_text(encoding="utf-8"))
            if held.get("mtime_ns") == stat.st_mtime_ns and held.get("size") == stat.st_size:
                return {k: held[k] for k in
                        ("buckets", "bucket_seconds", "duration", "sample_rate", "peaks")}
        except (OSError, ValueError, KeyError):
            # A truncated or hand-edited cache is not worth a 500: recompute.
            pass

    payload = compute_peaks(path)
    held = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, **payload}
    try:
        tmp = cache.with_suffix(".json.part")
        tmp.write_text(json.dumps(held), encoding="utf-8")
        store.replace_with_retry(tmp, cache)
    except OSError:
        # The answer is already computed and correct; failing to memoise it is
        # not a reason to fail the request.
        pass
    return payload
