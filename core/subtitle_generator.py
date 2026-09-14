"""Subtitle/SRT/VTT generation from audio using faster-whisper.

Transcribes audio (or extracts audio from video) and produces subtitle files
in both SRT and WebVTT formats. Segments are split to ~10 words max for
readability.

Usage:
    from core.subtitle_generator import generate_subtitles, burn_subtitles

    result = generate_subtitles(Path("video.mp4"), Path("output/"))
    # result == {"srt": Path, "vtt": Path, "segments": [...]}

    burn_subtitles(Path("video.mp4"), result["srt"], Path("video_subtitled.mp4"))
"""

import subprocess
from pathlib import Path
from typing import List, Optional, Callable

from core.video_importer import _get_whisper_model, extract_audio, DEFAULT_MODEL
from utils.logger import get_logger

logger = get_logger("SUBS")

# Maximum words per subtitle segment for readability
MAX_WORDS_PER_SEGMENT = 10


def _format_srt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SRT."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_vtt_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for WebVTT."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _split_words_into_segments(words: List[dict], max_words: int = MAX_WORDS_PER_SEGMENT) -> List[dict]:
    """Split word-level timestamps into subtitle segments of max ~max_words words.

    Each returned segment has keys: start, end, text.
    """
    if not words:
        return []

    segments = []
    current_words = []

    for word_info in words:
        current_words.append(word_info)

        if len(current_words) >= max_words:
            text = "".join(w["word"] for w in current_words).strip()
            segments.append({
                "start": current_words[0]["start"],
                "end": current_words[-1]["end"],
                "text": text,
            })
            current_words = []

    # Flush remaining words
    if current_words:
        text = "".join(w["word"] for w in current_words).strip()
        segments.append({
            "start": current_words[0]["start"],
            "end": current_words[-1]["end"],
            "text": text,
        })

    return segments


def _write_srt(segments: List[dict], output_path: Path) -> None:
    """Write segments to an SRT file."""
    lines = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_format_srt_time(seg['start'])} --> {_format_srt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")  # blank line between entries

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"SRT written: {output_path} ({len(segments)} segments)")


