"""Video creation module for combining slides and audio into MP4.

Assembles individual slide images (or video clips for animated slides)
with their corresponding audio narration into a single MP4 video.
Supports configurable resolution, transition pauses between slides,
and optional transition sound effects.
"""

import time
import subprocess
from pathlib import Path
from typing import List, Optional, Callable, Tuple
from dataclasses import dataclass

import proglog
import numpy as np
from moviepy import (
    ImageClip, AudioFileClip, VideoFileClip,
    concatenate_videoclips, ColorClip, CompositeAudioClip,
    CompositeVideoClip,
)

from core.fonts import resolve_font, text_image
from core.tts_provider import OnsetProfile
from utils.logger import get_logger
logger = get_logger("VIDEO")

# A static deck is encoded at 2 fps (every frame is the same slide, so the
# encode stays fast). A visual transition needs real frames to play on: at 2 fps
# a 0.5 s fade is a single frame, so a deck with a transition renders at 24 fps.
STATIC_FPS = 2
TRANSITION_FPS = 24


def fps_for_transition(slide_transition: str, transition_duration: float = 0.5) -> int:
    """The frame rate a render needs: ``TRANSITION_FPS`` when a transition will
    actually render (the same gate as ``create_video``), else ``STATIC_FPS``."""
    if slide_transition and slide_transition != "none" and transition_duration > 0:
        return TRANSITION_FPS
    return STATIC_FPS


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


class _EncodingProgressLogger(proglog.ProgressBarLogger):
    """Custom proglog logger that forwards encoding progress to a callback.

    Reports frame-level progress during write_videofile so the UI
    can show a meaningful percentage instead of a static message.
    Also supports cancellation via a callable check.
    """

    def __init__(
        self,
        callback: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ):
        super().__init__()
        self._callback = callback
        self._cancel_check = cancel_check
        self._total_frames = 0

    def bars_callback(self, bar, attr, value, old_value=None):
        if bar == "frame_index" and attr == "total":
            self._total_frames = value
        if bar == "frame_index" and attr == "index":
            if self._callback:
                self._callback(value, self._total_frames)
            # Check cancellation every frame
            if self._cancel_check and self._cancel_check():
                raise CancelledError("Video encoding cancelled")


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


