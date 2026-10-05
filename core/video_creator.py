"""Video creation module for combining slides and audio into MP4.

Assembles individual slide images (or video clips for animated slides)
with their corresponding audio narration into a single MP4 video.
Supports configurable resolution, transition pauses between slides,
and optional transition sound effects.

The deck's picture is composed by ffmpeg from stills
(``VideoCreator.create_video``; see "the deck render" below): Python draws
the title cards and the watermark once each and builds the narration track,
and never touches a frame. A render is several ffmpeg runs - one per chunk
of the picture, one for the sound and one that joins them.
"""

import math
import os
import re
import shutil
import tempfile
import time
import subprocess
from pathlib import Path
from typing import List, Optional, Callable, Tuple
from dataclasses import dataclass

import numpy as np
from PIL import Image

from core.fonts import resolve_font, text_image
from core.tts_provider import OnsetProfile
from utils.logger import get_logger
logger = get_logger("VIDEO")

# A static deck is encoded at 2 fps (every frame is the same slide, so the
# encode stays fast). A visual transition needs real frames to play on: at 2 fps
# a 0.5 s fade is a single frame, so a deck with a transition renders at 24 fps,
# and so does a deck with an animated slide (``deck_fps``).
STATIC_FPS = 2
TRANSITION_FPS = 24

# ── the H.264 every render writes (Q1) ────────────────────────────────────────
#
# Two parameters go with every libx264 encode the app runs - the deck render,
# its preview and the re-voice's picture cut:
#
# - ``-profile:v <profile>``: a CEILING on the tools x264 may use, never a
#   label. The stream is flagged by what the x264 preset actually uses:
#   ``medium`` (8×8 transform, CABAC, B-frames) writes High; ``ultrafast``
#   uses none of them and writes Constrained Baseline whatever profile is
#   asked for (measured on the bundled 7.1 and on 8.0.1).
# - ``-pix_fmt yuv420p``: 4:2:0, the only chroma layout a High-profile decoder
#   (a browser, a phone, a TV) is required to take. The deck render's graph
#   ends in yuv420p as well, so the argument says what the file is; the
#   real-binary tests read the pixel format back from a real render
#   (``tests/test_encode_settings.py``, ``tests/test_deck_render.py``).
#
# Plus ``-movflags +faststart`` on every MP4 the app hands a user: the index
# (``moov``) goes in front of the media (``mdat``), so a web upload or the
# page's player can start playing before the whole file has arrived.
PIX_FMT_420 = ["-pix_fmt", "yuv420p"]
FASTSTART = ["-movflags", "+faststart"]


def h264_params(profile: str) -> List[str]:
    """``-profile:v <profile> -pix_fmt yuv420p`` for a libx264 encode (see above)."""
    return ["-profile:v", profile, *PIX_FMT_420]


# The re-voice has no output preset of its own. Its picture cut - the picture
# the re-voiced file carries, whenever the edit cuts it - is encoded like a final
# deck render (x264 ``medium``, High, 4:2:0, and the source's frame rate: see
# ``cut_picture``); its mux and its music pass give AAC ONE bitrate, named here
# so the two cannot drift apart - 192k, what every output preset gives the deck
# render too (``services.output_presets``).
#
# ... at 48 kHz stereo (:data:`RESAMPLE_48K`, :data:`UPMIX_STEREO`). The
# narration arrives at the TTS engine's own rate, 24 kHz mono for Edge and
# Kokoro alike, and at 24 kHz AAC-LC cannot carry 192k at all (6144 bits per
# channel per frame is 144 kbit/s a channel, and ffmpeg's ``aac`` wrote about
# 100 kbit/s of mono narration there whatever it was given). Resampled to
# 48 kHz and up-mixed at unity, the same narration is given 192 kbit/s and
# reads at or a little under it, lower over silence (measured: see
# ``docs/porting/generation-options.md``, *As built — Q1*).
REVOICE_X264_PRESET = "medium"
REVOICE_H264_PROFILE = "high"
REVOICE_AUDIO_BITRATE = "192k"
AUDIO_SAMPLE_RATE = 48000
RESAMPLE_48K = f"aresample={AUDIO_SAMPLE_RATE}"


def fps_for_transition(slide_transition: str, transition_duration: float = 0.5) -> int:
    """The frame rate a render needs: ``TRANSITION_FPS`` when a transition will
    actually render (the same gate as ``create_video``), else ``STATIC_FPS``."""
    if slide_transition and slide_transition != "none" and transition_duration > 0:
        return TRANSITION_FPS
    return STATIC_FPS


def deck_fps(slide_transition: str, transition_duration: float = 0.5, animated: bool = False) -> int:
    """The frame rate a deck renders at: ``fps_for_transition``'s, raised to
    ``TRANSITION_FPS`` when any slide is ``animated`` (has a video clip).

    The whole picture is sampled at one rate, so at ``STATIC_FPS`` an
    animated slide played as two pictures a second - which is what every
    static deck with an animated slide did before T1 (moviepy's writer
    sampled the composite at the creator's fps)."""
    if animated:
        return TRANSITION_FPS
    return fps_for_transition(slide_transition, transition_duration)


def effective_pause(override, default: float) -> float:
    """The pause after one slide: ``override`` when it is a non-negative
    number (the slide's own ``pause_override``), else ``default`` (the job's
    transition pause). One rule for the master track, the transition clips,
    the chapters and the per-slide subtitles, so they always agree."""
    if isinstance(override, bool) or not isinstance(override, (int, float)) or override < 0:
        return float(default)
    return float(override)


def pause_after(clip_info, default: float) -> float:
    """The pause after ``clip_info`` (a ``SlideClipInfo``): its ``pause_after``
    when set, else ``default``."""
    return effective_pause(getattr(clip_info, "pause_after", None), default)


def chapter_spans(
    durations: List[float], transition_pause: float, intro_offset: float = 0.0,
    pauses: Optional[List[float]] = None,
) -> List[Tuple[int, int]]:
    """``(start_ms, end_ms)`` per slide: the slides run back to back with the
    transition pause between them, all shifted by the intro card's duration
    (``intro_offset``) when there is one - the card has no chapter of its own.
    ``pauses`` gives the pause after each slide where it differs from
    ``transition_pause`` (a slide's own override); None means the same
    pause after every slide.
    """
    spans = []
    current = float(intro_offset)
    for i, duration in enumerate(durations):
        spans.append((int(round(current * 1000)), int(round((current + duration) * 1000))))
        current += duration
        if i < len(durations) - 1:
            current += pauses[i] if pauses is not None and i < len(pauses) else transition_pause
    return spans


def _ffmeta_escape(text: str) -> str:
    """A metadata value as ffmpeg's ffmetadata file wants it: the format's
    own rule is that ``=``, ``;``, ``#``, ``\\`` and a newline in a key or a
    value are escaped with a backslash, and a chapter's title is user-typed
    (a marker's name), so every one of the five is. The backslash goes
    first, or the escapes just added would be escaped again. Measured on the
    machine's 8.0.1 and the bundled 7.1 alike (2026-09-24): unescaped, the
    parser happens to forgive ``=``, ``;`` and ``#`` inside a value, eats a
    backslash and cuts the title at a newline; escaped, all five come back
    from ``ffprobe -show_chapters`` exactly as typed."""
    out = text.replace("\\", "\\\\")
    for char in ("=", ";", "#"):
        out = out.replace(char, "\\" + char)
    return out.replace("\n", "\\\n")


