"""Video importer module — extract speech from video, transcribe, and map to slides.

Uses faster-whisper (CTranslate2-optimized Whisper) to transcribe audio from
a video file into timestamped text segments. These segments can then be mapped
to slide boundaries so the narration can be re-voiced with a different TTS engine.

Workflow:
    1. Extract audio track from MP4/video file (via ffmpeg)
    2. Transcribe audio → list of TimedSegment(start, end, text)
    3. Match segments to slides using slide timestamps or even distribution
    4. Populate speaker notes with transcribed text
"""

import os
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import subprocess
import tempfile
import threading
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Callable

from utils.logger import get_logger

logger = get_logger("IMPORT")

# Whisper model sizes — tradeoff between speed and accuracy. Sizes are the
# approximate on-disk download of the faster-whisper (CTranslate2) model.
WHISPER_MODELS = {
    "tiny":   {"size": "~75 MB",  "description": "Fastest, lower accuracy"},
    "base":   {"size": "~145 MB", "description": "Fast, good for clear speech"},
    "small":  {"size": "~484 MB", "description": "Balanced speed/accuracy"},
    "medium": {"size": "~1.5 GB", "description": "High accuracy, slower"},
    "large-v3-turbo": {"size": "~1.6 GB", "description": "Fast and accurate — best on a GPU"},
    "distil-large-v3": {"size": "~1.5 GB", "description": "Fast large model, English only"},
    "large-v3": {"size": "~3.1 GB", "description": "Best accuracy, slowest"},
}

DEFAULT_MODEL = "medium"

# Module-level caches so a model isn't reloaded on every import.
_whisper_cache: dict = {}      # model_size -> WhisperModel
_model_device: dict = {}       # model_size -> "cuda" | "cpu"
_last_device = {"device": "cpu"}
_load_lock = threading.Lock()  # guards the check-then-load in _get_whisper_model
_cuda_device_count = None
_cuda_disabled = False         # set once GPU inference has proven broken this run
_cuda_dll_dirs_added = False
# Handles from os.add_dll_directory MUST be kept alive: when a handle is
# garbage-collected the directory is removed from the DLL search path, and
# CTranslate2 loads cuBLAS/cuDNN lazily at inference time (after the model has
# loaded), so a dropped directory shows up as "cublas64_12.dll not found" only
# once transcription starts. Hold them for the process lifetime.
_cuda_dll_handles: list = []


def _add_cuda_dll_dirs() -> None:
    """On Windows, add the pip-installed CUDA runtime DLL folders to the search
    path so CTranslate2 can load cuBLAS/cuDNN, keeping the handles alive. No-op
    on non-Windows, if the ``nvidia-*-cu12`` wheels are absent, or after the
    first call.
    """
    global _cuda_dll_dirs_added
    if _cuda_dll_dirs_added:
        return
    _cuda_dll_dirs_added = True
    if os.name != "nt":
        return
    try:
        import nvidia
        # `nvidia` is a PEP 420 namespace package: __file__ is None, so use
        # __path__ (the list of directories the wheels populate).
        added = False
        for root in list(getattr(nvidia, "__path__", [])):
            base = Path(root)
            for sub in ("cublas/bin", "cudnn/bin", "cuda_nvrtc/bin"):
                d = base / sub
                if d.is_dir():
                    _cuda_dll_handles.append(os.add_dll_directory(str(d.resolve())))
                    logger.debug("Added CUDA DLL dir: %s", d)
                    added = True
        if not added:
            logger.debug("No CUDA DLL dirs found under nvidia.__path__=%s",
                         list(getattr(nvidia, "__path__", [])))
    except Exception as e:  # nvidia wheels not installed, or add_dll_directory unavailable
        logger.debug("CUDA DLL dirs not added: %s", e)


