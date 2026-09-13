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
    CompositeVideoClip, TextClip,
)

from utils.logger import get_logger
logger = get_logger("VIDEO")


def _active_onset_profile():
    """Return the onset profile for the currently configured TTS provider."""
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


def _build_master_audio(
    slide_clips: list,
    voice_start_delay: float,
    transition_pause: float,
    transition_sound_path=None,
) -> tuple:
    """Build a single continuous audio track from all slide audio files.

    Instead of attaching audio per-clip (which causes clicks at boundaries),
    this concatenates all audio into one seamless pydub track with precise
    silence gaps for voice delay and transitions.

    Returns:
        (master_audio_path, slide_durations) where slide_durations is a list
        of (visual_duration, audio_start_offset) per slide for syncing.
        Returns (None, []) if no audio files are found.
    """
    try:
        from pydub import AudioSegment
        from pydub.silence import detect_leading_silence
    except ImportError:
        return None, []

    delay_ms = int(voice_start_delay * 1000)
    pause_ms = int(transition_pause * 1000)
    delay_silence = AudioSegment.silent(duration=delay_ms) if delay_ms > 0 else AudioSegment.empty()
    pause_silence = AudioSegment.silent(duration=pause_ms) if pause_ms > 0 else AudioSegment.empty()

    # Load transition sound if available
    trans_sound = None
    if transition_sound_path and Path(transition_sound_path).exists():
        try:
            trans_sound = AudioSegment.from_file(str(transition_sound_path))
        except Exception:
            pass

    master = AudioSegment.empty()
    slide_info = []  # (visual_duration_s,) per slide
    has_any_audio = False

    for i, clip_info in enumerate(slide_clips):
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

        # Trim TTS silence, then level the opening to match body volume.
        # Use the active provider's onset profile so quieter providers
        # (e.g. Kokoro) are not over-trimmed by Edge's fixed -13 dB threshold.
        _trim_leading_silence(str(audio_path), profile=_active_onset_profile())
        slide_audio = AudioSegment.from_file(str(audio_path))
        slide_audio = _level_opening(slide_audio, profile=_active_onset_profile())

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
) -> AudioFileClip:
    """Open an AudioFileClip with retries for antivirus file-lock delays.

    Args:
        trim_silence: If True, trim leading silence from TTS audio before loading.
                      This prevents the fade-in artifact from TTS engines.
    """
    if trim_silence:
        path = _trim_leading_silence(path, profile=_active_onset_profile())

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


class VideoCreator:
    """Creates MP4 videos from slides and audio."""

    def __init__(
        self,
        resolution: Tuple[int, int] = (1920, 1080),
        fps: int = 2,
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
    ):
        self.resolution = resolution
        self.fps = fps
        self.transition_pause = transition_pause
        self.voice_start_delay = voice_start_delay
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
                audio_clip = _open_audio_with_retry(str(clip_info.audio_path))
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

    def create_transition_clip(self) -> any:
        """Create a transition/pause clip between slides, with optional sound."""
        bg_color = (255, 255, 255) if self.slide_transition == "fade-to-white" else (0, 0, 0)
        if self._transition_audio:
            duration = max(self.transition_pause, self._transition_audio.duration)
            clip = ColorClip(
                size=self.resolution, color=bg_color, duration=duration,
            ).with_fps(self.fps).with_audio(self._transition_audio)
            return clip

        return ColorClip(
            size=self.resolution, color=bg_color, duration=self.transition_pause
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

            clip = clip.transform(_slide_in, apply_to="mask" if clip.mask else None)
            clip = clip.transform(_slide_in)

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

            clip = clip.transform(_slide_v)

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

    def _apply_watermark(self, video):
        """Overlay a text or image watermark on the video."""
        from moviepy import CompositeVideoClip

        try:
            watermark = None
            if self.watermark_text:
                watermark = TextClip(
                    text=self.watermark_text,
                    font_size=24,
                    color="white",
                    font="Arial",
                    duration=video.duration,
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
            logger.error("Error applying watermark: %s", e)
            return video

    def _create_title_card(self, text: str, subtitle: str = "", duration: float = 3.0):
        """Create a title card clip with centered text on black background."""
        from moviepy import CompositeVideoClip
        bg = ColorClip(size=self.resolution, color=(0, 0, 0), duration=duration).with_fps(self.fps)
        clips = [bg]
        try:
            title = TextClip(
                text=text, font_size=48, color="white", font="Arial",
                duration=duration, method="caption", size=(self.resolution[0] - 200, None),
            ).with_position(("center", "center" if not subtitle else 0.4), relative=subtitle != "")
            clips.append(title)
            if subtitle:
                sub = TextClip(
                    text=subtitle, font_size=28, color="#cccccc", font="Arial",
                    duration=duration, method="caption", size=(self.resolution[0] - 200, None),
                ).with_position(("center", 0.55), relative=True)
                clips.append(sub)
        except Exception as e:
            logger.error("Title card text error: %s", e)
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
            # Calculate chapter timestamps from clip durations
            chapters = []
            current_time = 0.0
            for i, clip_info in enumerate(slide_clips):
                title = slide_titles[i] if i < len(slide_titles) else f"Slide {i + 1}"
                if not title or not title.strip():
                    title = f"Slide {i + 1}"

                # Get duration from audio or default
                duration = clip_info.duration or 5.0
                if clip_info.audio_path and clip_info.audio_path.exists():
                    try:
                        clip = AudioFileClip(str(clip_info.audio_path))
                        duration = clip.duration
                        clip.close()
                    except Exception:
                        pass

                start_ms = int(current_time * 1000)
                end_ms = int((current_time + duration) * 1000)
                chapters.append((start_ms, end_ms, title))
                current_time += duration + self.transition_pause

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

                # Add transition (except after last slide)
                if add_transitions and self.transition_pause > 0 and i < total - 1:
                    clips.append(self.create_transition_clip())

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

            # Attach single continuous audio track (avoids per-clip boundary clicks)
            if master_audio_path and master_audio_path.exists():
                master_audio_clip = AudioFileClip(str(master_audio_path))
                # Trim or pad to match video duration
                if master_audio_clip.duration > final_video.duration:
                    master_audio_clip = master_audio_clip.subclipped(0, final_video.duration)
                elif master_audio_clip.duration < final_video.duration:
                    # Pad with silence — intro/outro cards have no audio in master track
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