def embed_chapters(video_path: Path, chapters: List[Tuple[int, int, str]]) -> bool:
    """Write ``chapters`` - ``(start_ms, end_ms, title)`` each - into the MP4
    at ``video_path``, in place: an ffmetadata file beside it, one stream-copy
    remux into ``<stem>_chaptered.mp4`` and a rename over the original. The
    deck's ``_embed_chapters`` computes its spans and calls this; the re-voice
    job calls it with the markers' chapters (``services.edit.chapters_for``)
    on the FINAL file it records, after the mux and the music pass, so the
    chapters are on the file the user downloads.

    ``True`` when the file now carries the chapters; ``False`` - logged, the
    file left exactly as it was, the metadata file and any partial output
    removed - when there is nothing to write, no ffmpeg, or the remux failed.
    Never raises: a chapter is worth a warning, not a failed job. The
    RESOLVED ``FFMPEG_PATH``, never the bare name (trap 3), and the same
    ``-map_metadata 1 -codec copy`` remux the deck path has always run,
    plus ``-map_chapters 1``: without it ffmpeg takes the chapters from the
    FIRST input that has any, and a re-voiced file already has the source
    video's (``replace_video_audio`` copies them along with the picture),
    so the markers' list was silently ignored on any source that carried
    chapters of its own (a Camtasia export, say) - measured on 8.0.1 and
    the bundled 7.1, 2026-09-24. The deck's input never has chapters, so
    for the deck path the explicit map is exactly the default it always got.

    ``+faststart`` (:data:`FASTSTART`) as well: this remux is the LAST write
    of every deck render with slide titles and of every re-voice with
    markers, and a plain remux puts the index back at the end of the file,
    undoing the render's own faststart (Q1).
    """
    from utils.config import FFMPEG_PATH
    if not chapters:
        return False
    if not FFMPEG_PATH:
        logger.warning("FFmpeg not found, skipping chapter embedding")
        return False

    meta_path = video_path.with_suffix(".chapters.txt")
    temp_output = video_path.with_stem(video_path.stem + "_chaptered")
    try:
        # Write FFmpeg metadata file
        with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(";FFMETADATA1\n")
            for start_ms, end_ms, title in chapters:
                f.write(
                    f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}\nEND={end_ms}\n"
                    f"title={_ffmeta_escape(title)}\n"
                )

        # Remux video with chapter metadata - the chapters mapped from the
        # metadata file EXPLICITLY, or a source that has its own would keep them.
        cmd = [
            FFMPEG_PATH, "-i", str(video_path), "-i", str(meta_path),
            "-map_metadata", "1", "-map_chapters", "1", "-codec", "copy", *FASTSTART,
            "-y", str(temp_output),
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=60,
            creationflags=0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        if result.returncode == 0 and temp_output.exists():
            temp_output.replace(video_path)
            logger.info("Embedded %d chapters", len(chapters))
            return True
        logger.warning("ffmpeg failed: %s", result.stderr.decode(errors="replace")[:200])
        if temp_output.exists():
            temp_output.unlink()
        return False
    except Exception as e:
        logger.error("Error embedding chapters: %s", e)
        try:
            if temp_output.exists():
                temp_output.unlink()
        except OSError:
            pass
        return False
    finally:
        # Clean up metadata file
        if meta_path.exists():
            meta_path.unlink()


def _configured_title_font() -> Optional[str]:
    """The ``title_font`` config value (a font file path), if any."""
    from utils.config import config
    return config._config.get("title_font") or None


def _active_onset_profile() -> OnsetProfile:
    """The onset profile of the STUDIO'S configured TTS provider.

    A fallback only: a run that knows its own provider passes that provider's
    profile explicitly (``VideoCreator(onset_profile=...)``), because a job may
    narrate with a provider other than the studio default, and the default can
    change while the job runs.
    """
    from core.tts_provider import get_onset_profile
    from utils.config import config
    return get_onset_profile(config.tts_provider)


class CancelledError(Exception):
    """Raised when video encoding is cancelled by the user."""


def trim_leading_silence_segment(audio, profile=None, chunk_ms: int = 5):
    """Trim leading silence from an in-memory ``AudioSegment`` via an onset profile.

    Shared core used by both the file-based ``_trim_leading_silence`` and the
    in-memory trim paths in the GUI processing pipeline, so threshold/cap/fade
    behaviour stays consistent across providers.

    Args:
        audio: A ``pydub.AudioSegment``.
        profile: Optional ``OnsetProfile``. Defaults to ``EDGE_ONSET``.
        chunk_ms: Slice size (ms) for silence detection.

    Returns:
        The trimmed ``AudioSegment`` (or the original if no trim was needed).
    """
    from pydub.silence import detect_leading_silence
    from core.tts_provider import EDGE_ONSET

    if profile is None:
        profile = EDGE_ONSET

    threshold_db = profile.resolve_threshold_db(audio)
    leading_ms = detect_leading_silence(audio, silence_threshold=threshold_db, chunk_size=chunk_ms)

    if leading_ms < 10:
        return audio

    trim_ms = min(leading_ms, profile.trim_cap_ms)
    trimmed = audio[trim_ms:]

    fade_ms = profile.micro_fade_ms
    if fade_ms > 0 and len(trimmed) > fade_ms + 2:
        trimmed = trimmed.fade_in(fade_ms)

    return trimmed


def _trim_leading_silence(
    path: str, silence_thresh_db: float = -13, chunk_ms: int = 5, profile=None,
) -> str:
    """Trim leading silence and TTS ramp-up from an audio file.

    The threshold, trim cap and micro-fade come from the provider's
    ``OnsetProfile``. For Edge (the default ``EDGE_ONSET``) this is a fixed
    -13 dB threshold that cuts past both the silence AND the baked-in volume
    ramp-up. Kokoro (``KOKORO_ONSET``) is ~2 dB quieter, so it uses a relative
    threshold derived from the clip's own body level — a fixed -13 dB would
    classify the whole quiet clip as silence and over-trim it.

    Args:
        path: Audio file to trim in place.
        silence_thresh_db: Legacy fixed threshold, used only when ``profile`` is
            None (preserves the original signature/behaviour).
        chunk_ms: Slice size (ms) for silence detection.
        profile: Optional ``OnsetProfile``. Defaults to ``EDGE_ONSET``.

    Returns:
        The original path (trimmed in place), or unchanged on error/no-op.
    """
    try:
        from pydub import AudioSegment

        audio = AudioSegment.from_file(path)
        trimmed = trim_leading_silence_segment(audio, profile=profile, chunk_ms=chunk_ms)

        if len(trimmed) == len(audio):
            return path

        trimmed.export(path, format="mp3")
        logger.debug("Trimmed %dms leading silence from %s", len(audio) - len(trimmed), Path(path).name)
        return path
    except Exception as e:
        logger.warning("Could not trim leading silence: %s", e)
        return path


def _level_opening(audio, profile=None):
    """Boost the opening of each audio clip to match the body volume.

    Edge TTS produces a soft ramp-up in the first 30-60ms of speech. This
    boosts the opening in small slices to match the body level, preserving
    natural pitch and volume variation in the rest of the clip. Providers whose
    ``OnsetProfile.boost_opening`` is False (e.g. Kokoro, which starts at full
    volume) return the audio unchanged.

    Args:
        audio: A ``pydub.AudioSegment``.
        profile: Optional ``OnsetProfile``. Defaults to ``EDGE_ONSET``.
    """
    from pydub import AudioSegment
    from core.tts_provider import EDGE_ONSET

    if profile is None:
        profile = EDGE_ONSET

    if not profile.boost_opening:
        return audio

    if len(audio) < 120:
        return audio

    # Measure the body volume (skip first 80ms ramp region)
    body = audio[80:min(len(audio), 3000)]
    if body.dBFS <= -50:
        return audio

    target_db = body.dBFS
    level_ms = profile.boost_window_ms  # Only process the first window
    slice_ms = profile.boost_slice_ms
    max_boost = profile.max_boost_db

    result = AudioSegment.empty()
    for ms in range(0, min(level_ms, len(audio)), slice_ms):
        chunk = audio[ms:ms + slice_ms]
        if len(chunk) == 0:
            break
        if chunk.dBFS > -50 and chunk.dBFS < target_db - 1:
            boost = min(target_db - chunk.dBFS, max_boost)
            chunk = chunk + boost
        result += chunk

    # Append the rest unchanged — natural dynamics preserved
    if level_ms < len(audio):
        result += audio[level_ms:]

    return result


# What ``_build_replace_audio_cmd`` raises when there is no ffmpeg to name.
FFMPEG_MISSING = "FFmpeg is not available - install ffmpeg (or the imageio-ffmpeg package)."

# ffmpeg's OWN duration line, and nothing else that looks like one.
#
# It is printed at exactly two spaces of indent, directly under the input, and
# the field that follows it is separated by a comma:
#     "  Duration: 00:00:10.00, start: 0.000000, bitrate: 87 kb/s"  (mp4)
#     "  Duration: 00:00:06.01, bitrate: 256 kb/s"                   (wav - no start:)
# The input's METADATA block is printed FIRST and its values are indented four
# spaces or more, so an unanchored search found them before ffmpeg's own line:
#     "    comment         : Duration: 596523:14:07.99"
# - ffmpeg's classic bogus-duration rendering, easily present in a remuxed or
# tool-tagged file. That read 2147483647.99 out of a 10 s video and put
# ``apad=whole_dur=2147483647.990`` into the mux, which is the shipped stall
# reached through the fixed code. Anchored on the indent and the separator, and
# both binaries print both shapes identically (verified 7.1 and 8.0.1, on mp4,
# wav and mp3).
_DURATION_LINE = re.compile(
    r"^ {2}Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)(?=,|\s*$)", re.MULTILINE
)

# No real media file is a week long. A number past this is a mis-parse, a bogus
# tag or a caller's bug, never a duration - so it is "unknown", which is the
# branch that emits no pad at all. The belt to :data:`_DURATION_LINE`'s braces:
# it closes the stall class for ANY future path that hands the mux a bad number,
# not just for the one that was found. (A two-hour recording, the longest thing
# this app has been pointed at, is 1/84th of it.)
MAX_MEDIA_SECONDS = 7 * 24 * 3600


def usable_duration(value) -> Optional[float]:
    """``value`` as a number of seconds that may bound a filter, else None.

    THE rule, in one place, for every "do we know how long this is?" question
    the mux asks: a real, finite, positive float no longer than
    :data:`MAX_MEDIA_SECONDS`. None, a string, a NaN, an infinity, a zero or
    negative length and an absurd one are all "we do not know" - and the point
    of funnelling them here is that the unknown answer is the SAFE one (no pad
    at all), so no caller can turn a junk value into an argv that runs for
    ever. See :func:`_pad_filter`.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0 or seconds > MAX_MEDIA_SECONDS:
        return None
    return seconds


def _ffmpeg_header(path) -> str:
    """What ffmpeg says about ``path``: the header it writes to stderr, or "".

    ``ffmpeg -i <file>`` with no output file prints everything it knows about
    the input - duration, streams, chapters - and exits at once with "At least
    one output file must be specified" (measured against the SHIPPED
    ``app/bin/ffmpeg.exe``: 0.04 s, exit 1, and 0.04 s for a file that is not
    there or is not media at all). That is how the engine reads a file's
    metadata: **its own code never needs a prober** (F1), which is what makes
    it safe on any machine - one with no ffprobe, or with a different one on
    PATH. (The installer ships an ffprobe from 0.9.1, but for pydub's decode;
    0.9.0 shipped none, and every ffprobe call the engine made answered
    nothing on a customer machine.)

    The RESOLVED ``FFMPEG_PATH``, never a bare name (trap 3). "" when there is
    no ffmpeg or it could not be run, so every caller's "cannot tell" branch is
    the one that runs. Decoded with ``errors="replace"``: a file name that is
    not cp1252 must not raise inside a probe.
    """
    from utils.config import FFMPEG_PATH

    if not FFMPEG_PATH:
        return ""
    try:
        out = subprocess.run(
            [FFMPEG_PATH, "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except Exception:
        return ""
    return out.stderr or ""


def _probe_duration(path: Path) -> Optional[float]:
    """A media file's duration in seconds, or None when it cannot be told.

    Asks ffmpeg, never a prober (:func:`_ffmpeg_header`), so it answers on any
    machine. The old bare ``["ffprobe", ...]`` here answered None wherever
    none was on PATH - every 0.9.0 install - and the mux was then handed an
    unbounded pad it could not finish (F1).

    None when there is no ffmpeg, when ffmpeg cannot open the file, when it
    reports ``Duration: N/A``, or when the answer is not a usable length
    (:func:`usable_duration`) - the unchanged contract, so every caller
    behaves exactly as before.
    """
    found = _DURATION_LINE.search(_ffmpeg_header(path))
    if not found:
        return None
    hours, minutes, seconds = found.groups()
    return usable_duration(int(hours) * 3600 + int(minutes) * 60 + float(seconds))


def pad_seconds(source_video, told=None) -> Optional[float]:
    """How long the PICTURE at ``source_video`` is, for padding the narration
    to it: the LARGER of what ffmpeg says about the file and what the caller
    was ``told``, or None when neither can say.

    **The caller's number is a belt; the probe is the braces.** F1's first
    round trusted the caller alone, on the brief's premise that "the app
    already knows the picture's length". It does not - it knows the AUDIO's.
    For an uncut project the length threaded in is ``waveform.duration_for``,
    the extracted ``audio.wav``'s header (``services/edit.py`` passes it
    straight through as ``output_duration`` when nothing is cut), and the
    fallback ``record["duration"]`` is faster-whisper's measurement of that
    same WAV. A 10.00 s picture whose audio stream stops at 6.01 s - a mic that
    stopped before the capture did, a clip ending on a silent card - then muxed
    to 6.01 s: **four seconds of picture deleted, with a success and no
    warning** (measured on both binaries). Probing asks ffmpeg itself and
    costs ~0.04 s, so it is simply done.

    **The larger, not the probe alone**, because the two failures are not
    symmetric: the pad's contract is "at least as long as the picture", so a
    pad that overshoots costs one ``-shortest`` trim and nothing else, while a
    pad that falls short silently deletes picture. The probe reads the
    container's own duration, which is the longest stream, so for a cut,
    picture-only intermediate it IS the picture; the told value keeps the
    edit's exact arithmetic (the sum of the kept ranges, known before ffmpeg
    ran) in play, and covers a file ffmpeg cannot measure at all. Both operands
    go through :func:`usable_duration`, so neither can be a number that hangs
    the mux.
    """
    known = [
        seconds for seconds in (_probe_duration(Path(source_video)), usable_duration(told))
        if seconds is not None
    ]
    return max(known) if known else None


def _pad_filter(video_duration) -> Optional[str]:
    """The audio filter that pads the narration to the picture, or None when
    there must not be one.

    ``apad=whole_dur=<n>`` pads with trailing silence to exactly ``n`` seconds
    and stops. A BARE ``apad`` pads for ever, and on the ffmpeg the app ships
    (7.1) that never finishes: measured against ``app/bin/ffmpeg.exe`` with a
    5 s picture and a 3 s narration, ``apad=whole_dur=5.000`` exits 0 in 0.1 s
    while a bare ``apad`` was still running when it was killed, leaving a
    48-byte ``ftyp`` stub with no ``moov`` atom. ``-shortest`` does NOT bound
    it, whatever the old docstring here claimed. So when the length is unknown
    there is no pad at all: the mux is then bounded by the shorter stream
    (the pre-``apad`` behaviour), which completes.
    """
    seconds = usable_duration(video_duration)
    return None if seconds is None else f"apad=whole_dur={seconds:.3f}"


def mux_audio_filter(video_duration) -> str:
    """The mux's one audio chain: the narration up-mixed to stereo at UNITY
    (:data:`UPMIX_STEREO`, the music graph's own ``pan`` - never ``-ac 2``,
    whose power-preserving rematrix takes a mono voice 3.01 dB down),
    resampled to 48 kHz (:data:`RESAMPLE_48K`: the TTS's 24 kHz cannot carry
    the re-voice's bitrate), then padded to the picture's length when that is
    known (:func:`_pad_filter`) and not padded at all when it is not."""
    pad = _pad_filter(video_duration)
    return ",".join([UPMIX_STEREO, RESAMPLE_48K] + ([pad] if pad else []))


def _build_replace_audio_cmd(source_video, audio, temp_output, video_duration):
    """ffmpeg args to put ``audio`` onto ``source_video`` keeping the full video.

    The new narration is usually shorter than the original (Whisper only makes
    segments for speech, so a music/transition outro after the last words is
    not covered). ``apad`` pads the audio with trailing silence to the video's
    duration so ``-shortest`` trims to the *video* length — the whole original
    video, including its closing transition, is kept with a silent tail rather
    than being cut to the shorter audio.

    **No argv this builds can run for ever.** The pad is emitted only for a
    length that is known (:func:`_pad_filter`); with none, the command's
    ``-af`` carries no ``apad`` at all and the mux ends with the shorter
    stream - the original video's tail is lost, which is a visible but FINITE
    cost, where the bare ``apad`` this used to emit hung the whole job for its
    ten-minute timeout.

    ``FFMPEG_PATH`` from ``utils.config``, never the bare name (trap 3): the
    older call sites that spawn "ffmpeg" work only because that module
    prepends the binary's directory to PATH at import.

    The picture is COPIED, never re-encoded (a re-voice with no cut keeps the
    source's frames untouched; a cut picture was already encoded by
    ``cut_picture``). The narration goes through :func:`mux_audio_filter` -
    stereo at unity, 48 kHz, padded - and is given AAC at
    :data:`REVOICE_AUDIO_BITRATE`, the music pass's own bitrate. (It used to
    stay at the TTS's 24 kHz mono, where AAC wrote about 100 kbit/s whatever
    it was given.) The file gets :data:`FASTSTART`, because with no music and
    no markers this mux is the file the user downloads.
    """
    from utils.config import FFMPEG_PATH

    if not FFMPEG_PATH:
        # Unreachable through ``replace_video_audio``, which refuses first;
        # here so that no argv this function returns can ever name a binary
        # the host may not have.
        raise RuntimeError(FFMPEG_MISSING)
    cmd = [
        FFMPEG_PATH,
        "-i", str(source_video),       # original video
        "-i", str(audio),               # new audio
        "-c:v", "copy",                 # keep video codec (no re-encode)
        "-map", "0:v:0",               # video from first input
        "-map", "1:a:0",               # audio from second input
        # stereo at unity, 48 kHz, then the trailing silence when the length is known
        "-af", mux_audio_filter(video_duration),
        "-c:a", "aac",                  # re-encode padded audio for MP4
        "-b:a", REVOICE_AUDIO_BITRATE,  # the re-voice's one audio bitrate
        *FASTSTART,                     # the index in front of the media
        "-shortest",                    # bound to the (now longer-or-equal) video
        "-y",                           # overwrite
        str(temp_output),
    ]
    return cmd


def replace_video_audio(
    source_video: Path,
    master_audio: Path,
    output_path: Path,
    background_music: Optional[Path] = None,
    music_volume: float = 0.15,
    video_duration: Optional[float] = None,
) -> bool:
    """Replace the audio track of a video with new TTS audio.

    Used for re-voicing: keeps the original video visuals but swaps
    the audio with a new TTS narration track.

    Args:
        source_video: Original video file (MP4/AVI/etc.)
        master_audio: New audio track (MP3/WAV) to overlay.
        output_path: Where to save the output video.
        background_music: Optional background music to mix in.
        music_volume: Volume level for background music (0.0-1.0).
        video_duration: What the caller believes the picture's length to be,
            in seconds - a BELT, never the whole answer. The picture is always
            probed as well and the LARGER of the two is padded to
            (:func:`pad_seconds`): the numbers the re-voice job can offer are
            measured on the extracted audio, not on the picture, so trusting
            one alone truncated a video whose audio stream was shorter than its
            frames. With neither available the mux runs with no pad at all.

    Returns:
        True if successful.
    """
    from utils.config import FFMPEG_PATH

    if not FFMPEG_PATH:
        logger.error("Cannot replace the audio: ffmpeg is not available")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_suffix(".tmp.mp4")

    try:
        # If background music, mix it with the TTS audio first
        if background_music and background_music.exists():
            from pydub import AudioSegment
            tts = AudioSegment.from_file(str(master_audio))
            music = AudioSegment.from_file(str(background_music))
            # Loop music to match TTS length
            if len(music) < len(tts):
                loops = (len(tts) // len(music)) + 1
                music = music * loops
            music = music[:len(tts)]
            # Apply volume
            music_db = 20 * (music_volume / 1.0) - 20  # rough dB from 0-1
            music = music + music_db
            mixed = tts.overlay(music)
            # WAV, not pydub's default MP3 (32 kbit/s for 24 kHz mono): the
            # mux is the one lossy encode. (No caller passes music today -
            # the re-voice's music is ``mix_music`` - so this is the belt.)
            mixed_path = master_audio.parent / "mixed_audio.wav"
            mixed.export(str(mixed_path), format="wav")
            audio_to_use = mixed_path
        else:
            audio_to_use = master_audio

        # Keep the video stream, swap the audio, and pad the audio to the full
        # video length so the original ending (its closing transition) is kept.
        # The picture is measured HERE, every time, whatever the caller said:
        # this is the last place that can tell the mux how long the picture
        # really is, and it must be right for any caller (pad_seconds).
        video_duration = pad_seconds(source_video, video_duration)
        if video_duration is None:
            # Said out loud, because the output loses the picture's tail: this
            # is the branch that used to hang (F1), and it is now reached only
            # when nobody - the caller, the record, the edit, or ffmpeg itself
            # - can say how long the picture is.
            logger.warning(
                "No length for %s: muxing without a pad, so the output ends with the "
                "shorter of picture and narration", source_video.name,
            )
        cmd = _build_replace_audio_cmd(source_video, audio_to_use, temp_output, video_duration)

        logger.info("Replacing audio: %s -> %s (video %.1fs)",
                    source_video.name, output_path.name, video_duration or -1)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            logger.error("ffmpeg failed: %s", result.stderr[:500])
            return False

        # Move temp to final
        if output_path.exists():
            output_path.unlink()
        temp_output.rename(output_path)

        logger.info("Audio replaced successfully: %s", output_path.name)
        return True

    except Exception as e:
        logger.error("Error replacing video audio: %s", e)
        if temp_output.exists():
            temp_output.unlink()
        return False


def cut_filtergraph(keep, origin: float = 0.0) -> str:
    """The one ffmpeg graph that cuts a picture to ``keep`` (``[[start, end],
    ...]`` in source seconds): every range trimmed and re-timed from zero, then
    concatenated. ``trim``'s end is exclusive, so a range's last instant is the
    first frame not shown. Video only (``a=0``): the narration is muxed on
    afterwards by ``replace_video_audio``, exactly as for an uncut picture.

    ``origin`` is the input seek the command puts ahead of the graph
    (``-ss origin`` BEFORE ``-i`` resets the input's timestamps so that moment
    is zero), so every trim is offset by it; 0 is the graph as written for the
    whole source. See ``cut_picture`` for why the seek is there.

    No frame rate is set anywhere: ``trim`` + ``setpts`` + ``concat`` carry the
    source's own cadence through (measured: 30 fps in, 30 fps out), and it
    must never be ``fps_for_transition``, which chooses 2 fps for a static deck
    and would wreck a screen recording.
    """
    parts = [
        f"[0:v]trim=start={float(start) - origin:.3f}:end={float(end) - origin:.3f},setpts=PTS-STARTPTS[v{i}]"
        for i, (start, end) in enumerate(keep)
    ]
    inputs = "".join(f"[v{i}]" for i in range(len(keep)))
    return ";".join(parts) + f";{inputs}concat=n={len(keep)}:v=1:a=0[v]"


# How often the picture cut looks at its cancel flag and its deadline while
# ffmpeg runs, and the clock it reads (a seam the tests move).
CUT_POLL_SECONDS = 0.5
_clock = time.monotonic


def cut_timeout(keep) -> float:
    """How long a cut may take: 60 s plus three times the DECODE REACH - from
    the first kept start to the last kept end - never the output's length.
    ``trim`` is a filter and runs after the decode, so the work is everything
    ffmpeg has to decode to get there (the input seek in ``cut_picture`` moves
    the start of that stretch up to the first kept range; nothing moves its
    end). Measured rate at x264 ``medium`` (Q1): about 6 s per minute of
    1080p30 reach on the bundled 7.1 (32.1 s and 34.1 s for the corpus's
    341 s; it was about 2.8 s per minute at ``ultrafast``), so three seconds
    a second is headroom of some thirty times; a 4K30 cut at ``medium`` ran
    at 0.50 s per second of reach on the same i9, six times inside it. Any
    future filtergraph cut inherits this rule."""
    return 60 + 3 * (float(keep[-1][1]) - float(keep[0][0]))


# ffmpeg's header line for a video stream carries its base frame rate as
# "<n> tbr" ("30 tbr", "29.97 tbr", "1k tbr"): ffmpeg's own guess of the rate
# the stream's timestamps are laid on, which is the stream's nominal rate for
# a variable-rate recording too (a VFR clip reads "22.69 fps, 30 tbr").
_TBR = re.compile(r"Stream #\d+:\d+\S*: Video: .*?(\d+(?:\.\d+)?)(k?) tbr")
# Beyond this a "frame rate" is a container's time base, not a picture's.
MAX_FRAME_RATE = 240.0


def source_frame_rate(path) -> Optional[str]:
    """The first video stream's base frame rate as ffmpeg prints it ("30",
    "29.97"), or None when it cannot be told or is not a picture's rate
    (above :data:`MAX_FRAME_RATE`). Read from ffmpeg's own header
    (:func:`_ffmpeg_header`), never a prober, like every length the engine
    reads."""
    found = _TBR.search(_ffmpeg_header(path))
    if not found or found.group(2):  # "1k tbr" and up: a time base, not a picture
        return None
    return found.group(1) if 0 < float(found.group(1)) <= MAX_FRAME_RATE else None


def _stop(proc) -> None:
    """Kill an ffmpeg that must not finish and reap it. Never raises: the
    caller is already on a failure path, and a process that is gone by the
    time this runs is exactly the outcome wanted."""
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001 - see the docstring
        pass


def _run_until_done(
    cmd, log: Path, timeout: float, cancelled,
    on_poll: Optional[Callable[[], None]] = None, cwd: Optional[Path] = None,
) -> tuple[str, "subprocess.Popen"]:
    """Run one ffmpeg command with its stderr in ``log`` (a file, never a
    pipe nobody reads: a chatty run would fill it and stall), polling it
    every ``CUT_POLL_SECONDS`` until it exits, ``cancelled()`` answers True
    or ``timeout`` seconds pass - and killing it on either of the last two.
    Returns the outcome (``"finished"``, ``"cancelled"``, ``"timeout"``) and
    the process, whose ``returncode`` the caller reads when it finished. The
    one loop the picture cut, the music mix and the deck render share.

    ``on_poll`` is called on every poll while ffmpeg runs (the deck render
    reads its progress file there); ``cwd`` is the directory ffmpeg runs in
    (the deck render's scratch, so its many stills are named short)."""
    with open(log, "w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
            cwd=str(cwd) if cwd else None,
        )
        outcome = _wait_until_done(proc, timeout, cancelled, on_poll)
    return outcome, proc


def _wait_until_done(proc, timeout: float, cancelled, on_poll: Optional[Callable[[], None]] = None) -> str:
    """Poll a running ffmpeg every ``CUT_POLL_SECONDS`` until it exits,
    ``cancelled()`` answers True or ``timeout`` seconds pass - killing it on
    either of the last two - and return the outcome (``_run_until_done``'s
    loop, for a process started elsewhere: the deck render's sound encode
    runs beside its picture)."""
    deadline = _clock() + timeout
    outcome = "finished"
    while proc.poll() is None:
        if on_poll:
            on_poll()
        if cancelled():
            outcome = "cancelled"
            break
        if _clock() >= deadline:
            outcome = "timeout"
            break
        time.sleep(CUT_POLL_SECONDS)
    if outcome != "finished":
        _stop(proc)
    return outcome


def cut_picture(
    source, keep, dst, video_bitrate: str = "",
    cancel_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """Cut ``source``'s PICTURE to the kept ranges, writing a video-only MP4
    at ``dst``. True when it is there; False (and a log line) on any failure,
    never an exception - the same contract as ``replace_video_audio``.

    One ffmpeg run, re-encoding everything at libx264
    :data:`REVOICE_X264_PRESET` (``medium``), H.264
    :data:`REVOICE_H264_PROFILE` (High) and 4:2:0 (:func:`h264_params`) -
    the final deck render's own encode, because this picture is the one the
    re-voiced file carries (Q1; it was ``ultrafast``, which x264 flags
    Constrained Baseline). ``video_bitrate`` is the output preset's, "" for
    the codec default, as ``write_videofile`` takes it. A stream copy is not
    an option: a cut lands on P-frames with no reference picture, and the
    source's keyframes are two seconds apart, so snapping to one can miss by
    more than a sentence. A re-voice with NO cut never comes here: its mux
    copies the source's picture untouched (``_build_replace_audio_cmd``).

    **The encoder is told the source's frame rate** (``-x264-params
    fps=<rate>``, :func:`source_frame_rate`), and nothing else about timing.
    On the bundled 7.1, ``setpts`` leaves the graph's frame rate unknown and
    ``concat`` hands on a 1/1000000 time base, so x264 took the stream for a
    million frames a second and declared H.264 Level 6.2 for a 1080p30
    picture - a level a player that checks it (a TV, a phone) may refuse.
    (8.0.1 hands on the input's time base and declared the right level
    already; the parameter changes nothing there.) Told the rate, it
    declares the level the picture needs (4.0 for 1080p30, measured on the
    bundled 7.1) at the same size and quality. ``-r`` would do that too, but
    it forces a constant rate - duplicating and dropping frames of a
    variable-rate recording - where x264's own ``fps`` changes no timestamp:
    the graph's cadence is kept exactly (measured: the same frame count and
    the same timestamps, constant-rate and variable-rate source alike). With
    no rate to tell, nothing is passed and the cut is what it always was.

    **The work is sized by how far into the source ffmpeg has to DECODE, not
    by how much comes out.** ``trim`` is a filter: it runs after the decode,
    so left to itself ffmpeg decodes every frame from the start of the file to
    the last kept range's end and throws most of them away. Two things follow.

    - ``-ss <first kept start>`` goes BEFORE ``-i``, so the decode starts at
      the first range (an input seek lands on the keyframe before it and
      discards up to the exact frame), and every trim in the graph is offset
      by that origin, because the seek resets the input's timestamps. Measured
      on the 341 s corpus source: a 5 s keep at its tail cost 3.9 s without the
      seek and 0.87 s with it, and on a two-hour source the seekless cut was
      killed by its own timeout. It is frame-exact: the decoded frames'
      ``-f framemd5`` output is identical with and without the seek. That is a
      DEV-ONLY check, since the suite fakes ffmpeg - run
      ``ffmpeg -ss S0 -i src -filter_complex "<graph offset by S0>" -map "[v]"
      -f framemd5 -`` and the same without ``-ss`` and the offset, and diff.
    - The timeout is bound by the decode reach, ``keep[-1][1] - keep[0][0]``,
      not by the output's length (``cut_timeout``): with the seek in place
      that is exactly the stretch ffmpeg decodes.

    ``FFMPEG_PATH`` from ``utils.config``, never the bare name: the older call
    sites that spawn "ffmpeg" work only because that module prepends the
    binary's directory to PATH at import, and this step is the first that
    runs ONLY for an edited project, so it must not inherit the accident.

    ``cancel_check`` (the job's ``services.jobs.cancel_requested_here``) is
    polled every ``CUT_POLL_SECONDS`` while ffmpeg runs, and a cancel KILLS
    it: this is the one step of a re-voice that looks at the flag, and a cut
    can run for a minute or more, so Cancel has to mean stop rather than
    "discard the result when it is done". A cancelled or failed cut leaves
    nothing behind. Written to a ``.part`` file and published with
    ``utils.helpers.replace_with_retry``, because a render holds several files
    open across seconds and Windows refuses a rename under an open handle now
    and then. ffmpeg's stderr goes to a file beside the part rather than a
    pipe, so a chatty run can never fill a pipe nobody is reading and stall.
    """
    from utils.config import FFMPEG_PATH
    from utils.helpers import replace_with_retry

    source, dst = Path(source), Path(dst)
    if not FFMPEG_PATH:
        logger.error("Cannot cut the picture: ffmpeg is not available")
        return False
    if not keep:
        logger.error("Cannot cut the picture: no ranges to keep")
        return False

    origin = float(keep[0][0])
    reach = float(keep[-1][1]) - origin
    length = sum(float(end) - float(start) for start, end in keep)
    timeout = cut_timeout(keep)
    part = dst.with_suffix(".part.mp4")
    log = part.with_suffix(".log")
    rate = source_frame_rate(source)
    cmd = [
        FFMPEG_PATH, "-hide_banner", "-nostats",
        "-ss", f"{origin:.3f}", "-i", str(source),
        "-filter_complex", cut_filtergraph(keep, origin),
        "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", REVOICE_X264_PRESET, *h264_params(REVOICE_H264_PROFILE),
    ]
    if rate:
        cmd += ["-x264-params", f"fps={rate}"]
    else:
        logger.warning("No frame rate for %s: the cut's H.264 level is x264's own guess", source.name)
    if video_bitrate:
        cmd += ["-b:v", video_bitrate]
    cmd += ["-y", str(part)]

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    try:
        if _cancelled():
            logger.info("Picture cut cancelled before it started")
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Cutting the picture: %d range(s), %.1fs kept, %.1fs of %s decoded",
                    len(keep), length, reach, source.name)
        outcome, proc = _run_until_done(cmd, log, timeout, _cancelled)
        if outcome == "cancelled":
            logger.info("Picture cut cancelled; ffmpeg stopped and the cut discarded")
            return False
        if outcome == "timeout":
            logger.error("Cutting the picture took longer than %.0fs and was stopped", timeout)
            return False
        if proc.returncode != 0:
            tail = log.read_text(encoding="utf-8", errors="replace")[-800:] if log.is_file() else ""
            logger.error("ffmpeg failed to cut the picture (exit %s): %s", proc.returncode, tail)
            return False
        if _cancelled():
            logger.info("Picture cut cancelled; the cut is discarded")
            return False
        if not part.is_file() or part.stat().st_size == 0:
            logger.error("ffmpeg reported success but wrote no picture")
            return False
        replace_with_retry(part, dst)
        logger.info("Picture cut: %s (%.1fs)", dst.name, length)
        return True
    except Exception as e:
        logger.error("Error cutting the picture: %s", e)
        return False
    finally:
        # No-ops after a successful publish; the cleanup on every other exit.
        for leftover in (part, log):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass


# The one step that makes any input two channels before it is mixed, at
# UNITY: ``pan`` takes exactly the channels its mapping names and drops every
# other one, present or not, so mono (FC alone) becomes L = R = M and stereo
# passes through untouched - one string for both, with nothing probed
# (traps 2, 30). ``aformat=channel_layouts=stereo`` is the trap it replaces:
# its rematrix is power-preserving, so a mono narration arrived 3.01 dB down
# (measured on ffmpeg 8.0.1 and on the bundled 7.1).
#
# The cost of one static mapping is a layout with MORE than two channels: a
# 5.1 clip would keep FL, FR and FC and lose LFE, BL and BR, with its FC at
# unity where a standard down-mix takes it to 0.7071 (measured). Mono and
# stereo are what the app makes, what a music bed is, and what the browser's
# audition agrees with - so rather than fold a surround file badly here, the
# library refuses one at upload (``services.music.MAX_CHANNELS``), at the one
# place that decodes it. Every clip that reaches this graph is mono or
# stereo. See ``music_filtergraph``.
UPMIX_STEREO = "pan=stereo|FL=FL+FC|FR=FR+FC"


def music_filtergraph(clips, inputs) -> str:
    """The one ffmpeg graph that lays music clips under a video's own audio
    (the edit's music lane, spec §12.4). ``clips`` carry ``path, at, in, out,
    gain, fade_in, fade_out`` - ``at`` in the OUTPUT's seconds, ``in``/``out``
    in the file's, ``gain`` a LINEAR factor (trap 26: never the old pydub
    "rough dB" formula); ``inputs`` is the ordered list of DISTINCT file
    paths, input ``k`` being ``inputs[k - 1]`` (the video is input 0), so a
    file used by several clips is decoded from one input.

    Per clip, in order: the slice (``atrim``, re-timed from zero), the
    up-mix to stereo (:data:`UPMIX_STEREO`), 48 kHz (:data:`RESAMPLE_48K`),
    the level, a linear fade in and
    out (``afade``'s default ``tri`` curve - the same ramp the audition's
    ``GainNode`` plays, and a fade of 0 is left out), then the placement
    (``adelay`` in whole milliseconds, one delay per stereo channel). The
    clips are summed into the bed with ``normalize=0`` (trap 28: ``amix``
    otherwise divides by the input count) and ``dropout_transition=0`` (no
    ramp when one ends); a single clip is the bed. The voice, up-mixed the
    same way, is mixed with the bed under ``duration=first`` - the output
    ends where the voice does - and ``normalize=0`` again, so the
    narration's level is untouched (trap 27: no ducking, and the voice never
    fades).

    **The up-mix is UNITY, and that is why it is ``pan`` and not
    ``aformat``.** ffmpeg's default mono-to-stereo rematrix is
    power-preserving - each output channel gets M/sqrt(2), i.e. -3.01 dB -
    so a mono narration (the local kokoro path writes mono 24 kHz) came out
    audibly quieter with music than without, from the up-mix alone and not
    from ``amix``. ``pan=stereo|FL=FL+FC|FR=FR+FC`` takes exactly the
    channels it names and drops every other one, so the one graph serves a
    mono and a stereo voice with no probe (traps 2 and 30) and changes
    neither: measured at 0.00 dB per channel against the source on
    the dev ffmpeg 8.0.1 AND the bundled 7.1 essentials, where
    ``aformat=channel_layouts=stereo`` measured -3.01 dB for mono. It is
    also what the browser does (Web Audio up-mixes mono as L = R = M), so
    E4b's audition and the render agree exactly (decision 4).

    **Every input is resampled to 48 kHz before it is mixed** (Q1): ``amix``
    runs at one rate, and left to itself the graph settled on the voice's -
    the TTS's 24 kHz - so the music was cut down to a 12 kHz band and the
    AAC, at 24 kHz, could not carry the bitrate it was given. The voice
    arrives at 48 kHz stereo from the mux already; it is resampled here too
    so the graph never depends on what came before it.

    Raises ``ValueError`` for an empty clip list: there is no graph for no
    music, and one written anyway names a ``[m1]`` that does not exist.
    """
    if not clips:
        raise ValueError("A music graph needs at least one clip.")
    paths = [str(path) for path in inputs]
    chains = []
    for k, clip in enumerate(clips, start=1):
        index = paths.index(str(clip["path"])) + 1
        start, end = float(clip["in"]), float(clip["out"])
        fade_in, fade_out = float(clip.get("fade_in") or 0.0), float(clip.get("fade_out") or 0.0)
        steps = [
            f"[{index}:a]atrim=start={start:.3f}:end={end:.3f}",
            "asetpts=PTS-STARTPTS",
            UPMIX_STEREO,
            RESAMPLE_48K,
            f"volume={float(clip['gain']):.3f}",
        ]
        if fade_in > 0:
            steps.append(f"afade=t=in:st={0:.3f}:d={fade_in:.3f}")
        if fade_out > 0:
            steps.append(f"afade=t=out:st={end - start - fade_out:.3f}:d={fade_out:.3f}")
        ms = round(float(clip["at"]) * 1000)
        steps.append(f"adelay={ms}|{ms}")
        chains.append(",".join(steps) + f"[m{k}]")
    if len(clips) > 1:
        labels = "".join(f"[m{k}]" for k in range(1, len(clips) + 1))
        chains.append(f"{labels}amix=inputs={len(clips)}:normalize=0:dropout_transition=0[bed]")
        bed = "[bed]"
    else:
        bed = "[m1]"
    chains.append(f"[0:a]{UPMIX_STEREO},{RESAMPLE_48K}[v]")
    chains.append(f"[v]{bed}amix=inputs=2:duration=first:normalize=0[a]")
    return ";".join(chains)


def music_timeout(output_seconds: float) -> float:
    """How long the music mix may take: 60 s plus twice the OUTPUT's length.
    The work is decoding the clips and one AAC encode of the output's
    length (the picture is copied): measured at 8.25 s for a 336 s output
    under two five-minute MP3s on the bundled ffmpeg 7.1, so this is
    generous by forty times."""
    return 60 + 2 * float(output_seconds)


def mix_music(
    video_in, clips, video_out, cancel_check: Optional[Callable[[], bool]] = None,
    output_seconds: Optional[float] = None,
) -> bool:
    """Lay ``clips`` (see ``music_filtergraph``) under ``video_in``'s audio,
    writing ``video_out`` - which may be the same file: the output goes to
    ``<video_out>.music.part.mp4`` and replaces it at the end. True when it
    is there; False (and a log line) on any failure, never an exception -
    the contract of ``cut_picture`` and ``replace_video_audio``.

    One ffmpeg run over the muxed output, ``FFMPEG_PATH`` (never the bare
    name, trap 3): the video stream COPIED (trap 29 - a re-encode here would
    be the cut's half-minute again for nothing) and the audio re-encoded once,
    at 48 kHz stereo (the graph's own resample), given AAC at
    :data:`REVOICE_AUDIO_BITRATE` (192 kbit/s, the mux's bitrate too),
    ``+faststart`` for the page's player. ``cancel_check``
    is polled while ffmpeg runs and a cancel kills it, exactly as the
    picture cut's is; the deadline is ``music_timeout(output_seconds)``,
    or, when the caller does not know the output's length, the same rule
    over the clips' furthest end. A cancelled, failed or stalled mix leaves
    nothing behind and ``video_out`` as it was. Refuses up front, with no
    ffmpeg started, when a clip's file is not on disk: the library's
    verdict (``missing``) is the caller's to check first, and this is the
    belt to it - never silence in a file's place (trap 25).
    """
    from utils.config import FFMPEG_PATH
    from utils.helpers import replace_with_retry

    video_in, video_out = Path(video_in), Path(video_out)
    if not FFMPEG_PATH:
        logger.error("Cannot mix the music: ffmpeg is not available")
        return False
    if not clips:
        logger.error("Cannot mix the music: no clips")
        return False
    inputs: list[str] = []
    for clip in clips:
        path = str(clip["path"])
        if not Path(path).is_file():
            logger.error("Cannot mix the music: %s is missing", path)
            return False
        if path not in inputs:
            inputs.append(path)

    reach = max(float(clip["at"]) + float(clip["out"]) - float(clip["in"]) for clip in clips)
    timeout = music_timeout(output_seconds if output_seconds else reach)
    part = video_out.with_suffix(".music.part.mp4")
    log = part.with_suffix(".log")
    cmd = [FFMPEG_PATH, "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(video_in)]
    for path in inputs:
        cmd += ["-i", path]
    cmd += [
        "-filter_complex", music_filtergraph(clips, inputs),
        "-map", "0:v", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", REVOICE_AUDIO_BITRATE, *FASTSTART,
        str(part),
    ]

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    try:
        if _cancelled():
            logger.info("Music mix cancelled before it started")
            return False
        video_out.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Mixing the music: %d clip(s) from %d file(s) under %s", len(clips), len(inputs), video_in.name)
        outcome, proc = _run_until_done(cmd, log, timeout, _cancelled)
        if outcome == "cancelled":
            logger.info("Music mix cancelled; ffmpeg stopped and the mix discarded")
            return False
        if outcome == "timeout":
            logger.error("Mixing the music took longer than %.0fs and was stopped", timeout)
            return False
        if proc.returncode != 0:
            tail = log.read_text(encoding="utf-8", errors="replace")[-800:] if log.is_file() else ""
            logger.error("ffmpeg failed to mix the music (exit %s): %s", proc.returncode, tail)
            return False
        if _cancelled():
            logger.info("Music mix cancelled; the mix is discarded")
            return False
        if not part.is_file() or part.stat().st_size == 0:
            logger.error("ffmpeg reported success but wrote no video")
            return False
        replace_with_retry(part, video_out)
        logger.info("Music mixed: %s (%d clip(s))", video_out.name, len(clips))
        return True
    except Exception as e:
        logger.error("Error mixing the music: %s", e)
        return False
    finally:
        # No-ops after a successful publish; the cleanup on every other exit.
        for leftover in (part, log):
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass


def _build_master_audio(
    slide_clips: list,
    voice_start_delay: float,
    transition_pause: float,
    transition_sound_path=None,
    profile: Optional[OnsetProfile] = None,
    intro_offset: float = 0.0,
    gaps_out: Optional[list] = None,
) -> tuple:
    """Build a single continuous audio track from all slide audio files.

    Instead of attaching audio per-clip (which causes clicks at boundaries),
    this concatenates all audio into one seamless pydub track with precise
    silence gaps for voice delay and transitions.

    ``profile`` is the onset profile of the provider that synthesised the
    clips (trim threshold, cap, opening boost); None falls back to the
    studio's configured provider.

    ``intro_offset`` is the duration of the intro title card, when there is
    one: the card has no narration and sits before the first slide, so the
    track opens with that much silence - otherwise every slide's narration
    would play that much early.

    ``gaps_out``, when given, gets the seconds of the gap the track left
    after each slide appended to it (0 after the last), so the picture can
    follow the track exactly: the pause, in whole milliseconds, or - after a
    narrated slide, when there is a transition sound longer than the pause
    - the sound's length (``create_video``).

    Returns:
        (master_audio_path, slide_durations) where slide_durations is a list
        of (visual_duration, audio_start_offset) per slide for syncing.
        Returns (None, []) if no audio files are found.
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        return None, []

    profile = profile or _active_onset_profile()
    delay_ms = int(voice_start_delay * 1000)
    intro_ms = int(round(max(0.0, intro_offset) * 1000))
    delay_silence = AudioSegment.silent(duration=delay_ms) if delay_ms > 0 else AudioSegment.empty()

    # Load transition sound if available
    trans_sound = None
    if transition_sound_path and Path(transition_sound_path).exists():
        try:
            trans_sound = AudioSegment.from_file(str(transition_sound_path))
        except Exception:
            pass

    master = AudioSegment.silent(duration=intro_ms) if intro_ms > 0 else AudioSegment.empty()
    slide_info = []  # (visual_duration_s,) per slide
    has_any_audio = False

    for i, clip_info in enumerate(slide_clips):
        # The gap after this slide: its own pause override, else the job's.
        pause_ms = int(pause_after(clip_info, transition_pause) * 1000)
        pause_silence = AudioSegment.silent(duration=pause_ms) if pause_ms > 0 else AudioSegment.empty()

        audio_path = clip_info.audio_path
        if not audio_path or not Path(audio_path).exists():
            # No audio — use default duration
            dur = clip_info.duration or 5.0
            slide_info.append(dur)
            silence_chunk = AudioSegment.silent(duration=int(dur * 1000))
            master += silence_chunk
            if i < len(slide_clips) - 1 and pause_ms > 0:
                master += pause_silence
            if gaps_out is not None:
                gaps_out.append(pause_ms / 1000.0 if i < len(slide_clips) - 1 and pause_ms > 0 else 0.0)
            continue

        has_any_audio = True

        # Trim TTS silence, then level the opening to match body volume, with
        # the synthesising provider's onset profile so quieter providers
        # (e.g. Kokoro) are not over-trimmed by Edge's fixed threshold.
        _trim_leading_silence(str(audio_path), profile=profile)
        slide_audio = AudioSegment.from_file(str(audio_path))
        slide_audio = _level_opening(slide_audio, profile=profile)

        # Voice start delay: silence before narration
        slide_chunk = delay_silence + slide_audio
        slide_dur_s = len(slide_chunk) / 1000.0
        slide_info.append(slide_dur_s)
        master += slide_chunk

        # Transition gap between slides (except after last)
        gap_ms = 0
        if i < len(slide_clips) - 1:
            if trans_sound and pause_ms > 0:
                # Overlay transition sound on the pause
                gap_ms = max(pause_ms, len(trans_sound))
                gap = AudioSegment.silent(duration=gap_ms)
                gap = gap.overlay(trans_sound)
                master += gap[:gap_ms]
            elif pause_ms > 0:
                gap_ms = pause_ms
                master += pause_silence
        if gaps_out is not None:
            gaps_out.append(gap_ms / 1000.0)

    if not has_any_audio:
        return None, []

    # Export master track to temp file
    import tempfile
    master_path = Path(tempfile.mktemp(suffix=".mp3"))
    master.export(str(master_path), format="mp3", bitrate="192k")
    logger.info("Built master audio: %.1fs, %d slides", len(master) / 1000.0, len(slide_info))
    return master_path, slide_info


@dataclass
class SlideClipInfo:
    """Information for creating a slide clip."""
    slide_index: int
    image_path: Optional[Path] = None
    video_path: Optional[Path] = None  # For animated slides
    audio_path: Optional[Path] = None
    duration: Optional[float] = None  # If None, uses audio duration
    pause_after: Optional[float] = None  # Seconds of pause after this slide; None = the creator's transition_pause


# ── the deck render: ffmpeg composes the picture (T1) ─────────────────────────
#
# Python prepares STILLS and the narration track, and never touches a frame:
# each slide's image (linked into a scratch folder under a short name, or
# flattened onto black once when it has transparency), the title cards and the
# watermark, drawn once each with Pillow (``core.fonts.text_image``), and the
# master track (``_build_master_audio``). ffmpeg builds and encodes every frame
# from them. (moviepy used to build each frame in numpy and pipe raw RGB to
# ffmpeg: any transition raised the whole deck to 24 fps and the watermark was
# composited onto every frame - 42 minutes for the owner's 20-slide deck.)
#
# A still is decoded and scaled ONCE - ``scale`` with lanczos to the output
# size, stretched exactly as ``ImageClip.resized(resolution)`` stretched it, no
# letterbox - converted to 4:2:0 once and repeated in memory by ``loop``, so a
# static stretch costs ffmpeg nothing but the encode. A transition's frames are
# split off the same still and drawn in RGB by ffmpeg's own filters, on a clock
# that follows the slide's EXACT start on the master track: ``fade`` (through
# black or white), ``pad`` + ``crop`` (a slide-in) and a per-frame ``scale`` +
# ``crop`` + ``fade`` (the zoom). A pause is a ``color`` source. The watermark
# is one ``overlay`` over the whole picture, as moviepy composited it over the
# whole video.
#
# **A transition's clock never goes below zero.** A slide whose start rounds
# DOWN to a frame has its first frame a fraction of a frame BEFORE its exact
# start, and ffmpeg's ``fade`` never fades at all when the first timestamp it
# sees is negative (measured on the bundled 7.1 and on 8.0.1: every frame comes
# out at full brightness - 8 of the owner's 19 fading slides cut in unfaded).
# So every transition chain runs on the slide's own time PLUS
# ``transition_clock_shift`` (a whole second), and the fade's start and the
# slide and zoom expressions are moved by the same amount.
#
# **The watermark sits on the pixel it was placed at.** In 4:2:0 ``overlay``
# snaps an odd x or y DOWN to the even pixel before it (its chroma is half the
# size), which drew the owner's mark one row too high. The watermark's still is
# padded with a clear row and column where its corner is odd
# (``even_origin``), and overlaid at the even pixel before.
#
# **The picture is rendered in chunks** of at most ``chunk_limit`` segments
# (a segment is a card, a slide or the pause after one), each chunk one ffmpeg
# run that encodes its frames with the render's own settings
# (``encode_settings``); the chunks are joined by the concat demuxer with a
# stream copy, and the narration and music, encoded by one ffmpeg run of their
# own beside the picture's, are muxed in with them. One filter graph over a
# whole deck would be simpler and was measured first, but ffmpeg configures
# and primes every chain of a graph before its first frame, so its memory grows
# with the slides (on the owner's deck with a crossfade: 3.0 GB at 1080p and
# 7.2 GB for 60 slides as one graph, 1.4 GB for either in chunks;
# ``docs/porting/generation-options.md``, *As built - T1*). A chunk holds at
# most ``chunk_limit`` segments' chains, so a 60-slide deck needs no more
# memory than a 20-slide one. The join keeps every frame, in order, on the
# bundled 7.1 and on 8.0.1, as long as no chunk is a single frame (on 8.0.1 a
# one-frame chunk broke it), so a chunk is never shorter than
# ``MIN_CHUNK_FRAMES`` unless it is the whole deck.

# The zoom-in starts at this scale and settles at 1.0 over the transition.
ZOOM_FROM = 1.3
# The edge each slide transition brings the incoming slide in from. (Before T1
# "slide-left" and "slide-right" drew the same - both came in from the left -
# and "slide-up" and "slide-down" both came in from the top.)
SLIDE_FROM = {"slide-left": "right", "slide-right": "left", "slide-up": "bottom", "slide-down": "top"}
# The colour each fading transition passes through. "crossfade" is a fade
# through the black pause, as it has always been, not a dissolve.
FADE_COLOURS = {"fade-to-black": "black", "crossfade": "black", "fade-to-white": "white"}
# A still's colour tags are dropped as it is read: a PNG says sRGB/BT.709,
# moviepy's raw RGB said nothing, and the file has always gone out untagged.
UNTAGGED = "setparams=color_primaries=unknown:color_trc=unknown:colorspace=unknown"
# The chunk size: this many 1080p pictures' worth of segments at a time (8 at
# 1080p, 18 at 720p, 4 at 4K - never fewer than 4), and never a chunk under
# MIN_CHUNK_FRAMES frames unless it is the whole deck. Measured on the owner's
# 20-slide deck with a crossfade and the watermark: ffmpeg's peak 1.37 GB at 8
# and 1.8 GB at 16 for the same time (3.0 GB as one graph); at 4K, 4.0 GB at 4
# (10.0 GB as one graph).
CHUNK_PIXELS = 8 * 1920 * 1080
MIN_CHUNK_SEGMENTS = 4
MIN_CHUNK_FRAMES = 48
# How long one ffmpeg run of the render may take: a base plus a budget per
# frame, scaled by the picture's size against 1080p. Measured on the i9-9900K
# at x264 ``medium`` on the owner's deck: about 0.003 s a 1080p frame with a
# crossfade (0.0045 with the zoom) and 0.011 s a 4K frame, so this is eleven to
# eighteen times headroom (plus the base) on that box.
RENDER_BASE_SECONDS = 120.0
RENDER_SECONDS_PER_FRAME = 0.05
_PIXELS_1080P = 1920 * 1080
# The positions of the watermark: (horizontal, vertical), as moviepy placed them.
WATERMARK_POSITIONS = {
    "top-left": ("left", "top"),
    "top-right": ("right", "top"),
    "bottom-left": ("left", "bottom"),
    "bottom-right": ("right", "bottom"),
    "center": ("center", "center"),
}
_VIDEO_STREAM = re.compile(r"Stream #\d+:\d+\S*: Video: ")
_PROGRESS_FRAME = re.compile(rb"^frame=\s*(\d+)", re.MULTILINE)


def round_frames(seconds: float, fps: float) -> int:
    """``seconds × fps`` rounded half up: the frame a moment falls on."""
    return int(math.floor(float(seconds) * fps + 0.5))


def transition_clock_shift(fps: float) -> float:
    """The seconds added to a slide's own time on every transition chain, so
    no transition filter ever sees a negative timestamp (see "A transition's
    clock never goes below zero" above): a whole number of seconds, at least
    one frame - a slide's first frame is at most half a frame before its
    exact start, so the clock starts at half a frame or later."""
    return float(max(1, math.ceil(1.0 / fps)))


def even_origin(layer: Image.Image, x: int, y: int) -> Tuple[Image.Image, int, int]:
    """``layer`` and the corner to overlay it at so that it lands on exactly
    (``x``, ``y``) in a 4:2:0 picture: ``overlay`` snaps an odd coordinate
    down to the even one before it, so where a coordinate is odd the layer
    gains one clear column (or row) on that side and is placed one pixel
    earlier. An even corner is returned as it is."""
    dx, dy = x % 2, y % 2
    if not dx and not dy:
        return layer, x, y
    padded = Image.new("RGBA", (layer.width + dx, layer.height + dy), (0, 0, 0, 0))
    padded.paste(layer, (dx, dy))
    return padded, x - dx, y - dy


def frame_boundaries(durations, fps: float) -> List[int]:
    """Where each segment starts, in frames, and where the last one ends.

    Boundary k is the CUMULATIVE time ``t_k`` rounded to a frame - never a sum
    of per-segment roundings - so no boundary is more than half a frame from
    the moment the master track puts it at, however many segments a deck has.
    (Rounded segment by segment, a 60-slide deck could drift by 30 frames.)

    The LAST boundary rounds UP: the picture never ends before the track
    does, so the narration's last words are never cut to the last frame (at
    2 fps, rounding to the nearest frame dropped up to a quarter of a second
    of them). The picture is then at most one frame longer than the track,
    over silence."""
    bounds, elapsed = [0], 0.0
    for seconds in durations:
        elapsed += float(seconds)
        bounds.append(round_frames(elapsed, fps))
    if len(bounds) > 1:
        # A millionth of a frame of slack: a total that is a whole number of
        # frames but for float noise must not gain a frame.
        bounds[-1] = max(bounds[-2], int(math.ceil(elapsed * fps - 1e-6)))
    return bounds


def render_timeout(frames: int, size: Tuple[int, int]) -> float:
    """How long one ffmpeg run of the deck render may take, for ``frames``
    frames of ``size`` (see :data:`RENDER_SECONDS_PER_FRAME`)."""
    scale = max(1.0, (size[0] * size[1]) / _PIXELS_1080P)
    return RENDER_BASE_SECONDS + RENDER_SECONDS_PER_FRAME * frames * scale


@dataclass
class DeckSegment:
    """One stretch of the deck's picture, in the master track's seconds.

    ``image`` / ``video`` are the slide's picture - its source path as
    planned, the still's name in the scratch folder once prepared; a segment
    with neither (a pause) is a ``color`` source. ``card`` is a title card's
    (title, subtitle), drawn into a still when the stills are prepared."""
    seconds: float
    kind: str = "slide"            # "slide", "pause" or "card"
    image: Optional[object] = None
    video: Optional[object] = None
    color: str = "black"
    card: Optional[Tuple[str, str]] = None
    effect_in: str = ""            # "fade", "slide-from-<edge>" or "zoom"
    effect_out: str = ""           # "fade"
    effect_seconds: float = 0.0
    effect_color: str = "black"


def _transition_filter(effect: str, opening: bool, seg: DeckSegment, size: Tuple[int, int], shift: float) -> str:
    """The ffmpeg filters that draw one transition on a slide's RGB frames,
    whose timestamps are the slide's own seconds plus ``shift``
    (``transition_clock_shift``: ``shift`` = the slide's start on the master
    track), so that no filter here sees a negative timestamp. ``p`` runs
    from 0 to 1 over the transition, as moviepy's ``t / fade_dur`` did; a
    frame before the slide's exact start is at ``p`` = 0."""
    w, h = size
    fd = seg.effect_seconds
    p = f"clip((t-{shift:g})/{fd:.6f},0,1)"
    if effect == "fade":
        colour = "" if seg.effect_color == "black" else f":color={seg.effect_color}"
        if opening:
            return f"fade=t=in:st={shift:.6f}:d={fd:.6f}{colour}"
        return f"fade=t=out:st={seg.seconds - fd + shift:.6f}:d={fd:.6f}{colour}"
    if effect.startswith("slide-from-"):
        # The incoming slide is offset by trunc(size × (1 - p)) pixels, over
        # black: padded with black on the side it moves away from, then cut
        # back to the frame with a moving window.
        edge = effect[len("slide-from-"):]
        if edge in ("left", "right"):
            off = f"trunc({w}*(1-{p}))"
            if edge == "left":   # enters from the left edge, moving right
                return f"pad=w={2 * w}:h={h}:x=0:y=0:color=black,crop=w={w}:h={h}:x='{off}':y=0:exact=1"
            return f"pad=w={2 * w}:h={h}:x={w}:y=0:color=black,crop=w={w}:h={h}:x='{w}-{off}':y=0:exact=1"
        off = f"trunc({h}*(1-{p}))"
        if edge == "top":        # enters from the top edge, moving down
            return f"pad=w={w}:h={2 * h}:x=0:y=0:color=black,crop=w={w}:h={h}:x=0:y='{off}':exact=1"
        return f"pad=w={w}:h={2 * h}:x=0:y={h}:color=black,crop=w={w}:h={h}:x=0:y='{h}-{off}':exact=1"
    if effect == "zoom":
        # moviepy's zoom: the frame resized to (int(w·s), int(h·s)) with
        # lanczos, s from 1.3 to 1.0, the centre cut out, faded up from
        # black. ``crop`` is told the size it cuts from by the same
        # expression: its own ``iw`` keeps the first frame's width when
        # ``scale`` changes size frame by frame.
        s = f"(1+{ZOOM_FROM - 1:g}*(1-{p}))"
        zw, zh = f"trunc({w}*{s})", f"trunc({h}*{s})"
        return (f"scale=w='{zw}':h='{zh}':eval=frame:flags=lanczos,"
                f"crop=w={w}:h={h}:x='floor(({zw}-{w})/2)':y='floor(({zh}-{h})/2)':exact=1,"
                f"fade=t=in:st={shift:.6f}:d={fd:.6f}")
    raise ValueError(f"Unknown transition effect {effect!r}")


def _segment_parts(seg: DeckSegment, frames: int, lead: float, fps: float) -> List[Tuple[int, int, str]]:
    """A still slide's frames ``[start, end)`` split by what they show: the
    opening transition ("in"), the slide as it is (""), the closing one
    ("out"). Frame j is at ``(j + lead) / fps`` of the slide's own time, and
    a frame belongs to a transition exactly when moviepy would have drawn the
    transition at that moment (``t < fade_dur``, ``t >= duration - fade_dur``)."""
    in_end = 0
    if seg.effect_in:
        in_end = min(frames, max(0, math.ceil(seg.effect_seconds * fps - lead - 1e-6)))
    out_start = frames
    if seg.effect_out:
        out_start = min(frames, max(in_end, math.ceil((seg.seconds - seg.effect_seconds) * fps - lead - 1e-6)))
    parts = []
    if in_end:
        parts.append((0, in_end, "in"))
    if out_start > in_end:
        parts.append((in_end, out_start, ""))
    if frames > out_start:
        parts.append((out_start, frames, "out"))
    return parts


def chunk_limit(size: Tuple[int, int]) -> int:
    """At most this many segments in one ffmpeg run of the render."""
    return max(MIN_CHUNK_SEGMENTS, CHUNK_PIXELS // max(1, size[0] * size[1]))


def deck_chunks(bounds: List[int], limit: int, min_frames: Optional[int] = None) -> List[Tuple[int, int]]:
    """The segments ``[first, last)`` of each chunk: ``limit`` at a time,
    longer while a chunk is under ``min_frames`` frames (default
    :data:`MIN_CHUNK_FRAMES`), and a short tail folded into the chunk before
    it - so no chunk but a whole deck is shorter than ``min_frames``, and none
    holds more than ``limit`` segments plus the few frames' worth it takes to
    reach that length."""
    if min_frames is None:
        min_frames = MIN_CHUNK_FRAMES
    count = len(bounds) - 1
    chunks, first = [], 0
    for idx in range(count):
        if idx + 1 - first >= limit and bounds[idx + 1] - bounds[first] >= min_frames:
            chunks.append((first, idx + 1))
            first = idx + 1
    if first < count:
        if chunks and bounds[count] - bounds[first] < min_frames:
            chunks[-1] = (chunks[-1][0], count)
        else:
            chunks.append((first, count))
    return chunks


def deck_chunk_graph(
    segments: List[DeckSegment], bounds: List[int], starts: List[float], first: int, last: int,
    fps: float, size: Tuple[int, int], watermark: Optional[Tuple[str, int, int]] = None,
) -> Tuple[List[str], str, int]:
    """The ffmpeg inputs (argv), the filter graph (output ``[v]``) and the
    number of inputs for ``segments[first:last]``: every segment exactly
    ``bounds[k+1] - bounds[k]`` frames, the chunk ``bounds[last] -
    bounds[first]``. Inputs are the stills' and clips' names in the scratch
    folder ffmpeg runs in; ``watermark`` is (name, x, y)."""
    w, h = size
    inputs: List[str] = []
    chains: List[str] = []
    labels: List[str] = []
    count = 0
    # Every transition chain's clock is the slide's own time plus this, so it
    # never starts below zero (ffmpeg's fade never fades from a negative one).
    shift = transition_clock_shift(fps)
    for idx in range(first, last):
        seg = segments[idx]
        frames = bounds[idx + 1] - bounds[idx]
        if frames <= 0:
            continue
        # Frame j of the segment is at (j + lead) / fps of the segment's own
        # time: the frame grid against the slide's exact start, within half a
        # frame - so ``lead`` is NEGATIVE whenever the start rounds down.
        lead = bounds[idx] - starts[idx] * fps
        label = f"s{idx}"
        if seg.video:
            # An animated slide: looped for as long as its span when it is
            # shorter, cut when it is longer, sampled at the render's rate.
            inputs += ["-stream_loop", "-1", "-i", str(seg.video)]
            chain = f"[{count}:v]{UNTAGGED},fps={fps},scale={w}:{h}:flags=lanczos,setsar=1,trim=end_frame={frames}"
            count += 1
            effects = [_transition_filter(effect, opening, seg, size, shift)
                       for effect, opening in ((seg.effect_in, True), (seg.effect_out, False)) if effect]
            if effects:
                chain += (f",format=rgb24,settb=AVTB,setpts=(N+{lead:.6f})/{fps}/TB+{shift:g}/TB,"
                          + ",".join(effects))
            chains.append(chain + f",format=yuv420p,setpts=PTS-STARTPTS[{label}]")
            labels.append(f"[{label}]")
        elif seg.image:
            inputs += ["-i", str(seg.image)]
            parts = _segment_parts(seg, frames, lead, fps)
            names = [f"[{label}p{part}]" for part in range(len(parts))]
            chains.append(f"[{count}:v]{UNTAGGED},scale={w}:{h}:flags=lanczos,format=rgb24,setsar=1"
                          + (f",split={len(parts)}" if len(parts) > 1 else "") + "".join(names))
            count += 1
            for part, (start, end, where) in enumerate(parts):
                out = f"[{label}x{part}]"
                if where:
                    effect = seg.effect_in if where == "in" else seg.effect_out
                    chains.append(
                        f"{names[part]}loop=loop={end - start - 1}:size=1:start=0,settb=AVTB,"
                        f"setpts=(N+{start + lead:.6f})/{fps}/TB+{shift:g}/TB,"
                        f"{_transition_filter(effect, where == 'in', seg, size, shift)},"
                        f"format=yuv420p,setpts=PTS-STARTPTS{out}"
                    )
                else:
                    chains.append(f"{names[part]}format=yuv420p,loop=loop={end - start - 1}:size=1:start=0{out}")
                labels.append(out)
        else:
            chains.append(f"color=c={seg.color}:s={w}x{h}:r={fps},setsar=1,format=yuv420p,"
                          f"trim=end_frame={frames}[{label}]")
            labels.append(f"[{label}]")
    # Every frame renumbered on one grid: the segments' own timestamps only
    # place their transitions.
    tail = "".join(labels) + f"concat=n={len(labels)}:v=1:a=0,settb=1/{fps},setpts=N"
    if watermark:
        name, x, y = watermark
        inputs += ["-i", name]
        chains.append(f"[{count}:v]{UNTAGGED}[wm]")
        count += 1
        chains.append(tail + "[cat]")
        chains.append(f"[cat][wm]overlay=x={x}:y={y},format=yuv420p,{UNTAGGED}[v]")
    else:
        chains.append(tail + f",format=yuv420p,{UNTAGGED}[v]")
    return inputs, ";\n".join(chains), count


def deck_audio_graph(
    voice: Optional[int], music: List[int], seconds: float, volume: float, fade: float, loop_playlist: bool,
) -> str:
    """The deck's sound as one ffmpeg graph, output ``[a]``: the master
    narration track (input ``voice``) and the background music playlist
    (inputs ``music``, in order), ``seconds`` long.

    The narration is up-mixed at UNITY (:data:`UPMIX_STEREO`, the re-voice
    mux's own filter - never the -3 dB rematrix moviepy's reader applied),
    resampled to 48 kHz and padded to the picture's length with a BOUNDED
    ``apad`` (an unbounded one never finishes on the bundled 7.1). The music
    is what moviepy's ``_apply_background_music`` made it: the tracks one
    after another, looped as a whole to the video (``loop_playlist``, or the
    single track's own ``-stream_loop``), at ``volume`` (a linear factor),
    one fade in at the start and one out at the end when the video is longer
    than both, never ducked, and summed under the voice with
    ``normalize=0`` so the voice keeps its level."""
    chains = []
    if voice is not None:
        chains.append(f"[{voice}:a]{UPMIX_STEREO},{RESAMPLE_48K},"
                      f"apad=whole_dur={seconds:.6f},atrim=end={seconds:.6f}[voice]")
    if music:
        for i, k in enumerate(music):
            chains.append(f"[{k}:a]{UPMIX_STEREO},{RESAMPLE_48K}[mu{i}]")
        steps = []
        if len(music) > 1:
            chains.append("".join(f"[mu{i}]" for i in range(len(music))) + f"concat=n={len(music)}:v=0:a=1[playlist]")
            source = "[playlist]"
            if loop_playlist:
                # The whole playlist again from its first track: held in
                # memory once, as 16-bit samples (only when it is shorter
                # than the video, so never more than the video's length).
                steps += ["aformat=sample_fmts=s16", "aloop=loop=-1:size=2147483647"]
        else:
            source = "[mu0]"
        steps += [f"atrim=end={seconds:.6f}", f"volume={volume:.6f}"]
        if fade > 0 and seconds > 2 * fade:
            steps += [f"afade=t=in:st=0:d={fade:.6f}", f"afade=t=out:st={seconds - fade:.6f}:d={fade:.6f}"]
        chains.append(source + ",".join(steps) + "[bed]")
    if voice is not None and music:
        chains.append("[voice][bed]amix=inputs=2:duration=first:normalize=0[a]")
    elif voice is not None:
        chains[-1] = chains[-1][: -len("[voice]")] + "[a]"
    elif music:
        chains[-1] = chains[-1][: -len("[bed]")] + "[a]"
    else:
        raise ValueError("A deck's sound needs a narration track or music.")
    return ";\n".join(chains)


def _progress_frame(path: Path) -> Optional[int]:
    """The last ``frame=`` ffmpeg's ``-progress`` wrote to ``path``, or None."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 4096))
            found = _PROGRESS_FRAME.findall(fh.read())
    except OSError:
        return None
    return int(found[-1]) if found else None


def _has_video_stream(path) -> bool:
    """Whether ffmpeg finds a video stream in ``path`` (its own header)."""
    return bool(_VIDEO_STREAM.search(_ffmpeg_header(path)))


def _link_or_copy(src: Path, dst: Path) -> None:
    """``dst`` as a hard link to ``src``, or a copy where a link cannot be made."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def _compose(background: Image.Image, layer: Image.Image, pos: Tuple[int, int]) -> Image.Image:
    """``layer`` (RGBA) over ``background`` (RGBA) at ``pos`` as moviepy 2.1.2
    composited a clip with a mask (``VideoClip.compose_on``): the alpha as its
    mask maths leaves it (``(a / 255) * 255`` truncated), pasted onto a clear
    canvas and alpha-composited by Pillow."""
    rgba = np.array(layer.convert("RGBA"))
    rgba[:, :, 3] = (1.0 * rgba[:, :, 3] / 255 * 255).astype("uint8")
    canvas = Image.new("RGBA", background.size, (0, 0, 0, 0))
    canvas.paste(Image.fromarray(rgba), pos)
    return Image.alpha_composite(background, canvas)


def _place(axis: str, outer: int, inner: int) -> int:
    """moviepy's named position on one axis, truncated as it truncated it."""
    return int({"left": 0, "top": 0, "center": (outer - inner) / 2, "right": outer - inner,
                "bottom": outer - inner}[axis])


class VideoCreator:
    """Creates MP4 videos from slides and audio."""

    def __init__(
        self,
        resolution: Tuple[int, int] = (1920, 1080),
        video_bitrate: str = "",
        x264_preset: str = "medium",
        h264_profile: str = "high",
        audio_bitrate: str = "",
        fps: int = STATIC_FPS,
        transition_pause: float = 1.0,
        transition_sound_path: Optional[Path] = None,
        background_music_paths: Optional[List[Path]] = None,
        music_volume: float = 0.25,
        music_fade_duration: float = 2.0,
        watermark_text: str = "",
        watermark_image: Optional[Path] = None,
        watermark_position: str = "bottom-right",
        watermark_opacity: float = 0.5,
        slide_transition: str = "none",
        transition_duration: float = 0.5,
        intro_text: str = "",
        intro_subtitle: str = "",
        intro_duration: float = 3.0,
        outro_text: str = "",
        outro_duration: float = 3.0,
        voice_start_delay: float = 1.0,
        onset_profile: Optional[OnsetProfile] = None,
    ):
        self.resolution = resolution
        self.video_bitrate = video_bitrate
        # The rest of the output preset's encode (Q1), threaded here the way
        # the video bitrate is: the x264 preset (the preview passes
        # ``ultrafast``), the H.264 profile, and the AAC bitrate ("" = the
        # encoder's default, about 128k - the route always passes the
        # preset's). See ``encode_settings``.
        self.x264_preset = x264_preset
        self.h264_profile = h264_profile
        self.audio_bitrate = audio_bitrate
        self.fps = fps
        self.transition_pause = transition_pause
        self.voice_start_delay = voice_start_delay
        # The onset profile of the provider that narrated this run's clips.
        # None = the studio's configured provider at assembly time (legacy
        # callers); the pipeline passes its own so a Kokoro job on an
        # Edge-default studio is trimmed and boosted as Kokoro audio.
        self.onset_profile = onset_profile
        self.transition_sound_path = transition_sound_path
        self.background_music_paths = [p for p in (background_music_paths or []) if p and p.exists()]
        self.music_volume = music_volume
        self.music_fade_duration = music_fade_duration
        self.watermark_text = watermark_text
        self.watermark_image = watermark_image
        self.watermark_position = watermark_position
        self.watermark_opacity = watermark_opacity
        self.slide_transition = slide_transition
        self.transition_duration = transition_duration
        self.intro_text = intro_text
        self.intro_subtitle = intro_subtitle
        self.intro_duration = intro_duration
        self.outro_text = outro_text
        self.outro_duration = outro_duration
        # The intro card has no narration: everything timed against the slides
        # (master track, chapters, the caller's subtitles) starts this much later.
        self.intro_offset = float(intro_duration) if intro_text else 0.0
        # The font FILE the title cards and the text watermark are drawn with,
        # resolved once per render (Pillow refuses a family name such as
        # "Arial"): the configured title_font, else the host's own. None = no
        # font file on this host; the text is then drawn with Pillow's built-in
        # font rather than dropped.
        self.font = resolve_font(_configured_title_font())
        if self.font:
            logger.info("Text font: %s", self.font)
        else:
            logger.warning("No font file found on this host - title cards and the watermark use Pillow's built-in font")
        # At most this many segments go through one ffmpeg run (see "the deck
        # render" above); the measurements set it, the tests move it.
        self.chunk_limit = chunk_limit(resolution)

    # ── the timeline ──────────────────────────────────────────────────────

    def _effects(self, index: int, count: int, seconds: float) -> Tuple[str, str, float, str]:
        """The transition slide ``index`` of ``count`` carries: (in, out,
        seconds, colour). None on the first slide's opening or the last's
        close, and none at all for "none" or a zero duration - the gate
        ``fps_for_transition`` uses. ``fade_dur`` is ``min(duration, slide /
        3)``, as it always was."""
        kind = self.slide_transition
        if not kind or kind == "none" or self.transition_duration <= 0:
            return "", "", 0.0, "black"
        fade = min(float(self.transition_duration), seconds / 3)
        if fade <= 0:
            return "", "", 0.0, "black"
        first, last = index == 0, index == count - 1
        if kind in FADE_COLOURS:
            return ("" if first else "fade"), ("" if last else "fade"), fade, FADE_COLOURS[kind]
        if kind in SLIDE_FROM:
            return ("" if first else f"slide-from-{SLIDE_FROM[kind]}"), "", fade, "black"
        if kind == "zoom-in":
            return ("" if first else "zoom"), "", fade, "black"
        return "", "", 0.0, "black"

    def deck_segments(
        self, slide_clips: List[SlideClipInfo], slide_durations: List[float], gaps: Optional[List[float]] = None,
    ) -> List[DeckSegment]:
        """The picture's timeline, every length taken from the master track:
        the intro card (the master's leading silence, in whole milliseconds),
        each slide for its span on the track (``slide_durations``; a slide's
        own ``duration``, or 5 s, when there is no track), the gap the track
        left after it (``gaps``, as ``_build_master_audio`` reports them; the
        pause when there is no track), and the outro card. A pause is white
        for "fade-to-white" and black otherwise."""
        segments: List[DeckSegment] = []
        if self.intro_text:
            intro_ms = int(round(max(0.0, self.intro_offset) * 1000))
            segments.append(DeckSegment(intro_ms / 1000.0, kind="card", card=(self.intro_text, self.intro_subtitle)))
        count = len(slide_clips)
        followed = gaps is not None and len(gaps) == count
        pause_colour = "white" if self.slide_transition == "fade-to-white" else "black"
        for i, info in enumerate(slide_clips):
            if slide_durations and i < len(slide_durations):
                seconds = float(slide_durations[i])
            else:
                seconds = float(info.duration or 5.0)
            effect_in, effect_out, fade, colour = self._effects(i, count, seconds)
            segments.append(DeckSegment(
                seconds, kind="slide", image=info.image_path, video=info.video_path,
                effect_in=effect_in, effect_out=effect_out, effect_seconds=fade, effect_color=colour,
            ))
            if i < count - 1:
                if followed:
                    gap = float(gaps[i])
                else:
                    gap = int(pause_after(info, self.transition_pause) * 1000) / 1000.0
                if gap > 0:
                    segments.append(DeckSegment(gap, kind="pause", color=pause_colour))
        if self.outro_text:
            segments.append(DeckSegment(float(self.outro_duration), kind="card", card=(self.outro_text, "")))
        return segments

    # ── the stills ────────────────────────────────────────────────────────

    def _title_card(self, text: str, subtitle: str = "") -> Image.Image:
        """A title card as a still: black, the title 48 px white, centred - at
        40 % of the height with a subtitle under it at 55 %, 28 px ``#cccccc``
        - wrapped to the width less 200 px, composited as moviepy composited
        it. Drawn by Pillow (``core.fonts.text_image``), with the resolved
        font or Pillow's built-in one; a card whose text cannot be drawn is
        left blank rather than failing the render."""
        w, h = self.resolution
        card = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        max_width = w - 200
        try:
            title = text_image(text, 48, "white", font=self.font, max_width=max_width)
            y = int(h * 0.4) if subtitle else _place("center", h, title.height)
            card = _compose(card, title, (_place("center", w, title.width), y))
            if subtitle:
                sub = text_image(subtitle, 28, "#cccccc", font=self.font, max_width=max_width)
                card = _compose(card, sub, (_place("center", w, sub.width), int(h * 0.55)))
        except Exception as e:
            logger.warning("The title card text could not be drawn (%s); the card is blank", e)
        return card.convert("RGB")

    def _watermark(self) -> Optional[Tuple[Image.Image, Tuple[int, int]]]:
        """The watermark as a still and the corner it sits at, or None.

        Text (which wins) is 24 px white; an image is scaled to 10 % of the
        video's width, its colour and its alpha resized apart as moviepy
        resized a clip and its mask. Either way the alpha is multiplied by the
        opacity as moviepy's mask maths did, and the corner is one of the five
        positions moviepy placed it at, flush with the frame's edges."""
        w, h = self.resolution
        try:
            if self.watermark_text:
                drawn = text_image(self.watermark_text, 24, "white", font=self.font)
                rgb = drawn.convert("RGB")
                alpha = np.asarray(drawn.getchannel("A"), dtype=np.float64)
            elif self.watermark_image and Path(self.watermark_image).exists():
                with Image.open(self.watermark_image) as source:
                    rgba = source.convert("RGBA")
                width = int(w * 0.10)
                height = max(1, int(rgba.height * width / rgba.width))
                rgb = rgba.convert("RGB").resize((width, height), Image.LANCZOS)
                alpha = np.asarray(rgba.getchannel("A").resize((width, height), Image.LANCZOS), dtype=np.float64)
            else:
                return None
            alpha = (self.watermark_opacity * (alpha / 255) * 255).astype("uint8")
            layer = rgb.convert("RGBA")
            layer.putalpha(Image.fromarray(alpha))
            across, down = WATERMARK_POSITIONS.get(self.watermark_position, ("right", "bottom"))
            return layer, (_place(across, w, layer.width), _place(down, h, layer.height))
        except Exception as e:
            logger.warning("The watermark could not be drawn (%s); the video has none", e)
            return None

    def _still(self, source: Path, scratch: Path, stem: str) -> Optional[str]:
        """``source`` in ``scratch`` as ``stem`` + its suffix - a hard link, or a
        copy - or flattened onto black first when it has transparency, as
        moviepy's composition over black showed it. None when it cannot be
        opened (the slide then shows black)."""
        try:
            with Image.open(source) as image:
                transparent = image.mode in ("RGBA", "LA", "PA") or (
                    image.mode == "P" and "transparency" in image.info)
                if transparent:
                    rgba = image.convert("RGBA")
                    flat = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
                    name = f"{stem}.png"
                    Image.alpha_composite(flat, rgba).convert("RGB").save(scratch / name, compress_level=1)
                    return name
        except Exception as e:
            logger.warning("Slide image %s cannot be read (%s); the slide shows black", source.name, e)
            return None
        name = f"{stem}{source.suffix.lower() or '.png'}"
        _link_or_copy(source, scratch / name)
        return name

    def _prepare_stills(self, segments: List[DeckSegment], scratch: Path, progress_callback=None) -> None:
        """Every segment's picture as a file in ``scratch``: a slide's image or
        clip under a short name (so a deck of any size stays far inside the
        command line's limit), a card drawn, a black still for a slide with no
        picture. An animated slide whose clip ffmpeg cannot read shows its
        image instead."""
        black = None
        slides = sum(1 for seg in segments if seg.kind == "slide")
        done = 0
        for i, seg in enumerate(segments):
            if seg.kind == "card":
                name = f"card{i:04d}.png"
                self._title_card(*seg.card).save(scratch / name, compress_level=1)
                seg.image = name
                continue
            if seg.kind != "slide":
                continue
            done += 1
            if progress_callback:
                progress_callback(done, slides, f"Preparing slide {done}...")
            if seg.video and Path(seg.video).is_file() and _has_video_stream(seg.video):
                source = Path(seg.video)
                name = f"a{i:04d}{source.suffix.lower()}"
                _link_or_copy(source, scratch / name)
                seg.video, seg.image = name, None
                continue
            if seg.video:
                logger.warning("Animated slide %s has no picture ffmpeg can read; showing its image", Path(seg.video).name)
            seg.video = None
            name = None
            if seg.image and Path(seg.image).is_file():
                name = self._still(Path(seg.image), scratch, f"s{i:04d}")
            if name is None:
                if black is None:
                    black = "black.png"
                    Image.new("RGB", self.resolution, (0, 0, 0)).save(scratch / black, compress_level=1)
                name = black
            seg.image = name

    def _embed_chapters(self, video_path: Path, slide_clips: List[SlideClipInfo], slide_titles: List[str]):
        """Embed chapter markers into the MP4 using ffmpeg metadata.

        Computes the deck's spans - one chapter per slide - and hands them to
        :func:`embed_chapters`, which writes the metadata file and remuxes
        the video to embed it. Players like VLC and YouTube recognize these
        chapters.
        """
        try:
            # Each slide's on-screen time: its narration plus the voice start
            # delay (the master track opens every narrated slide with that
            # silence), or the default hold for a silent slide. The narration's
            # length is ffmpeg's own (the header's ``Duration``, which is what
            # moviepy's ``AudioFileClip`` read here before T1).
            durations = []
            titles = []
            for i, clip_info in enumerate(slide_clips):
                title = slide_titles[i] if i < len(slide_titles) else f"Slide {i + 1}"
                if not title or not title.strip():
                    title = f"Slide {i + 1}"
                titles.append(title)

                duration = clip_info.duration or 5.0
                if clip_info.audio_path and clip_info.audio_path.exists():
                    seconds = _probe_duration(clip_info.audio_path)
                    if seconds is not None:
                        duration = seconds + self.voice_start_delay
                durations.append(duration)

            # Shifted past the intro card, which has no chapter of its own; the
            # gap after each slide is its own pause override, else the job's.
            pauses = [pause_after(clip_info, self.transition_pause) for clip_info in slide_clips]
            spans = chapter_spans(durations, self.transition_pause, self.intro_offset, pauses)
            chapters = [(start_ms, end_ms, title) for (start_ms, end_ms), title in zip(spans, titles)]

            if not chapters:
                return

            embed_chapters(video_path, chapters)

        except Exception as e:
            logger.error("Error embedding chapters: %s", e)

    # ── the encode ────────────────────────────────────────────────────────

    def encode_settings(self) -> dict:
        """The encode of this render as ffmpeg arguments - the one place every
        deck render takes them from, so a test can read exactly what the
        render passes (``tests/test_encode_settings.py``).

        ``video``: the render's frame rate, libx264 at the output preset's
        x264 preset and H.264 profile, 4:2:0 (:func:`h264_params`), the
        preset's video bitrate when it has one (none = the codec's
        constant-quality default), x264's own thread count. ``audio``: AAC at
        the preset's bitrate. ``container``: the index at the front
        (:data:`FASTSTART`). The parameters are the same whatever the x264
        preset, so the preview (``ultrafast``) plays everywhere the final
        render does.
        """
        video = ["-r", str(self.fps), "-c:v", "libx264", "-preset", self.x264_preset, *h264_params(self.h264_profile)]
        if self.video_bitrate:
            video += ["-b:v", self.video_bitrate]
        video += ["-threads", "0"]
        audio = ["-c:a", "aac"]
        if self.audio_bitrate:
            audio += ["-b:a", self.audio_bitrate]
        return {"video": video, "audio": audio, "container": list(FASTSTART)}

    def _sound_inputs(self, master: Optional[Path], seconds: float, first_input: int) -> Tuple[List[str], Optional[str]]:
        """The ffmpeg inputs and graph (output ``[a]``) of the deck's sound,
        its inputs numbered from ``first_input``; no graph when there is
        neither narration nor music."""
        inputs: List[str] = []
        voice = None
        if master and Path(master).exists():
            inputs += ["-i", str(master)]
            voice = first_input
        music_inputs: List[int] = []
        tracks = self.background_music_paths
        loop_playlist = False
        if tracks:
            lengths = [_probe_duration(track) for track in tracks]
            # Looped only when the playlist is shorter than the video (or a
            # track cannot be measured): a single track by ffmpeg's own
            # -stream_loop, a playlist as a whole in the graph.
            short = any(length is None for length in lengths) or sum(lengths) < seconds
            for track in tracks:
                if short and len(tracks) == 1:
                    inputs += ["-stream_loop", "-1"]
                inputs += ["-i", str(track)]
                music_inputs.append(first_input + (1 if voice is not None else 0) + len(music_inputs))
            loop_playlist = short and len(tracks) > 1
        if voice is None and not music_inputs:
            return [], None
        graph = deck_audio_graph(voice, music_inputs, seconds, max(0.0, float(self.music_volume)),
                                 float(self.music_fade_duration), loop_playlist)
        return inputs, graph

    def _run_ffmpeg(self, cmd: List[str], scratch: Path, timeout: float, cancel_check, on_frame=None) -> None:
        """One ffmpeg run of the render in ``scratch``, its progress handed
        to ``on_frame``; raises :class:`CancelledError` when it was
        cancelled and ``RuntimeError`` when it timed out or failed."""
        log = scratch / "ffmpeg.log"
        progress = scratch / "progress.txt"
        progress.unlink(missing_ok=True)

        def _cancelled() -> bool:
            return bool(cancel_check and cancel_check())

        def _poll() -> None:
            if on_frame is None:
                return
            frame = _progress_frame(progress)
            if frame is not None:
                try:
                    on_frame(frame)
                except Exception as e:  # noqa: BLE001 - a progress report never stops the render
                    logger.debug("Progress callback failed: %s", e)

        outcome, proc = _run_until_done(cmd, log, timeout, _cancelled, on_poll=_poll, cwd=scratch)
        if outcome == "cancelled":
            raise CancelledError("Cancelled while ffmpeg was rendering")
        if outcome == "timeout":
            raise RuntimeError(f"The render took longer than {timeout:.0f}s and was stopped")
        if proc.returncode != 0:
            tail = log.read_text(encoding="utf-8", errors="replace")[-800:] if log.is_file() else ""
            raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {tail}")
        _poll()

    def _render(
        self, segments: List[DeckSegment], master: Optional[Path], scratch: Path, output: Path,
        watermark: Optional[Tuple[str, int, int]], encoding_callback=None, cancel_check=None,
    ) -> None:
        """The picture and the sound of ``segments`` into ``output``: each
        chunk's run, then the join (see "the deck render" above)."""
        from utils.config import FFMPEG_PATH

        fps = self.fps
        bounds = frame_boundaries([seg.seconds for seg in segments], fps)
        starts, elapsed = [], 0.0
        for seg in segments:
            starts.append(elapsed)
            elapsed += seg.seconds
        total = bounds[-1]
        seconds = total / fps
        chunks = deck_chunks(bounds, self.chunk_limit)
        settings = self.encode_settings()
        logger.info("Rendering %d frames at %d fps (%.1fs) in %d chunk(s) of at most %d segments",
                    total, fps, seconds, len(chunks), self.chunk_limit)
        base = [FFMPEG_PATH, "-hide_banner", "-nostats", "-loglevel", "error", "-y"]

        def _cancelled() -> bool:
            return bool(cancel_check and cancel_check())

        # The sound: one ffmpeg run of its own, started first and running beside
        # the picture's (an AAC encode of a long narration takes seconds, and
        # in the picture's graph it ran on the same thread as the frames).
        sound_inputs, sound_graph = self._sound_inputs(master, seconds, 0)
        sound = sound_log = None
        try:
            if sound_graph:
                (scratch / "sound.txt").write_text(sound_graph, encoding="utf-8")
                sound_log = open(scratch / "sound.log", "w", encoding="utf-8", errors="replace")
                sound = subprocess.Popen(
                    [*base, *sound_inputs, "-/filter_complex", "sound.txt", "-map", "[a]", *settings["audio"],
                     "sound.m4a"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sound_log, cwd=str(scratch),
                )
            names = []
            for c, (first, last) in enumerate(chunks):
                if _cancelled():
                    raise CancelledError("Cancelled between chunks")
                if sound is not None and sound.poll() not in (None, 0):
                    break  # the sound failed: reported below, without encoding the rest
                inputs, graph, _ = deck_chunk_graph(segments, bounds, starts, first, last, fps, self.resolution, watermark)
                name = f"chunk{c:03d}.mp4"
                graph_file = f"graph{c:03d}.txt"
                (scratch / graph_file).write_text(graph, encoding="utf-8")
                frames = bounds[last] - bounds[first]
                done = bounds[first]
                t1 = time.time()
                self._run_ffmpeg(
                    [*base, "-progress", "progress.txt", *inputs, "-/filter_complex", graph_file,
                     "-map", "[v]", *settings["video"], name],
                    scratch, render_timeout(frames, self.resolution), cancel_check,
                    on_frame=(lambda frame, done=done: encoding_callback(min(total, done + frame), total))
                    if encoding_callback else None,
                )
                logger.info("Chunk %d/%d: %d frames in %.1fs", c + 1, len(chunks), frames, time.time() - t1)
                names.append(name)
            if sound is not None:
                outcome = _wait_until_done(sound, 120 + seconds / 2, _cancelled)
                if outcome == "cancelled":
                    raise CancelledError("Cancelled while the sound was encoding")
                if outcome == "timeout" or sound.returncode != 0:
                    sound_log.close()
                    tail = (scratch / "sound.log").read_text(encoding="utf-8", errors="replace")[-800:]
                    raise RuntimeError(f"The sound could not be encoded ({outcome}, exit {sound.returncode}): {tail}")
        finally:
            if sound is not None and sound.poll() is None:
                _stop(sound)
            if sound_log is not None:
                sound_log.close()
        # The join: the chunks back to back by a stream copy, the sound with
        # them, the index at the front.
        (scratch / "chunks.ffconcat").write_text(
            "ffconcat version 1.0\n" + "".join(f"file '{name}'\n" for name in names), encoding="utf-8")
        cmd = [*base, "-f", "concat", "-safe", "0", "-i", "chunks.ffconcat"]
        if sound is not None:
            cmd += ["-i", "sound.m4a", "-map", "0:v:0", "-map", "1:a:0"]
        else:
            cmd += ["-map", "0:v:0"]
        cmd += ["-c", "copy", *settings["container"], str(output)]
        self._run_ffmpeg(cmd, scratch, 60 + seconds / 10, cancel_check)
        if encoding_callback:
            encoding_callback(total, total)

    def create_video(
        self,
        slide_clips: List[SlideClipInfo],
        output_path: Path,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        encoding_callback: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        slide_titles: Optional[List[str]] = None,
    ) -> bool:
        """Render the deck into ``output_path``: the master narration track,
        the stills, then ffmpeg composes and encodes the picture (see "the
        deck render" above) and the chapters are embedded.

        ``encoding_callback(frame, total)`` is told the frames encoded so far
        (from ffmpeg's ``-progress``) while ffmpeg runs; ``cancel_check`` is
        polled every ``CUT_POLL_SECONDS`` and a cancel stops ffmpeg within a
        poll. The video is written to ``<stem>.part.mp4`` and published over
        ``output_path`` only when it is whole, so a cancelled or failed render
        leaves no partial file and whatever was at ``output_path`` untouched.
        True when the video is there.
        """
        from utils import config as config_module
        from utils.config import FFMPEG_PATH
        from utils.helpers import replace_with_retry

        output_path = Path(output_path)
        part = output_path.with_suffix(".part.mp4")
        master_audio_path = None
        scratch = None
        t0 = time.time()
        try:
            total = len(slide_clips)

            # Step 1: one continuous audio track from all slides, and the gap
            # it leaves after each - the picture follows the track exactly.
            if progress_callback:
                progress_callback(0, total, "Building audio track...")
            gaps: list = []
            master_audio_path, slide_durations = _build_master_audio(
                slide_clips,
                voice_start_delay=self.voice_start_delay,
                transition_pause=self.transition_pause,
                transition_sound_path=self.transition_sound_path,
                profile=self.onset_profile,
                intro_offset=self.intro_offset,
                gaps_out=gaps,
            )
            if not slide_clips:
                logger.error("No clips to combine")
                return False
            if not FFMPEG_PATH:
                logger.error("Cannot render the video: ffmpeg is not available")
                return False
            if cancel_check and cancel_check():
                raise CancelledError("Cancelled after the audio track")

            # Step 2: the stills.
            segments = self.deck_segments(slide_clips, slide_durations, gaps)
            config_module.TEMP_DIR.mkdir(parents=True, exist_ok=True)
            scratch = Path(tempfile.mkdtemp(prefix="deck-", dir=str(config_module.TEMP_DIR)))
            self._prepare_stills(segments, scratch, progress_callback)
            watermark = None
            drawn = self._watermark()
            if drawn is not None:
                # On exactly the pixel it was placed at: overlay snaps an odd
                # corner down to an even one in 4:2:0 (see ``even_origin``).
                layer, x, y = even_origin(drawn[0], *drawn[1])
                layer.save(scratch / "watermark.png")
                watermark = ("watermark.png", x, y)
            if cancel_check and cancel_check():
                raise CancelledError("Cancelled before the encode")

            # Step 3: ffmpeg composes and encodes.
            if progress_callback:
                progress_callback(total, total, "Writing video file...")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Writing video to %s...", output_path)
            t1 = time.time()
            self._render(segments, master_audio_path, scratch, part, watermark, encoding_callback, cancel_check)
            if cancel_check and cancel_check():
                raise CancelledError("Cancelled after the encode")
            replace_with_retry(part, output_path)
            logger.info("Video written in %.1fs", time.time() - t1)

            # Embed chapter markers if slide titles are available
            if slide_titles:
                self._embed_chapters(output_path, slide_clips, slide_titles)

            if progress_callback:
                progress_callback(total, total, "Complete!")

            logger.info("Total create_video time: %.1fs", time.time() - t0)
            return True

        except CancelledError:
            logger.warning("Encoding cancelled by user; ffmpeg stopped and the partial video discarded")
            return False

        except Exception as e:
            logger.exception("Error creating video: %s", e)
            return False

        finally:
            # Clean up master audio temp file
            if master_audio_path and master_audio_path.exists():
                try:
                    master_audio_path.unlink()
                except OSError:
                    pass
            # No-op after a publish; the partial file on every other exit.
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