def cuda_available() -> bool:
    """True when CTranslate2 can see at least one CUDA device (driver present).

    This only reports that a GPU exists; actually running a model also needs the
    cuBLAS/cuDNN runtime. ``_get_whisper_model`` falls back to the CPU if the GPU
    model fails to load, and ``transcribe_audio`` falls back (and calls
    ``_disable_cuda``) if it instead fails at inference — the common case when the
    driver is present but the ``[gpu]`` wheels are not.
    """
    global _cuda_device_count
    if _cuda_disabled:
        return False
    if _cuda_device_count is None:
        try:
            import ctranslate2
            _cuda_device_count = ctranslate2.get_cuda_device_count()
        except Exception:
            _cuda_device_count = 0
    return _cuda_device_count > 0


def _disable_cuda() -> None:
    """Stop attempting the GPU for the rest of this process.

    Called when GPU inference fails (e.g. the cuBLAS/cuDNN runtime is missing):
    the failure surfaces only when transcription starts, not at model load, so
    without this every subsequent model would load on the GPU and fail again.
    """
    global _cuda_disabled
    _cuda_disabled = True


def _evict_model(model_size: str) -> None:
    """Drop a cached model so the next load re-resolves its device."""
    _whisper_cache.pop(model_size, None)
    _model_device.pop(model_size, None)


def recommended_default_model() -> str:
    """large-v3-turbo when a GPU is present (fast and accurate), else medium."""
    return "large-v3-turbo" if cuda_available() else DEFAULT_MODEL


def last_load_device() -> str:
    """Device the most recently loaded Whisper model ran on ("cuda" or "cpu")."""
    return _last_device["device"]


def _get_whisper_model(model_size: str, force_cpu: bool = False):
    """Return a cached WhisperModel, loading it only on first use.

    Prefers CUDA (float16) when a GPU is present and ``force_cpu`` is False,
    and falls back to CPU (int8) if the GPU model fails to *load*. A GPU whose
    runtime is missing only fails at inference, not load, so the caller
    (``transcribe_audio``) retries with ``force_cpu=True`` on a transcription
    error. The device actually used is recorded (see ``last_load_device``).
    """
    if model_size in _whisper_cache:
        _last_device["device"] = _model_device.get(model_size, "cpu")
        return _whisper_cache[model_size]

    from faster_whisper import WhisperModel

    with _load_lock:
        # Another thread may have loaded it while we waited for the lock.
        if model_size in _whisper_cache:
            _last_device["device"] = _model_device.get(model_size, "cpu")
            return _whisper_cache[model_size]

        if not force_cpu and cuda_available():
            _add_cuda_dll_dirs()
            try:
                model = WhisperModel(model_size, device="cuda", compute_type="float16")
                _whisper_cache[model_size] = model
                _model_device[model_size] = "cuda"
                _last_device["device"] = "cuda"
                logger.info("Whisper '%s' loaded on GPU (cuda/float16)", model_size)
                return model
            except Exception as e:
                logger.warning("GPU load of Whisper '%s' failed (%s) — using CPU", model_size, e)

        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        _whisper_cache[model_size] = model
        _model_device[model_size] = "cpu"
        _last_device["device"] = "cpu"
        logger.info("Whisper '%s' loaded on CPU (int8)", model_size)
        return model


@dataclass
class TimedSegment:
    """A timestamped text segment from transcription."""
    start: float    # seconds
    end: float      # seconds
    text: str
    words: List[dict] = field(default_factory=list)  # word-level timing


@dataclass
class SlideTranscript:
    """Transcribed text assigned to a single slide."""
    slide_index: int
    text: str
    start_time: float
    end_time: float
    segments: List[TimedSegment] = field(default_factory=list)


@dataclass
class ImportResult:
    """Result of importing a video file."""
    video_path: str
    audio_path: str
    duration: float
    language: str
    segments: List[TimedSegment]
    slide_transcripts: List[SlideTranscript]
    model_used: str
    device: str = "cpu"  # "cuda" (GPU) or "cpu"