def _probe_duration(path: Path) -> Optional[float]:
    """Return a media file's duration in seconds via ffprobe, or None on failure."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def _build_replace_audio_cmd(source_video, audio, temp_output, video_duration):
    """ffmpeg args to put ``audio`` onto ``source_video`` keeping the full video.

    The new narration is usually shorter than the original (Whisper only makes
    segments for speech, so a music/transition outro after the last words is
    not covered). ``apad`` pads the audio with trailing silence to the video's
    duration so ``-shortest`` trims to the *video* length — the whole original
    video, including its closing transition, is kept with a silent tail rather
    than being cut to the shorter audio. When the duration is unknown, ``apad``
    pads to infinity and ``-shortest`` still bounds the output to the video.
    """
    pad = f"apad=whole_dur={video_duration:.3f}" if video_duration else "apad"
    return [
        "ffmpeg",
        "-i", str(source_video),       # original video
        "-i", str(audio),               # new audio
        "-c:v", "copy",                 # keep video codec (no re-encode)
        "-map", "0:v:0",               # video from first input
        "-map", "1:a:0",               # audio from second input
        "-af", pad,                     # pad narration with trailing silence
        "-c:a", "aac",                  # re-encode padded audio for MP4
        "-shortest",                    # bound to the (now longer-or-equal) video
        "-y",                           # overwrite
        str(temp_output),
    ]


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
        video_duration: The picture's length in seconds when the caller
            already knows it, in which case nothing is probed. None probes
            the source with ffprobe exactly as before (and pads to infinity
            when there is no ffprobe to ask). Present and tested, but UNWIRED
            in E1: an edited output's length is the sum of its kept ranges,
            known before ffmpeg runs, yet the only caller of this function
            is ``services.processing.VideoProcessor._revoice_video``, which
            the edit deliberately left untouched - so an edited run still
            takes the None path. It is unobservable either way: the mux is
            ``-shortest``, and a plain ``apad`` and ``apad=whole_dur=<the
            edit's length>`` give identical output whenever the narration
            outruns the picture (measured on the 336 s edited corpus render).

    Returns:
        True if successful.
    """
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
            mixed_path = master_audio.parent / "mixed_audio.mp3"
            mixed.export(str(mixed_path), format="mp3")
            audio_to_use = mixed_path
        else:
            audio_to_use = master_audio

        # Keep the video stream, swap the audio, and pad the audio to the full
        # video length so the original ending (its closing transition) is kept.
        if video_duration is None:
            video_duration = _probe_duration(source_video)
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
    end). Measured rate about 2.8 s per minute of 1080p30, with headroom. Any
    future filtergraph cut inherits this rule."""
    return 60 + 3 * (float(keep[-1][1]) - float(keep[0][0]))


def _stop(proc) -> None:
    """Kill an ffmpeg that must not finish and reap it. Never raises: the
    caller is already on a failure path, and a process that is gone by the
    time this runs is exactly the outcome wanted."""
    try:
        proc.kill()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001 - see the docstring
        pass


def _run_until_done(cmd, log: Path, timeout: float, cancelled) -> tuple[str, "subprocess.Popen"]:
    """Run one ffmpeg command with its stderr in ``log`` (a file, never a
    pipe nobody reads: a chatty run would fill it and stall), polling it
    every ``CUT_POLL_SECONDS`` until it exits, ``cancelled()`` answers True
    or ``timeout`` seconds pass - and killing it on either of the last two.
    Returns the outcome (``"finished"``, ``"cancelled"``, ``"timeout"``) and
    the process, whose ``returncode`` the caller reads when it finished. The
    one loop the picture cut and the music mix share."""
    with open(log, "w", encoding="utf-8", errors="replace") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err)
        deadline = _clock() + timeout
        outcome = "finished"
        while proc.poll() is None:
            if cancelled():
                outcome = "cancelled"
                break
            if _clock() >= deadline:
                outcome = "timeout"
                break
            time.sleep(CUT_POLL_SECONDS)
        if outcome != "finished":
            _stop(proc)
    return outcome, proc


def cut_picture(
    source, keep, dst, video_bitrate: str = "",
    cancel_check: Optional[Callable[[], bool]] = None,
) -> bool:
    """Cut ``source``'s PICTURE to the kept ranges, writing a video-only MP4
    at ``dst``. True when it is there; False (and a log line) on any failure,
    never an exception - the same contract as ``replace_video_audio``.

    One ffmpeg run, re-encoding everything at ``libx264 -preset ultrafast``
    (the generate path's own settings; ``video_bitrate`` is the output
    preset's, "" for the codec default, as ``write_videofile`` takes it). A
    stream copy is not an option: a cut lands on P-frames with no reference
    picture, and the source's keyframes are two seconds apart, so snapping to
    one can miss by more than a sentence.

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
    cmd = [
        FFMPEG_PATH, "-hide_banner", "-nostats",
        "-ss", f"{origin:.3f}", "-i", str(source),
        "-filter_complex", cut_filtergraph(keep, origin),
        "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "ultrafast",
    ]
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
# 5.1 clip keeps FL, FR and FC and loses LFE, BL and BR, and its FC arrives
# at unity where a standard down-mix would take it to 0.7071 (measured).
# Mono and stereo are what the app makes and what music beds are, and they
# are what the audition must agree with; a surround upload is the corner that
# pays for it, and the library records its ``channels`` at upload, so this is
# answerable later without a probe. See ``music_filtergraph``.
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
    up-mix to stereo (:data:`UPMIX_STEREO`), the level, a linear fade in and
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
    chains.append(f"[0:a]{UPMIX_STEREO}[v]")
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
    be the cut's 16 s again for nothing) and the audio re-encoded once as
    192 kbit/s AAC, ``+faststart`` for the page's player. ``cancel_check``
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
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
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
        if i < len(slide_clips) - 1:
            if trans_sound and pause_ms > 0:
                # Overlay transition sound on the pause
                gap = AudioSegment.silent(duration=max(pause_ms, len(trans_sound)))
                gap = gap.overlay(trans_sound)
                master += gap[:max(pause_ms, len(trans_sound))]
            elif pause_ms > 0:
                master += pause_silence

    if not has_any_audio:
        return None, []

    # Export master track to temp file
    import tempfile
    master_path = Path(tempfile.mktemp(suffix=".mp3"))
    master.export(str(master_path), format="mp3", bitrate="192k")
    logger.info("Built master audio: %.1fs, %d slides", len(master) / 1000.0, len(slide_info))
    return master_path, slide_info


def _open_audio_with_retry(
    path: str, retries: int = 4, delay: float = 0.3,
    trim_silence: bool = True,
    profile: Optional[OnsetProfile] = None,
) -> AudioFileClip:
    """Open an AudioFileClip with retries for antivirus file-lock delays.

    Args:
        trim_silence: If True, trim leading silence from TTS audio before loading.
                      This prevents the fade-in artifact from TTS engines.
        profile: The onset profile of the provider that made the clip; None
                 falls back to the studio's configured provider.
    """
    if trim_silence:
        path = _trim_leading_silence(path, profile=profile or _active_onset_profile())

    last_exc: Exception = RuntimeError("Unknown error")
    for attempt in range(retries):
        try:
            t0 = time.time()
            clip = AudioFileClip(path)
            logger.debug("Opened %s in %.2fs (attempt %d, duration=%.1fs)",
                        Path(path).name, time.time() - t0, attempt+1, clip.duration)
            return clip
        except Exception as exc:
            last_exc = exc
            logger.debug("AudioFileClip attempt %d/%d failed for %s: %s", attempt+1, retries, path, exc)
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    raise last_exc


@dataclass
class SlideClipInfo:
    """Information for creating a slide clip."""
    slide_index: int
    image_path: Optional[Path] = None
    video_path: Optional[Path] = None  # For animated slides
    audio_path: Optional[Path] = None
    duration: Optional[float] = None  # If None, uses audio duration
    pause_after: Optional[float] = None  # Seconds of pause after this slide; None = the creator's transition_pause


class VideoCreator:
    """Creates MP4 videos from slides and audio."""

    def __init__(
        self,
        resolution: Tuple[int, int] = (1920, 1080),
        video_bitrate: str = "",
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
        # resolved once per render (moviepy/Pillow refuse a family name such as
        # "Arial"): the configured title_font, else the host's own. None = no
        # font file on this host; the text is then drawn with Pillow's built-in
        # font rather than dropped.
        self.font = resolve_font(_configured_title_font())
        if self.font:
            logger.info("Text font: %s", self.font)
        else:
            logger.warning("No font file found on this host - title cards and the watermark use Pillow's built-in font")
        # Cache the transition audio so it's decoded once, not per slide gap
        self._transition_audio: Optional[AudioFileClip] = None
        if transition_sound_path and transition_sound_path.exists():
            self._transition_audio = AudioFileClip(str(transition_sound_path))

    def create_slide_clip(
        self,
        clip_info: SlideClipInfo,
        default_duration: float = 5.0
    ) -> Optional[any]:
        """Create a video clip for a single slide."""
        audio_clip = None
        visual_clip = None
        try:
            duration = clip_info.duration or default_duration

            # If we have audio, use its duration
            if clip_info.audio_path and clip_info.audio_path.exists():
                audio_clip = _open_audio_with_retry(str(clip_info.audio_path), profile=self.onset_profile)
                duration = audio_clip.duration

            # Create visual clip
            if clip_info.video_path and clip_info.video_path.exists():
                visual_clip = VideoFileClip(str(clip_info.video_path))
                visual_clip = visual_clip.resized(self.resolution)
                if audio_clip and visual_clip.duration != duration:
                    if visual_clip.duration < duration:
                        visual_clip = visual_clip.looped(duration=duration)
                    else:
                        visual_clip = visual_clip.subclipped(0, duration)
            elif clip_info.image_path and clip_info.image_path.exists():
                visual_clip = ImageClip(str(clip_info.image_path), duration=duration)
                visual_clip = visual_clip.resized(self.resolution)
            else:
                visual_clip = ColorClip(
                    size=self.resolution, color=(0, 0, 0), duration=duration
                )

            # Animated slides keep their native fps; static slides use low fps
            if clip_info.video_path and clip_info.video_path.exists():
                pass  # keep VideoFileClip's native fps
            else:
                visual_clip = visual_clip.with_fps(self.fps)

            if audio_clip:
                # Delay narration start so video plays first
                delay = self.voice_start_delay
                if delay > 0:
                    audio_clip = audio_clip.with_start(delay)
                    duration += delay
                    visual_clip = visual_clip.with_duration(duration)
                    combined_audio = CompositeAudioClip([audio_clip])
                    combined_audio = combined_audio.with_duration(duration)
                    visual_clip = visual_clip.with_audio(combined_audio)
                else:
                    visual_clip = visual_clip.with_audio(audio_clip)

            return visual_clip

        except Exception as e:
            # Close any opened clips to prevent resource leaks
            if audio_clip:
                try:
                    audio_clip.close()
                except Exception:
                    pass
            if visual_clip:
                try:
                    visual_clip.close()
                except Exception:
                    pass
            logger.error("Error creating slide clip: %s", e)
            return None

    def create_transition_clip(self, pause: Optional[float] = None) -> any:
        """Create a transition/pause clip between slides, with optional sound.

        ``pause`` is the gap after the slide just shown (a slide's own
        override); None = the creator's ``transition_pause``.
        """
        pause = self.transition_pause if pause is None else float(pause)
        bg_color = (255, 255, 255) if self.slide_transition == "fade-to-white" else (0, 0, 0)
        if self._transition_audio:
            duration = max(pause, self._transition_audio.duration)
            clip = ColorClip(
                size=self.resolution, color=bg_color, duration=duration,
            ).with_fps(self.fps).with_audio(self._transition_audio)
            return clip

        return ColorClip(
            size=self.resolution, color=bg_color, duration=pause
        ).with_fps(self.fps)

    def _apply_transition_effect(self, clip, slide_index: int, total_slides: int):
        """Apply visual transition effects to a slide clip.

        Supports: fade-to-black, fade-to-white, crossfade, slide-left,
        slide-right, slide-up, slide-down, zoom-in.
        """
        from moviepy.video.fx import FadeIn, FadeOut

        fade_dur = min(self.transition_duration, clip.duration / 3)
        is_first = (slide_index == 0)
        is_last = (slide_index == total_slides - 1)
        w, h = self.resolution

        if self.slide_transition == "fade-to-black":
            effects = []
            if not is_first:
                effects.append(FadeIn(fade_dur))
            if not is_last:
                effects.append(FadeOut(fade_dur))
            if effects:
                clip = clip.with_effects(effects)

        elif self.slide_transition == "fade-to-white":
            # Fade from/to a white frame
            white = ColorClip(size=self.resolution, color=(255, 255, 255))
            layers = []
            if not is_first:
                white_in = white.with_duration(fade_dur).with_fps(self.fps)
                white_in = white_in.with_effects([FadeOut(fade_dur)])
                layers.append(white_in)
            clip_layers = [clip]
            if not is_last:
                white_out = (white.with_duration(fade_dur)
                             .with_fps(self.fps)
                             .with_effects([FadeIn(fade_dur)])
                             .with_start(clip.duration - fade_dur))
                clip_layers.append(white_out)
            if layers:
                clip_layers = layers + clip_layers
            if len(clip_layers) > 1:
                clip = CompositeVideoClip(clip_layers, size=self.resolution).with_duration(clip.duration)
                if clip_layers[0] != clip:
                    clip = clip.with_fps(self.fps)

        elif self.slide_transition == "crossfade":
            # Simple opacity fade in/out — crossfade effect when combined with pause clips
            effects = []
            if not is_first:
                effects.append(FadeIn(fade_dur))
            if not is_last:
                effects.append(FadeOut(fade_dur))
            if effects:
                clip = clip.with_effects(effects)

        elif self.slide_transition in ("slide-left", "slide-right"):
            direction = -1 if self.slide_transition == "slide-left" else 1

            def _slide_in(get_frame, t):
                if t < fade_dur and not is_first:
                    progress = t / fade_dur
                    offset = int(w * (1 - progress) * direction)
                    frame = get_frame(t)
                    canvas = np.zeros_like(frame)
                    if direction == -1:  # slide from right
                        src_start = max(0, -offset)
                        dst_start = max(0, offset)
                        visible = w - abs(offset)
                        if visible > 0:
                            canvas[:, dst_start:dst_start + visible] = frame[:, src_start:src_start + visible]
                    else:  # slide from left
                        src_start = max(0, offset)
                        dst_start = max(0, -offset)
                        visible = w - abs(offset)
                        if visible > 0:
                            canvas[:, dst_start:dst_start + visible] = frame[:, src_start:src_start + visible]
                    return canvas
                return get_frame(t)

            # One transform (with the mask, when there is one): applying it a
            # second time shifted the frame by twice the offset.
            clip = clip.transform(_slide_in, apply_to="mask" if clip.mask else None)

        elif self.slide_transition in ("slide-up", "slide-down"):
            direction = -1 if self.slide_transition == "slide-up" else 1

            def _slide_v(get_frame, t):
                if t < fade_dur and not is_first:
                    progress = t / fade_dur
                    offset = int(h * (1 - progress) * direction)
                    frame = get_frame(t)
                    canvas = np.zeros_like(frame)
                    if direction == -1:  # slide from bottom
                        src_start = max(0, -offset)
                        dst_start = max(0, offset)
                        visible = h - abs(offset)
                        if visible > 0:
                            canvas[dst_start:dst_start + visible, :] = frame[src_start:src_start + visible, :]
                    else:  # slide from top
                        src_start = max(0, offset)
                        dst_start = max(0, -offset)
                        visible = h - abs(offset)
                        if visible > 0:
                            canvas[dst_start:dst_start + visible, :] = frame[src_start:src_start + visible, :]
                    return canvas
                return get_frame(t)

            clip = clip.transform(_slide_v, apply_to="mask" if clip.mask else None)

        elif self.slide_transition == "zoom-in":
            def _zoom(get_frame, t):
                if t < fade_dur and not is_first:
                    progress = t / fade_dur
                    # Zoom from 1.3x down to 1.0x with fade in
                    scale = 1.0 + 0.3 * (1 - progress)
                    alpha = progress
                    frame = get_frame(t)
                    from PIL import Image
                    img = Image.fromarray(frame)
                    new_w, new_h = int(w * scale), int(h * scale)
                    img = img.resize((new_w, new_h), Image.LANCZOS)
                    # Center crop
                    left = (new_w - w) // 2
                    top = (new_h - h) // 2
                    img = img.crop((left, top, left + w, top + h))
                    result = np.array(img).astype(np.float64) * alpha
                    return result.astype(np.uint8)
                return get_frame(t)

            clip = clip.transform(_zoom)

        return clip

    def _apply_background_music(self, video):
        """Mix background music across the entire video.

        Supports multiple tracks: they play in sequence (track 1 then track 2, etc.).
        If the combined playlist is shorter than the video, the whole sequence loops.
        A single fade in at the start and fade out at the end is applied.
        Music plays at a static lower level under the narration (no ducking).
        """
        from moviepy.audio.fx.AudioFadeIn import AudioFadeIn
        from moviepy.audio.fx.AudioFadeOut import AudioFadeOut
        from moviepy.audio.fx.AudioLoop import AudioLoop
        from moviepy import concatenate_audioclips

        try:
            video_duration = video.duration

            # Load all tracks and concatenate into one sequence
            track_clips = []
            for p in self.background_music_paths:
                try:
                    clip = AudioFileClip(str(p))
                    track_clips.append(clip)
                    logger.info("Loaded track: %s (%.1fs)", p.name, clip.duration)
                except Exception as e:
                    logger.error("Failed to load %s: %s", p.name, e)

            if not track_clips:
                return video

            if len(track_clips) == 1:
                music = track_clips[0]
            else:
                music = concatenate_audioclips(track_clips)
                logger.info("Playlist total: %.1fs across %d tracks", music.duration, len(track_clips))

            # Loop the whole playlist to cover the video duration
            if music.duration < video_duration:
                music = music.with_effects([AudioLoop(duration=video_duration)])
            else:
                music = music.subclipped(0, video_duration)

            # Apply volume (linear 0-1 scale as direct multiplier)
            volume_factor = max(0.0, self.music_volume)
            music = music.with_volume_scaled(volume_factor)

            # Single fade in at start, single fade out at end
            fade_s = self.music_fade_duration
            if fade_s > 0 and video_duration > fade_s * 2:
                music = music.with_effects([
                    AudioFadeIn(fade_s),
                    AudioFadeOut(fade_s),
                ])

            # Composite: narration + music
            if video.audio:
                combined = CompositeAudioClip([video.audio, music])
                return video.with_audio(combined)
            else:
                return video.with_audio(music)
        except Exception as e:
            logger.error("Error applying background music: %s", e)
            return video

    def _text_clip(self, text: str, font_size: int, color: str, duration: float, max_width: Optional[int] = None):
        """A transparent clip of ``text`` drawn by Pillow (``core.fonts.text_image``)
        with the resolved font file, or Pillow's built-in font when this host
        has none - text is never dropped silently. Wrapped to ``max_width``
        when given. Not moviepy's TextClip: in moviepy 2.1.2 it allocates an
        image shorter than the text it draws, so every letter loses its bottom
        rows and descenders (g, y, p) are cut flat."""
        image = text_image(text, font_size, color, font=self.font, max_width=max_width)
        return ImageClip(np.array(image), duration=duration)

    def _apply_watermark(self, video):
        """Overlay a text or image watermark on the video."""
        from moviepy import CompositeVideoClip

        try:
            watermark = None
            if self.watermark_text:
                watermark = self._text_clip(
                    self.watermark_text, 24, "white", video.duration,
                ).with_opacity(self.watermark_opacity)
            elif self.watermark_image and Path(self.watermark_image).exists():
                watermark = ImageClip(str(self.watermark_image), duration=video.duration)
                # Scale watermark to ~10% of video width
                wm_width = int(self.resolution[0] * 0.10)
                watermark = watermark.resized(width=wm_width).with_opacity(self.watermark_opacity)

            if watermark is None:
                return video

            # Position mapping
            pos_map = {
                "top-left": ("left", "top"),
                "top-right": ("right", "top"),
                "bottom-left": ("left", "bottom"),
                "bottom-right": ("right", "bottom"),
                "center": ("center", "center"),
            }
            pos = pos_map.get(self.watermark_position, ("right", "bottom"))
            watermark = watermark.with_position(pos)
            return CompositeVideoClip([video, watermark])
        except Exception as e:
            logger.warning("The watermark could not be drawn (%s); the video has none", e)
            return video

    def _create_title_card(self, text: str, subtitle: str = "", duration: float = 3.0):
        """Create a title card clip with centered text on black background."""
        from moviepy import CompositeVideoClip
        bg = ColorClip(size=self.resolution, color=(0, 0, 0), duration=duration).with_fps(self.fps)
        clips = [bg]
        max_width = self.resolution[0] - 200
        try:
            title = self._text_clip(text, 48, "white", duration, max_width=max_width)
            title = title.with_position(("center", "center" if not subtitle else 0.4), relative=subtitle != "")
            clips.append(title)
            if subtitle:
                sub = self._text_clip(subtitle, 28, "#cccccc", duration, max_width=max_width)
                clips.append(sub.with_position(("center", 0.55), relative=True))
        except Exception as e:
            logger.warning("The title card text could not be drawn (%s); the card is blank", e)
        return CompositeVideoClip(clips, size=self.resolution).with_duration(duration).with_fps(self.fps)

    def _embed_chapters(self, video_path: Path, slide_clips: List[SlideClipInfo], slide_titles: List[str]):
        """Embed chapter markers into the MP4 using ffmpeg metadata.

        Creates a metadata file with chapter info and remuxes the video to
        embed it. Players like VLC and YouTube recognize these chapters.
        """
        from utils.config import FFMPEG_PATH
        if not FFMPEG_PATH:
            logger.warning("FFmpeg not found, skipping chapter embedding")
            return

        try:
            # Each slide's on-screen time: its narration plus the voice start
            # delay (the master track opens every narrated slide with that
            # silence), or the default hold for a silent slide.
            durations = []
            titles = []
            for i, clip_info in enumerate(slide_clips):
                title = slide_titles[i] if i < len(slide_titles) else f"Slide {i + 1}"
                if not title or not title.strip():
                    title = f"Slide {i + 1}"
                titles.append(title)

                duration = clip_info.duration or 5.0
                if clip_info.audio_path and clip_info.audio_path.exists():
                    try:
                        clip = AudioFileClip(str(clip_info.audio_path))
                        duration = clip.duration + self.voice_start_delay
                        clip.close()
                    except Exception:
                        pass
                durations.append(duration)

            # Shifted past the intro card, which has no chapter of its own; the
            # gap after each slide is its own pause override, else the job's.
            pauses = [pause_after(clip_info, self.transition_pause) for clip_info in slide_clips]
            spans = chapter_spans(durations, self.transition_pause, self.intro_offset, pauses)
            chapters = [(start_ms, end_ms, title) for (start_ms, end_ms), title in zip(spans, titles)]

            if not chapters:
                return

            # Write FFmpeg metadata file
            meta_path = video_path.with_suffix(".chapters.txt")
            with open(meta_path, "w", encoding="utf-8") as f:
                f.write(";FFMETADATA1\n")
                for start_ms, end_ms, title in chapters:
                    f.write(f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}\nEND={end_ms}\ntitle={title}\n")

            # Remux video with chapter metadata
            temp_output = video_path.with_stem(video_path.stem + "_chaptered")
            cmd = [
                FFMPEG_PATH, "-i", str(video_path), "-i", str(meta_path),
                "-map_metadata", "1", "-codec", "copy",
                "-y", str(temp_output),
            ]
            result = subprocess.run(
                cmd, capture_output=True, timeout=60,
                creationflags=0x08000000 if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            if result.returncode == 0 and temp_output.exists():
                temp_output.replace(video_path)
                logger.info("Embedded %d chapters", len(chapters))
            else:
                logger.warning("ffmpeg failed: %s", result.stderr.decode()[:200])
                if temp_output.exists():
                    temp_output.unlink()

            # Clean up metadata file
            if meta_path.exists():
                meta_path.unlink()

        except Exception as e:
            logger.error("Error embedding chapters: %s", e)

    def create_video(
        self,
        slide_clips: List[SlideClipInfo],
        output_path: Path,
        add_transitions: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
        encoding_callback: Optional[Callable[[int, int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        slide_titles: Optional[List[str]] = None,
    ) -> bool:
        """Create a complete video from slide clips.

        Uses a single continuous audio track built from all slides' audio
        to avoid click artifacts at slide boundaries.
        """
        clips = []
        final_video = None
        master_audio_path = None
        t0 = time.time()
        try:
            total = len(slide_clips)

            # Step 1: Build a single continuous audio track from all slides
            if progress_callback:
                progress_callback(0, total, "Building audio track...")
            master_audio_path, slide_durations = _build_master_audio(
                slide_clips,
                voice_start_delay=self.voice_start_delay,
                transition_pause=self.transition_pause,
                transition_sound_path=self.transition_sound_path,
                profile=self.onset_profile,
                intro_offset=self.intro_offset,
            )

            # Step 2: Build visual-only clips with durations from the master track
            for i, clip_info in enumerate(slide_clips):
                if cancel_check and cancel_check():
                    logger.warning("Cancelled during slide assembly (slide %d/%d)", i + 1, total)
                    raise CancelledError("Cancelled during slide assembly")

                if progress_callback:
                    progress_callback(i + 1, total, f"Processing slide {i + 1}...")

                t1 = time.time()
                logger.info("Assembling slide %d/%d  img=%s  audio=%s",
                           i + 1, total, clip_info.image_path, clip_info.audio_path)

                # Use duration from master audio track if available
                if slide_durations and i < len(slide_durations):
                    clip_info_copy = SlideClipInfo(
                        slide_index=clip_info.slide_index,
                        image_path=clip_info.image_path,
                        video_path=clip_info.video_path,
                        audio_path=None,  # No per-slide audio — master track handles it
                        duration=slide_durations[i],
                    )
                else:
                    clip_info_copy = clip_info

                clip = self.create_slide_clip(clip_info_copy)
                if clip:
                    logger.info("Slide %d assembled in %.1fs duration=%.1fs", i + 1, time.time() - t1, clip.duration)
                else:
                    logger.warning("Slide %d FAILED", i + 1)
                if clip is None:
                    logger.warning("Skipping slide %d due to error", i + 1)
                    continue

                # Apply transition effects on the slide clip
                if clip and self.slide_transition != "none" and self.transition_duration > 0:
                    clip = self._apply_transition_effect(clip, i, total)

                clips.append(clip)

                # Add transition (except after last slide): the slide's own
                # pause override, else the job's transition pause - the same
                # gap the master track left after it.
                pause = pause_after(clip_info, self.transition_pause)
                if add_transitions and pause > 0 and i < total - 1:
                    clips.append(self.create_transition_clip(pause))

            if not clips:
                logger.error("No clips to combine")
                return False

            # Add intro title card
            if self.intro_text:
                intro = self._create_title_card(
                    self.intro_text, self.intro_subtitle, self.intro_duration
                )
                clips.insert(0, intro)

            # Add outro title card
            if self.outro_text:
                outro = self._create_title_card(
                    self.outro_text, "", self.outro_duration
                )
                clips.append(outro)

            if progress_callback:
                progress_callback(total, total, "Combining clips...")

            if cancel_check and cancel_check():
                raise CancelledError("Cancelled before concatenation")

            logger.info("Concatenating %d clips...", len(clips))
            t1 = time.time()
            final_video = concatenate_videoclips(clips, method="compose")
            logger.info("Concatenation done in %.1fs  total_duration=%.1fs",
                       time.time() - t1, final_video.duration)

            # Attach single continuous audio track (avoids per-clip boundary clicks).
            # The track already opens with the intro card's silence (see
            # _build_master_audio), so it is attached at t=0.
            if master_audio_path and master_audio_path.exists():
                master_audio_clip = AudioFileClip(str(master_audio_path))
                # Trim or pad to match video duration
                if master_audio_clip.duration > final_video.duration:
                    master_audio_clip = master_audio_clip.subclipped(0, final_video.duration)
                elif master_audio_clip.duration < final_video.duration:
                    # Pad with silence — the outro card has no audio in master track
                    pass  # AudioClip shorter than video is fine, moviepy fills with silence
                final_video = final_video.with_audio(master_audio_clip)
                logger.info("Attached master audio track (%.1fs)", master_audio_clip.duration)

            # Mix background music across the whole video
            if self.background_music_paths:
                if progress_callback:
                    progress_callback(total, total, "Mixing background music...")
                logger.info("Mixing background music...")
                t1 = time.time()
                final_video = self._apply_background_music(final_video)
                logger.info("Music mixed in %.1fs", time.time() - t1)

            # Apply watermark overlay
            if self.watermark_text or (self.watermark_image and Path(self.watermark_image).exists()):
                if progress_callback:
                    progress_callback(total, total, "Applying watermark...")
                logger.info("Applying watermark...")
                final_video = self._apply_watermark(final_video)

            if progress_callback:
                progress_callback(total, total, "Writing video file...")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Write moviepy's temp audio file to assets/temp instead of project root
            temp_dir = Path(__file__).resolve().parent.parent / "assets" / "temp"
            temp_dir.mkdir(parents=True, exist_ok=True)

            # Use custom logger to report encoding progress and handle cancellation
            enc_logger = _EncodingProgressLogger(
                encoding_callback, cancel_check,
            ) if (encoding_callback or cancel_check) else None
            logger.info("Writing video to %s...", output_path)
            t1 = time.time()
            final_video.write_videofile(
                str(output_path),
                fps=self.fps,
                codec="libx264",
                audio_codec="aac",
                preset="ultrafast",
                bitrate=self.video_bitrate or None,
                threads=0,
                logger=enc_logger,
                temp_audiofile_path=str(temp_dir) + "/",
            )
            logger.info("Video written in %.1fs", time.time() - t1)

            # Embed chapter markers if slide titles are available
            if slide_titles:
                self._embed_chapters(output_path, slide_clips, slide_titles)

            if progress_callback:
                progress_callback(total, total, "Complete!")

            logger.info("Total create_video time: %.1fs", time.time() - t0)
            return True

        except CancelledError:
            logger.warning("Encoding cancelled by user")
            # Clean up partial output
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
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
            # Always release moviepy resources
            if final_video:
                try:
                    final_video.close()
                except Exception:
                    pass
            for clip in clips:
                try:
                    clip.close()
                except Exception:
                    pass
            if self._transition_audio:
                try:
                    self._transition_audio.close()
                except Exception:
                    pass