def _write_vtt(segments: List[dict], output_path: Path) -> None:
    """Write segments to a WebVTT file."""
    lines = ["WEBVTT", ""]
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_format_vtt_time(seg['start'])} --> {_format_vtt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")  # blank line between entries

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info(f"VTT written: {output_path} ({len(segments)} segments)")


def generate_subtitles(
    audio_path: Path,
    output_dir: Path,
    model_size: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """Generate SRT and VTT subtitle files from an audio or video file.

    Args:
        audio_path: Path to audio file (WAV/MP3) or video file (MP4/MKV/etc.).
                    If a video file is given, audio is extracted first.
        output_dir: Directory to write .srt and .vtt files.
        model_size: Whisper model size (tiny/base/small/medium/large-v3).
        language: Language code (e.g. "en"). None = auto-detect.
        on_progress: Callback(progress_pct, status_text).

    Returns:
        {"srt": Path, "vtt": Path, "segments": list[dict]}
        Each segment dict has keys: start, end, text.
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # If input is a video file, extract audio first
    video_extensions = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv"}
    temp_audio = None
    if audio_path.suffix.lower() in video_extensions:
        if on_progress:
            on_progress(0.02, "Extracting audio from video...")
        logger.info(f"Extracting audio from video: {audio_path.name}")
        temp_audio = extract_audio(audio_path)
        actual_audio = temp_audio
    else:
        actual_audio = audio_path

    try:
        if not actual_audio.exists():
            raise FileNotFoundError(f"Audio file not found: {actual_audio}")

        # Load whisper model (cached)
        if on_progress:
            on_progress(0.05, f"Loading Whisper model ({model_size})...")
        logger.info(f"Loading Whisper model: {model_size}")
        model = _get_whisper_model(model_size)

        # Transcribe with word-level timestamps
        if on_progress:
            on_progress(0.15, "Transcribing audio...")
        logger.info("Starting transcription for subtitles...")

        raw_segments, info = model.transcribe(
            str(actual_audio),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        detected_lang = info.language
        duration = info.duration
        logger.info(f"Language: {detected_lang}, Duration: {duration:.1f}s")

        # Collect all word-level timestamps across segments
        all_words = []
        total_processed = 0.0
        for seg in raw_segments:
            if seg.words:
                for w in seg.words:
                    all_words.append({
                        "word": w.word,
                        "start": w.start,
                        "end": w.end,
                    })

            total_processed = seg.end
            if on_progress and duration > 0:
                pct = 0.15 + 0.70 * (total_processed / duration)
                on_progress(min(pct, 0.85), f"Transcribing... {total_processed:.0f}s / {duration:.0f}s")

        logger.info(f"Transcription complete: {len(all_words)} words")

        # Split into readable subtitle segments (~10 words each)
        if on_progress:
            on_progress(0.88, "Splitting into subtitle segments...")

        subtitle_segments = _split_words_into_segments(all_words, MAX_WORDS_PER_SEGMENT)
        logger.info(f"Split into {len(subtitle_segments)} subtitle segments")

        # Write SRT and VTT files
        if on_progress:
            on_progress(0.92, "Writing subtitle files...")

        stem = audio_path.stem
        srt_path = output_dir / f"{stem}.srt"
        vtt_path = output_dir / f"{stem}.vtt"

        _write_srt(subtitle_segments, srt_path)
        _write_vtt(subtitle_segments, vtt_path)

        if on_progress:
            on_progress(1.0, f"Subtitles complete: {len(subtitle_segments)} segments")

        return {
            "srt": srt_path,
            "vtt": vtt_path,
            "segments": subtitle_segments,
        }
    finally:
        # The WAV extracted from a video (and the temp dir made for it) is
        # scratch: a full-length 16 kHz track per render would otherwise pile
        # up in the system temp folder.
        if temp_audio is not None:
            temp_audio.unlink(missing_ok=True)
            try:
                temp_audio.parent.rmdir()
            except OSError:
                pass


def burn_subtitles(video_path: Path, srt_path: Path, output_path: Path) -> bool:
    """Burns subtitles into video using ffmpeg.

    Renders the SRT text permanently onto the video frames. The output
    video will have hardcoded subtitles visible without a player subtitle
    toggle.

    Args:
        video_path: Path to input video file.
        srt_path: Path to SRT subtitle file.
        output_path: Path for the output video with burned-in subtitles.

    Returns:
        True on success, False on failure.
    """
    # The resolved ffmpeg (system PATH or the imageio bundle), as every other
    # ffmpeg call in the engine uses it - a bare "ffmpeg" is not on PATH on a
    # machine that relies on the bundled one. Read at call time so it can be
    # pointed elsewhere by tests.
    from utils.config import FFMPEG_PATH

    video_path = Path(video_path)
    srt_path = Path(srt_path)
    output_path = Path(output_path)

    if not FFMPEG_PATH:
        logger.error("FFmpeg not found - cannot burn subtitles")
        return False
    if not video_path.exists():
        logger.error(f"Video not found: {video_path}")
        return False
    if not srt_path.exists():
        logger.error(f"SRT not found: {srt_path}")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Escape path for ffmpeg subtitle filter (backslashes and colons)
    srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")

    subtitle_filter = (
        f"subtitles='{srt_escaped}'"
        f":force_style='FontSize=24,PrimaryColour=&H00FFFFFF'"
    )

    cmd = [
        FFMPEG_PATH,
        "-i", str(video_path),
        "-vf", subtitle_filter,
        "-c:a", "copy",
        "-y",
        str(output_path),
    ]

    logger.info(f"Burning subtitles into video: {output_path.name}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg subtitle burn failed: {result.stderr[:500]}")
            return False

        logger.info(f"Subtitled video created: {output_path} ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")
        return True
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg subtitle burn timed out (10 min limit)")
        return False
    except Exception as exc:
        logger.error(f"Subtitle burn error: {exc}")
        return False