def extract_audio(video_path: Path, output_path: Optional[Path] = None) -> Path:
    """Extract audio track from video file using ffmpeg.

    Args:
        video_path: Path to input video (MP4, AVI, MKV, etc.)
        output_path: Where to save extracted audio (default: temp file)

    Returns:
        Path to extracted WAV file.
    """
    if output_path is None:
        output_path = Path(tempfile.mkdtemp()) / "extracted_audio.wav"

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn",                    # no video
        "-acodec", "pcm_s16le",   # 16-bit WAV
        "-ar", "16000",           # 16kHz (Whisper's native sample rate)
        "-ac", "1",               # mono
        "-y",                     # overwrite
        str(output_path),
    ]

    logger.info(f"Extracting audio from {video_path.name}...")
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        # Extract the actual error line (skip the version/config header)
        err_lines = [ln for ln in result.stderr.splitlines()
                     if not ln.startswith(("  ", "ffmpeg version", "  built", "  configuration",
                                           "  lib", "  Copyright"))]
        err_msg = "\n".join(err_lines[-5:]) if err_lines else result.stderr[-500:]
        raise RuntimeError(f"ffmpeg failed: {err_msg}")

    logger.info(f"Audio extracted: {output_path} ({output_path.stat().st_size / 1024:.0f} KB)")
    return output_path


def transcribe_audio(
    audio_path: Path,
    model_size: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> tuple[List[TimedSegment], str, float]:
    """Transcribe audio file using faster-whisper.

    Args:
        audio_path: Path to WAV/MP3 audio file.
        model_size: Whisper model size (tiny … large-v3, large-v3-turbo, distil-large-v3).
        language: Language code (e.g. "en"). None = auto-detect.
        on_progress: Callback(progress_pct, status_text).

    Returns:
        Tuple of (segments, detected_language, duration_seconds).
    """
    try:
        from faster_whisper import WhisperModel  # noqa: F401 — verify importable
    except ImportError:
        raise ImportError(
            "faster-whisper is required for video import. "
            "Install it with: pip install faster-whisper"
        )

    if model_size not in WHISPER_MODELS:
        model_size = DEFAULT_MODEL

    if on_progress:
        on_progress(0.05, f"Loading Whisper model ({model_size})...")

    logger.info(f"Loading Whisper model: {model_size}")

    def _run(m):
        """Transcribe with model ``m`` and collect segments.

        cuBLAS/cuDNN load lazily here — during ``m.transcribe`` and while
        iterating the segment generator — so a broken GPU runtime raises inside
        this function rather than at model construction, letting the caller
        fall back to the CPU.
        """
        raw_segments, info = m.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,          # filter out non-speech
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        _lang = info.language
        _dur = info.duration
        logger.info(f"Language: {_lang}, Duration: {_dur:.1f}s")
        _segs = []
        total_processed = 0.0
        for seg in raw_segments:
            words = []
            if seg.words:
                words = [
                    {"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                    for w in seg.words
                ]
            _segs.append(TimedSegment(
                start=seg.start, end=seg.end, text=seg.text.strip(), words=words,
            ))
            total_processed = seg.end
            if on_progress and _dur > 0:
                pct = 0.15 + 0.75 * (total_processed / _dur)
                on_progress(min(pct, 0.90), f"Transcribing... {total_processed:.0f}s / {_dur:.0f}s")
        return _segs, _lang, _dur

    model = _get_whisper_model(model_size)
    device = last_load_device()
    if on_progress:
        on_progress(0.15, f"Transcribing audio on {'GPU' if device == 'cuda' else 'CPU'}...")
    logger.info("Starting transcription on %s...", device.upper())

    try:
        segments, detected_lang, duration = _run(model)
    except Exception as e:
        if device != "cuda":
            raise
        # The GPU model loaded but inference failed — usually a missing
        # cuBLAS/cuDNN runtime (GPU driver present, [gpu] extra not installed).
        # Fall back to the CPU once and stop using the GPU for the rest of the run.
        logger.warning("GPU transcription failed (%s) — retrying on CPU", e)
        _disable_cuda()
        _evict_model(model_size)
        model = _get_whisper_model(model_size, force_cpu=True)
        if on_progress:
            on_progress(0.15, "Transcribing audio on CPU (GPU unavailable)...")
        segments, detected_lang, duration = _run(model)

    logger.info(f"Transcription complete: {len(segments)} segments")

    if on_progress:
        on_progress(0.95, "Transcription complete")

    return segments, detected_lang, duration


def get_video_chapters(video_path: Path) -> List[dict]:
    """Extract chapter markers from a video file using ffprobe.

    Returns list of {"start": float, "end": float, "title": str}.
    """
    cmd = [
        "ffprobe", "-i", str(video_path),
        "-print_format", "json",
        "-show_chapters",
        "-loglevel", "quiet",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        chapters = []
        for ch in data.get("chapters", []):
            chapters.append({
                "start": float(ch.get("start_time", 0)),
                "end": float(ch.get("end_time", 0)),
                "title": ch.get("tags", {}).get("title", f"Chapter {len(chapters) + 1}"),
            })
        return chapters
    except Exception as e:
        logger.warning(f"Could not extract chapters: {e}")
        return []


def map_segments_to_slides(
    segments: List[TimedSegment],
    num_slides: int,
    chapter_markers: Optional[List[dict]] = None,
) -> List[SlideTranscript]:
    """Map transcribed segments to slide boundaries.

    Uses chapter markers if available (e.g. from our own generated videos
    which embed chapter metadata). Otherwise distributes segments evenly
    across the specified number of slides.

    Args:
        segments: Timestamped transcript segments.
        num_slides: Number of slides to map to.
        chapter_markers: Optional chapter markers from video.

    Returns:
        List of SlideTranscript, one per slide.
    """
    if not segments:
        return [SlideTranscript(i, "", 0, 0) for i in range(num_slides)]

    total_duration = segments[-1].end if segments else 0

    # Strategy 1: Use chapter markers if available and count matches
    if chapter_markers and len(chapter_markers) == num_slides:
        logger.info(f"Using {len(chapter_markers)} chapter markers for slide mapping")
        slide_transcripts = []
        for i, ch in enumerate(chapter_markers):
            ch_start = ch["start"]
            ch_end = ch["end"]
            matched = [s for s in segments if s.start >= ch_start - 0.5 and s.end <= ch_end + 0.5]
            text = " ".join(s.text for s in matched)
            slide_transcripts.append(SlideTranscript(
                slide_index=i,
                text=text.strip(),
                start_time=ch_start,
                end_time=ch_end,
                segments=matched,
            ))
        return slide_transcripts

    # Strategy 2: Detect natural pauses (> 2s silence) as slide boundaries
    boundaries = [0.0]
    for i in range(1, len(segments)):
        gap = segments[i].start - segments[i - 1].end
        if gap >= 2.0:
            boundaries.append(segments[i].start)
    boundaries.append(total_duration)

    # If detected boundaries roughly match slide count, use them
    if abs(len(boundaries) - 1 - num_slides) <= 2:
        logger.info(f"Using {len(boundaries) - 1} detected pause boundaries")
        # Adjust to exactly num_slides
        while len(boundaries) - 1 > num_slides:
            # Merge shortest gap
            gaps = [(boundaries[i + 1] - boundaries[i], i) for i in range(len(boundaries) - 1)]
            gaps.sort()
            boundaries.pop(gaps[0][1] + 1)
        while len(boundaries) - 1 < num_slides:
            # Split longest gap
            gaps = [(boundaries[i + 1] - boundaries[i], i) for i in range(len(boundaries) - 1)]
            gaps.sort(reverse=True)
            mid = (boundaries[gaps[0][1]] + boundaries[gaps[0][1] + 1]) / 2
            boundaries.insert(gaps[0][1] + 1, mid)

        boundaries.sort()
        slide_transcripts = []
        for i in range(num_slides):
            b_start = boundaries[i]
            b_end = boundaries[i + 1]
            matched = [s for s in segments if s.start >= b_start - 0.3 and s.end <= b_end + 0.3]
            text = " ".join(s.text for s in matched)
            slide_transcripts.append(SlideTranscript(
                slide_index=i,
                text=text.strip(),
                start_time=b_start,
                end_time=b_end,
                segments=matched,
            ))
        return slide_transcripts

    # Strategy 3: Even time distribution
    logger.info(f"Using even time distribution for {num_slides} slides")
    slice_dur = total_duration / num_slides
    slide_transcripts = []
    for i in range(num_slides):
        s_start = i * slice_dur
        s_end = (i + 1) * slice_dur
        matched = [s for s in segments if s.start >= s_start - 0.3 and s.start < s_end + 0.3]
        text = " ".join(s.text for s in matched)
        slide_transcripts.append(SlideTranscript(
            slide_index=i,
            text=text.strip(),
            start_time=s_start,
            end_time=s_end,
            segments=matched,
        ))
    return slide_transcripts


def extract_keyframes(
    video_path: Path,
    output_dir: Path,
    timestamps: Optional[List[float]] = None,
    max_frames: int = 30,
) -> List[Path]:
    """Extract keyframe images from a video at specified timestamps.

    If no timestamps given, extracts frames at scene changes or evenly
    distributed intervals. Useful for creating slide images from a
    standalone video (no PPTX).

    Args:
        video_path: Path to video file.
        output_dir: Directory to save extracted frames.
        timestamps: Specific times (seconds) to extract. None = auto.
        max_frames: Maximum number of frames to extract.

    Returns:
        List of paths to extracted PNG images.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if timestamps:
        # Extract at specific timestamps
        frames = []
        for i, ts in enumerate(timestamps[:max_frames]):
            out = output_dir / f"frame_{i + 1:03d}.png"
            cmd = [
                "ffmpeg", "-ss", str(ts), "-i", str(video_path),
                "-vframes", "1", "-q:v", "2", "-y", str(out),
            ]
            subprocess.run(cmd, capture_output=True, timeout=30)
            if out.exists():
                frames.append(out)
        logger.info(f"Extracted {len(frames)} keyframes at specified timestamps")
        return frames

    # Auto-detect scene changes using ffmpeg scene filter
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", "select='gt(scene,0.3)',showinfo",
        "-vsync", "vfr", "-frame_pts", "1",
        "-y", str(output_dir / "frame_%03d.png"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    frames = sorted(output_dir.glob("frame_*.png"))

    # If too many or too few, fall back to even distribution
    if len(frames) < 2 or len(frames) > max_frames:
        # Clean up scene-detected frames
        for f in frames:
            f.unlink()

        # Get video duration
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip()) if probe.stdout.strip() else 60
        interval = duration / min(max_frames, max(int(duration / 10), 5))

        frames = []
        ts = 0.0
        i = 0
        while ts < duration and i < max_frames:
            out = output_dir / f"frame_{i + 1:03d}.png"
            cmd = [
                "ffmpeg", "-ss", str(ts), "-i", str(video_path),
                "-vframes", "1", "-q:v", "2", "-y", str(out),
            ]
            subprocess.run(cmd, capture_output=True, timeout=30)
            if out.exists():
                frames.append(out)
            ts += interval
            i += 1

    logger.info(f"Extracted {len(frames)} keyframes from video")
    return frames[:max_frames]


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
        "-of", "csv=p=0", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return float(result.stdout.strip()) if result.stdout.strip() else 0.0


def import_video(
    video_path: Path,
    num_slides: Optional[int] = None,
    model_size: str = DEFAULT_MODEL,
    language: Optional[str] = None,
    on_progress: Optional[Callable[[float, str], None]] = None,
    extract_frames: bool = False,
    frames_output_dir: Optional[Path] = None,
) -> ImportResult:
    """Full import pipeline: extract audio → transcribe → optionally map to slides.

    Works in two modes:
    - With num_slides: maps transcription to slide boundaries (for re-voicing PPTX videos)
    - Without num_slides: returns full transcript segmented by natural pauses
      (for standalone video transcription / product demos)

    Args:
        video_path: Path to video file (MP4, AVI, MKV, etc.)
        num_slides: Number of slides to map to. None = auto-segment by pauses.
        model_size: Whisper model size.
        language: Language code or None for auto-detect.
        on_progress: Callback(progress_pct, status_text).
        extract_frames: Whether to extract keyframe images from the video.
        frames_output_dir: Where to save extracted frames.

    Returns:
        ImportResult with transcribed and slide-mapped text.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    # Step 1: Extract audio
    if on_progress:
        on_progress(0.02, "Extracting audio from video...")
    audio_path = extract_audio(video_path)

    # Step 2: Check for chapter markers
    if on_progress:
        on_progress(0.05, "Checking for chapter markers...")
    chapters = get_video_chapters(video_path)
    if chapters:
        logger.info(f"Found {len(chapters)} chapters in video")

    # Step 3: Transcribe
    segments, detected_lang, duration = transcribe_audio(
        audio_path, model_size=model_size, language=language,
        on_progress=on_progress,
    )

    # Step 4: Map to slides or auto-segment by pauses
    if on_progress:
        on_progress(0.92, "Mapping transcription...")

    if num_slides and num_slides > 0:
        slide_transcripts = map_segments_to_slides(segments, num_slides, chapters)
    elif chapters:
        # Use chapter markers as natural segments
        slide_transcripts = map_segments_to_slides(segments, len(chapters), chapters)
    else:
        # Auto-segment by natural pauses (for standalone videos)
        slide_transcripts = _auto_segment_by_pauses(segments, duration)

    # Step 5: Extract keyframes if requested
    extracted_frames = []
    if extract_frames and frames_output_dir:
        if on_progress:
            on_progress(0.96, "Extracting keyframes...")
        # Use the midpoint of each segment as the keyframe timestamp
        timestamps = [
            (st.start_time + st.end_time) / 2 for st in slide_transcripts
        ]
        extracted_frames = extract_keyframes(
            video_path, frames_output_dir, timestamps=timestamps,
        )

    if on_progress:
        on_progress(1.0, "Import complete")

    result = ImportResult(
        video_path=str(video_path),
        audio_path=str(audio_path),
        duration=duration,
        language=detected_lang,
        segments=segments,
        slide_transcripts=slide_transcripts,
        model_used=model_size,
        device=last_load_device(),
    )

    n_slides = len(slide_transcripts)
    logger.info(
        f"Import complete: {len(segments)} segments → {n_slides} sections, "
        f"language={detected_lang}, duration={duration:.1f}s"
    )
    return result


def _auto_segment_by_pauses(
    segments: List[TimedSegment], duration: float, min_pause: float = 2.0,
) -> List[SlideTranscript]:
    """Group segments into sections based on natural pauses in speech.

    Each pause >= min_pause seconds triggers a new section. This is used
    for standalone video imports where there's no slide count to target.
    """
    if not segments:
        return []

    sections: List[SlideTranscript] = []
    current_segs: List[TimedSegment] = [segments[0]]

    for i in range(1, len(segments)):
        gap = segments[i].start - segments[i - 1].end
        if gap >= min_pause:
            # End current section
            text = " ".join(s.text for s in current_segs)
            sections.append(SlideTranscript(
                slide_index=len(sections),
                text=text.strip(),
                start_time=current_segs[0].start,
                end_time=current_segs[-1].end,
                segments=list(current_segs),
            ))
            current_segs = []
        current_segs.append(segments[i])

    # Last section
    if current_segs:
        text = " ".join(s.text for s in current_segs)
        sections.append(SlideTranscript(
            slide_index=len(sections),
            text=text.strip(),
            start_time=current_segs[0].start,
            end_time=current_segs[-1].end,
            segments=list(current_segs),
        ))

    logger.info(f"Auto-segmented into {len(sections)} sections by pauses (>={min_pause}s)")
    return sections
