"""Video processing pipeline — runs in background threads.

Handles the full generate and rebuild workflows:
  export slides → generate audio → mix music → create video → write SRT.

Separated from the UI so the logic is independently testable and
keeps web_app.py focused on presentation.
"""

import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Callable

from concurrent.futures import ThreadPoolExecutor, as_completed

from services.file_item import FileItem, VIDEO_SUFFIXES
from services.narration import MAX_OFFSET_SECONDS, MAX_SPEED, MIN_SPEED
from services.slides import slide_export_lock
from services.styles import TEMP_DIR
from utils.config import config, CONFIG_DIR
from utils.helpers import get_output_filename
from utils.logger import get_logger

logger = get_logger("PROC")
from core.pptx_exporter import PPTXExporter
from core.tts_provider import TTSProvider, effective_voice
from core.video_creator import VideoCreator, SlideClipInfo, effective_pause, fps_for_transition
from core.project_manager import ProjectManager, get_project_dir


# Type alias for progress callback: (progress_fraction, status_message)
ProgressCallback = Callable[[float, str], None]

# The subtitle modes a render accepts: one cue per slide from the notes, a
# word-level Whisper pass over the rendered MP4, or nothing.
SUBTITLE_MODES = ("none", "slide", "whisper")


def _pinned(value, fallback):
    """``value`` unless it is None, then ``fallback`` - the config value read
    ONCE at construction, so a job never reads the shared config mid-run."""
    return fallback if value is None else value


def _ensure_temp_dir() -> Path:
    """Create and return the assets/temp directory.

    Scratch files live in a per-job directory under it (``_job_scratch``)
    that the job removes when it is done; nothing ever sweeps the whole
    directory, because two jobs run at once and one used to wipe the other's
    build in progress.
    """
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    return TEMP_DIR


def _job_scratch(prefix: str) -> Path:
    """A fresh, uniquely named scratch directory under assets/temp for one
    job step; the caller removes it in a ``finally``."""
    return Path(tempfile.mkdtemp(prefix=prefix, dir=_ensure_temp_dir()))


def _normalise_text(text: str) -> str:
    """Collapse whitespace so cosmetic differences do not count as edits."""
    return " ".join((text or "").split())


def _seconds(value, fallback: float) -> float:
    """A timestamp as a float, falling back when it is missing or unusable.

    A segment carrying ``"start": None`` would otherwise raise inside
    ``float()`` and be swallowed into a bare "Re-voice failed".
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _offset(seg) -> float:
    """One sentence's per-sentence offset in seconds (``services.narration``).

    0.0 when the key is absent - which is every segment of every project made
    before the narration editor existed - or unusable. Positive pushes the
    sentence later, negative earlier.

    Bounded and bool-rejecting for the same reason ``_explicit_speed`` is, and
    more urgently: ``assemble_master`` turns a pin into
    ``AudioSegment.silent(duration=gap)``, so a hand-edited project.json
    carrying ``"offset": 999999`` asks pydub to allocate days of raw PCM before
    anything else can go wrong. The API bounds it on the way in
    (``services.narration``, whose bound this is); this is the engine refusing
    to trust the file.
    """
    raw = seg.get("offset")
    if raw is None or isinstance(raw, bool):
        return 0.0
    try:
        offset = float(raw)
    except (TypeError, ValueError):
        return 0.0
    # NaN fails both comparisons and falls through to 0.0, which is what it should.
    return offset if -MAX_OFFSET_SECONDS <= offset <= MAX_OFFSET_SECONDS else 0.0


def _pin(seg, sec) -> float:
    """Where a sentence is pinned: the moment it was spoken plus its offset.

    Clamped at zero explicitly rather than leaning on ``assemble_master``'s
    accidental floor (a negative pin there produces a non-positive gap and is
    ignored), so the log line and what the user is told agree with what is
    actually done. The same helper computes the NEXT sentence's pin when the
    window is measured: an offset that moves a sentence must move the room the
    one before it has, or the tempo squeeze fires against a window that no
    longer exists.
    """
    return max(0.0, _seconds(seg.get("start"), sec["start"]) + _offset(seg))


def _explicit_speed(seg):
    """A sentence's own TTS speed (``services.narration``), or None.

    A sentence that carries one bypasses ``_per_sentence_speed`` AND the
    post-synthesis tempo squeeze: the user asked for that length and the user's
    number wins. An out-of-range or unusable value is treated as absent - the
    API bounds it on the way in (``services.narration``, whose bounds these
    are), and a hand-edited project.json must not be able to drive the
    synthesiser to 50x.
    """
    raw = seg.get("speed")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        speed = float(raw)
    except (TypeError, ValueError):
        return None
    return speed if MIN_SPEED <= speed <= MAX_SPEED else None


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


def narration_path_for(video_path: Path) -> Path:
    """Where a re-voice keeps its narration as a standalone audio file.

    The assembled narration used to live only in the job's scratch directory
    and be deleted with it, so the one thing an editor most wants - the new
    voice on its own, starting at the same zero as the picture - was thrown
    away every time. Kept beside the re-voiced video, it can be dragged onto a
    timeline in Camtasia or any other editor and lined up against the original.

    It shares the picture's START, not its length: this is the assembled master
    as it stands before the mux, and the pad out to the video's duration is
    ffmpeg's ``apad`` inside the audio swap, so the standalone file ends where
    the last sentence's audio does. It is also voice only - background music,
    when configured, is mixed in during the swap and not into this file.
    """
    return Path(video_path).with_name(f"{Path(video_path).stem}_narration.mp3")


def _split_sentences(text: str) -> list:
    """Split edited notes into sentences (on . ! ? followed by whitespace)."""
    return [p.strip() for p in _SENTENCE_BOUNDARY.split(text or "") if p.strip()]


def _spread_over_window(sentences, start, end) -> list:
    """Give each sentence a slice of [start, end] proportional to its length.

    Keeps one TTS call per sentence (short enough for every provider) and
    keeps the narration roughly where the original speech was, even though
    the exact per-sentence timestamps are unknown for edited text.
    """
    start, end = float(start), float(end)
    total_chars = sum(len(s) for s in sentences) or 1
    span = max(0.0, end - start)
    out, cursor = [], start
    for i, sentence in enumerate(sentences):
        seg_end = end if i == len(sentences) - 1 else cursor + span * len(sentence) / total_chars
        out.append({"start": round(cursor, 3), "end": round(seg_end, 3), "text": sentence})
        cursor = seg_end
    return out


def collect_revoice_segments(slides) -> list:
    """Timed text segments to synthesise for a re-voice, honouring edited notes.

    Whisper's per-sentence segments give the best sync with the original
    picture, but they are frozen at import time while the user edits the
    section's ``speaker_notes`` in the preview. When a slide's notes no longer
    match the joined segment text, the edited notes are split into sentences
    and spread across the slide's original window in proportion to their
    length, so what the user typed is what is heard and each sentence stays
    near its original position. Empty notes fall back to the original
    segments rather than producing silence. Slides with neither segments nor
    an original window are skipped.

    Every returned segment keeps its own ``start`` / ``end`` — the moment it was
    spoken, which is what the re-voicer pins it to — and also carries
    ``section`` (the slide index) with the section's ``section_start`` /
    ``section_end``, which bound the last sentence of the section.
    """
    result = []
    for idx, slide in enumerate(slides):
        segs = list(getattr(slide, "original_segments", None) or [])
        notes = _normalise_text(getattr(slide, "speaker_notes", "") or "")
        joined = _normalise_text(" ".join((s.get("text") or "") for s in segs))
        start = getattr(slide, "original_start_time", None)
        end = getattr(slide, "original_end_time", None)
        if segs and (not notes or notes == joined):
            chosen = [dict(s) for s in segs]
        elif notes and start is not None and end is not None:
            chosen = _spread_over_window(_split_sentences(notes), start, end)
        else:
            continue
        sec_start = float(start) if start is not None else float(chosen[0]["start"])
        sec_end = float(end) if end is not None else float(chosen[-1]["end"])
        for seg in chosen:
            seg["section"] = idx
            seg["section_start"] = sec_start
            seg["section_end"] = sec_end
        result.extend(chosen)
    return result


def group_by_section(segments) -> list:
    """Group consecutive segments by their ``section`` into pacing units.

    Returns ``[{"index", "start", "end", "segments"}]``. Every sentence is
    pinned on its own, so a section now only supplies the fallback bound for
    its last sentence; segments without a ``section`` key (older callers) each
    form their own unit spanning their own window.
    """
    groups = []
    for seg in segments:
        key = seg.get("section")
        if key is not None and groups and groups[-1]["index"] == key:
            groups[-1]["segments"].append(seg)
            continue
        groups.append({
            "index": key if key is not None else len(groups),
            "start": float(seg.get("section_start", seg["start"])),
            "end": float(seg.get("section_end", seg["end"])),
            "segments": [seg],
        })
    return groups


def assemble_master(timed_chunks, is_free: bool):
    """Concatenate aligned chunks; in synced mode pin chunks to their start times.

    ``timed_chunks`` is an ordered list of ``(start_seconds | None, unused,
    AudioSegment)``. In synced mode a chunk with a start time is placed there
    by filling the gap since the previous chunk's actual end with silence —
    that is how every sentence begins where it did in the original and how the
    pause that followed one becomes silence again. A chunk with ``None``
    follows the previous one immediately. A chunk that overran simply eats into
    the following gap instead of shifting everything after it. Free-pace mode
    ignores the start times and just concatenates.

    The middle slot is not read; the re-voicer passes ``None``.
    """
    from pydub import AudioSegment

    master = None
    for start_s, _end_s, chunk in timed_chunks:
        if not is_free and start_s is not None:
            cursor_ms = len(master) if master is not None else 0
            gap = int(round(float(start_s) * 1000)) - cursor_ms
            if gap > 0:
                chunk = AudioSegment.silent(duration=gap) + chunk
        master = chunk if master is None else master + chunk
    return master


# The speaking rate assumed when it could not be measured: 15 characters a
# second, which is roughly an unhurried English narrator. Named because three
# call sites fall back to it - no sample long enough, every sample failed, and
# (for the audition plan) a provider that cannot be reached at all.
DEFAULT_BASELINE_RATE = 15.0

# The post-synthesis tempo squeeze, in ``_revoice_video``: a sentence whose clip
# runs more than SQUEEZE_TOLERANCE over the moment the next one is due is sped
# up with ffmpeg's atempo to fit exactly, unless it would take more than
# SQUEEZE_MAX_FACTOR to do it - past that the speech stops being intelligible
# and the overrun is let through instead.
#
# The tolerance is deliberately loose: Whisper often reports one sentence ending
# exactly where the next begins, so a tight bound would tempo-adjust most of a
# clip and the differing factors would be audible as a wobble in the speaking
# rate. A small overrun is inaudible on its own and is absorbed at the next real
# pause, where the sentence after it is still pinned to its own moment.
#
# Named because the narration timeline has to predict this loop to place its
# clips - the squeeze is exactly what stops a long clip cascading into every
# sentence after it - and it reports these two numbers to the browser
# (``services.narration.plan``) rather than letting it carry its own copies.
SQUEEZE_TOLERANCE = 1.15
SQUEEZE_MAX_FACTOR = 2.0


def sentence_window(spoken, index: int, video_end: float) -> tuple[float, float]:
    """Where sentence ``index`` is pinned, and the moment the next one is due.

    ``spoken`` is the ordered list of ``(segment, section)`` pairs that will
    actually be synthesised - empty and muted sentences already filtered out,
    which is what gives the sentence BEFORE a muted one its room. The room a
    sentence has is the time until the next one is due, so the pause after it is
    slack it may borrow from before anything is sped up; the final sentence runs
    to the end of the VIDEO (``apad`` pads the narration to it and ``-shortest``
    trims there), falling back to its section's end when the duration could not
    be probed.

    The NEXT sentence's own offset counts: moving it later gives this one more
    room, moving it earlier takes room away, and measuring against its
    unadjusted start would fire the tempo squeeze against a window nothing will
    use.

    Module-level and shared on purpose. ``_revoice_video`` computes the window
    to decide each sentence's synthesis speed, and the timeline's audition plan
    (``services.narration.plan``) has to report the speed the render will
    actually use - so the two must agree by construction rather than by two
    copies of the same arithmetic staying in step.
    """
    seg, sec = spoken[index]
    start = _pin(seg, sec)
    if index + 1 < len(spoken):
        nxt, nxt_sec = spoken[index + 1]
        return start, _pin(nxt, nxt_sec)
    return start, max(_seconds(sec["end"], 0.0), video_end)


def per_sentence_speed(
    text: str, orig_duration: float, tts_baseline_rate: float, user_speed: float,
) -> float:
    """TTS speed for one sentence in synced mode.

    Never slower than the user's speed: the original narrator's pauses are
    folded into Whisper's segment windows, so fitting text to the window used to
    drag the voice down to 0.5x and it sounded drugged. A sentence that needs
    less time than its window ends early and the rest is silence
    (``assemble_master`` keeps the following sentence on time). Only when the
    original speaker was faster than the TTS baseline is the voice sped up, and
    at most by 30% - anything beyond that is left to the post-synthesis tempo
    adjustment so the speech stays intelligible.

    Module-level so the audition plan can ask what the render will do without
    building a ``VideoProcessor``; ``VideoProcessor._per_sentence_speed`` is
    kept as the call site the re-voice loop (and its tests) use.
    """
    if not text.strip() or orig_duration <= 0 or tts_baseline_rate <= 0:
        return user_speed
    orig_rate = len(text.strip()) / orig_duration
    needed = (orig_rate / tts_baseline_rate) * user_speed
    floor = user_speed
    ceiling = round(user_speed * 1.3, 2)
    return round(max(floor, min(ceiling, needed)), 2)


def sentence_speed(
    seg, text: str, window_s: float, baseline_rate: float, user_speed: float, rule=None,
) -> float:
    """The speed a SYNCED re-voice synthesises one sentence at.

    The sentence's own explicit speed wins outright - that is the "nothing
    guesses a rate" rule made concrete, and it bypasses the post-synthesis tempo
    squeeze as well (the caller checks ``_explicit_speed`` again for that). With
    no explicit speed the per-sentence fitting rule applies over the window the
    sentence has.

    ``rule`` is the fitting rule, defaulting to :func:`per_sentence_speed`;
    ``_revoice_video`` passes its own ``self._per_sentence_speed`` so the seam
    the sync tests patch stays where it is. One home for the precedence, because
    the timeline's audition plan has to report the speed the render will really
    use and a second copy of these two lines would be free to drift from it.
    """
    explicit = _explicit_speed(seg)
    if explicit is not None:
        return explicit
    return (rule or per_sentence_speed)(text, window_s, baseline_rate, user_speed)


def calibrate_tts_baseline(
    segments: list, tts_gen, tmp_dir: Path, *, provider: str, voice_id: str,
    progress=None, file_label: str = "",
) -> float:
    """Measure the TTS baseline speaking rate (chars/sec at speed 1.0).

    Single-voice on purpose: it samples at ``voice_id``, so a sentence carrying
    its own voice would measure the wrong one. It feeds nothing but
    :func:`per_sentence_speed`, which a sentence with an explicit speed bypasses
    entirely, so a per-sentence voice does not make the baseline wrong for the
    sentences that do use it. Muted sentences are left out of the sample: they
    are never synthesised, and one of them standing in for a third of the
    measurement would skew it for no reason.

    The three samples go through the shared TTS cache keyed on (text, voice,
    speed), so the audition plan calling this pays for them once and the render
    that follows reuses the very same entries.
    """
    from pydub import AudioSegment
    from pydub.silence import detect_leading_silence
    from core.tts_provider import get_onset_profile

    _onset_profile = get_onset_profile(provider)

    if progress:
        progress(0.82, f"{file_label}: Calibrating speech rate...")

    sample_segs = [
        s for s in segments
        if len(s.get("text", "").strip()) > 30 and not s.get("muted")
    ]
    if len(sample_segs) > 3:
        step = len(sample_segs) // 3
        sample_segs = [sample_segs[i * step] for i in range(3)]
    elif not sample_segs:
        return DEFAULT_BASELINE_RATE

    tts_chars = 0
    tts_total_dur = 0.0
    for i, seg in enumerate(sample_segs):
        text = seg["text"].strip()
        sample_path = tmp_dir / f"calibrate_{i}.mp3"
        try:
            tts_gen.generate_audio(
                text=text, voice_id=voice_id,
                output_path=sample_path, speed=1.0,
            )
            if sample_path.exists():
                audio = AudioSegment.from_file(str(sample_path))
                threshold_db = _onset_profile.resolve_threshold_db(audio)
                trim = detect_leading_silence(audio, silence_threshold=threshold_db, chunk_size=5)
                audio = audio[trim:]
                tts_chars += len(text)
                tts_total_dur += len(audio) / 1000.0
        except Exception:
            continue

    if tts_chars == 0 or tts_total_dur == 0:
        return DEFAULT_BASELINE_RATE

    rate = tts_chars / tts_total_dur
    logger.info("TTS baseline rate: %.1f chars/sec (from %d samples)", rate, len(sample_segs))
    return rate


class VideoProcessor:
    """Orchestrates the video generation pipeline.

    All public methods are designed to run in a background thread.
    They communicate progress via a callback rather than touching the UI.
    """

    def __init__(
        self,
        voice_id: str = "",
        resolution: tuple = (1920, 1080),
        background_music_paths: Optional[List[Path]] = None,
        transition_sound_path: Optional[Path] = None,
        speed: float = 1.0,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        video_bitrate: str = "",
        provider: str = "",
        slide_transition: Optional[str] = None,
        transition_duration: Optional[float] = None,
        transition_pause: Optional[float] = None,
        intro_text: Optional[str] = None,
        intro_subtitle: Optional[str] = None,
        intro_duration: Optional[float] = None,
        outro_text: Optional[str] = None,
        outro_duration: Optional[float] = None,
        watermark_text: Optional[str] = None,
        watermark_position: Optional[str] = None,
        watermark_opacity: Optional[float] = None,
        subtitles: str = "slide",
        whisper_model: str = "",
        export_webm: bool = False,
        export_gif: bool = False,
        export_audio_only: bool = False,
    ):
        self.voice_id = voice_id
        self.resolution = resolution
        self.background_music_paths = background_music_paths or []
        self.transition_sound_path = transition_sound_path
        self.speed = speed
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.style = style
        self.video_bitrate = video_bitrate
        # The TTS provider this run uses, fixed at construction: a job carries
        # its own choice (the request's, or the studio default at request time)
        # rather than reading config.tts_provider mid-run, so an admin changing
        # the default never alters a job that is already queued or running.
        self.provider = provider or config.tts_provider
        # The render options are per job for the same reason (two jobs run at
        # once and must never share or mutate the config): each is the value
        # given, else the config value pinned now for callers that pass none.
        self.slide_transition = _pinned(slide_transition, config.slide_transition)
        self.transition_duration = float(_pinned(transition_duration, config.transition_duration))
        self.transition_pause = float(_pinned(transition_pause, config.transition_pause))
        self.intro_text = _pinned(intro_text, config.intro_text)
        self.intro_subtitle = _pinned(intro_subtitle, config.intro_subtitle)
        self.intro_duration = float(_pinned(intro_duration, config.intro_duration))
        self.outro_text = _pinned(outro_text, config.outro_text)
        self.outro_duration = float(_pinned(outro_duration, config.outro_duration))
        # Text only: the image watermark needs a server-side file and has no
        # upload path in this edition, so it is not forwarded.
        self.watermark_text = _pinned(watermark_text, config.watermark_text)
        self.watermark_position = _pinned(watermark_position, config.watermark_position)
        self.watermark_opacity = float(_pinned(watermark_opacity, config.watermark_opacity))
        # The seconds of picture before each slide's narration; not a per-job
        # option yet, pinned here for the same reason as the rest.
        self.voice_start_delay = float(config.voice_start_delay)
        if subtitles not in SUBTITLE_MODES:
            raise ValueError(f"Unknown subtitle mode '{subtitles}'. Choose one of: {', '.join(SUBTITLE_MODES)}.")
        self.subtitles = subtitles
        # "" = the engine's recommended default, chosen when the pass runs.
        self.whisper_model = whisper_model or ""
        self.export_webm = bool(export_webm)
        self.export_gif = bool(export_gif)
        self.export_audio_only = bool(export_audio_only)
        # The sidecar artifacts of the most recent full render, as
        # {"srt"|"vtt"|"webm"|"gif"|"mp3": filename} - files beside the MP4.
        self.outputs: dict = {}
        # Which backend the most recent slide export of this run used
        # ("powerpoint", or "pillow" for the title-only fallback); None when
        # no slides were exported (images were already there). The generate
        # route records it on the project for the slide editor.
        self.images_backend: Optional[str] = None
        # How many sentences of the most recent re-voice could not be
        # synthesised. A failed sentence leaves a silent hole and used to be
        # visible only in the server log, so a whole line could vanish from the
        # narration with nothing to show for it; the re-voice job reads this
        # and reports it (``services.revoice``), the way the AI loops report
        # their own tally.
        self.failed_sentences = 0
        self.cancel_requested = False

    def _create_tts_generator(self):
        """Create this run's TTS generator via the provider factory."""
        from core.tts_provider import get_tts_provider
        return get_tts_provider(self.provider)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def process_files(
        self,
        files: List[FileItem],
        output_dir: Path,
        progress: Optional[ProgressCallback] = None,
        preview_seconds: float = 0,
    ) -> int:
        """Full generation pipeline for a list of files.

        Returns the number of successfully generated videos.
        If preview_seconds > 0, only generates enough for a short preview clip.
        """
        _ensure_temp_dir()
        audio_gen = self._create_tts_generator()
        total = len(files)
        successes = 0
        self.outputs = {}

        # Count total slides across all files for step-level ETA
        total_slides = sum(f.slide_count for f in files)
        slides_done = 0
        audio_start = None  # set lazily on first audio generation

        for idx, file_item in enumerate(files):
            if self.cancel_requested:
                break

            label = f"[{idx + 1}/{total}] {file_item.path.name}"

            if progress:
                progress(idx / total, f"{label}: Starting...")

            pm = self._get_or_create_project(file_item)

            # --- Export slides as images ---
            # Skip for video projects — keyframe images are already extracted
            has_images = all(
                s.image_path and Path(s.image_path).exists()
                for s in pm.state.slides
            )
            if has_images:
                logger.info("%s: Slide images already present, skipping export", label)
            else:
                if progress:
                    progress((idx + 0.1) / total, f"{label}: Exporting slides...")
                try:
                    # One slide export at a time in the process: PowerPoint COM
                    # is a single instance and two jobs run at once.
                    with slide_export_lock(progress, (idx + 0.1) / total, label):
                        exporter = PPTXExporter(file_item.path, pm.images_dir)
                        for exp in exporter.export_slides_as_images():
                            pm.update_slide_image(exp.index, exp.image_path)
                            if exp.video_path:
                                pm.update_slide_video(exp.index, exp.video_path, exp.has_animation)
                        self.images_backend = getattr(exporter, "backend", None)
                except Exception as e:
                    if progress:
                        progress(0, f"Error exporting slides: {e}")
                    continue

            # --- Generate audio (parallel) ---
            if progress:
                progress((idx + 0.3) / total, f"{label}: Generating audio...")

            slides_needing = pm.get_slides_needing_regeneration(
                current_speed=self.speed,
                current_voice_id=self.voice_id,
                current_stability=self.stability,
                current_similarity_boost=self.similarity_boost,
                current_style=self.style,
            )
            # For preview mode, only generate audio for slides within the time budget
            if preview_seconds > 0:
                budget = preview_seconds
                preview_slide_indices = set()
                for s in pm.state.slides:
                    dur = s.audio_duration if s.audio_duration > 0 else 5.0
                    if budget <= 0:
                        break
                    preview_slide_indices.add(s.index)
                    budget -= dur + self._pause_after(s)
                slides_needing = [i for i in slides_needing if i in preview_slide_indices]

            logger.info("provider=%s, speed=%s, voice=%s, stab=%s, sim=%s, style=%s", self.provider, self.speed, self.voice_id, self.stability, self.similarity_boost, self.style)
            is_video_revoice = file_item.path.suffix.lower() in VIDEO_SUFFIXES
            if is_video_revoice and preview_seconds <= 0 and slides_needing:
                # Re-voice projects synthesise per sentence inside the video
                # step (collect_revoice_segments); per-section audio here would
                # be wasted work and, for Kokoro, too long for a single call.
                logger.info("%s: re-voice project — per-section audio skipped "
                            "(synthesised per sentence during video creation)", label)
                slides_needing = []
            logger.info("slides_needing=%s", slides_needing)
            if slides_needing:
                if audio_start is None:
                    audio_start = time.time()
                logger.info("%s: Generating audio for %d slides...", label, len(slides_needing))
                self._generate_audio_parallel(
                    audio_gen, pm, slides_needing,
                    idx, total, label, progress,
                    step_start_ref=[audio_start],
                    slides_done_ref=[slides_done],
                    total_slides=total_slides,
                )
                logger.info("%s: Audio generation complete", label)
                try:
                    from services.enterprise import events
                    events.emit(events.AUDIO_GENERATED, file=file_item.path.name)
                except Exception:
                    pass
                pm.update_generation_settings(
                    speed=self.speed, voice_id=self.voice_id,
                    stability=self.stability, similarity_boost=self.similarity_boost,
                    style=self.style,
                )

            slides_done += file_item.slide_count

            if self.cancel_requested:
                break

            # Brief pause to let Windows release file locks on newly-written audio
            time.sleep(0.2)

            # --- Create video + SRT (music is mixed at the video level) ---
            is_preview = preview_seconds > 0
            logger.info("%s: Starting video creation...%s%s", label,
                        " (PREVIEW MODE)" if is_preview else "",
                        " (RE-VOICE)" if is_video_revoice else "")
            if progress:
                msg = f"{label}: {'Re-voicing' if is_video_revoice else 'Assembling'} {'preview' if is_preview else 'video'}..."
                progress((idx + 0.8) / total, msg)

            output_path = get_output_filename(file_item.path, output_dir)
            if is_preview:
                output_path = output_path.with_stem(output_path.stem + "_preview")

            if is_video_revoice and not is_preview:
                ok = self._revoice_video(pm, file_item.path, output_path, progress=progress, file_label=label)
            else:
                ok = self._build_video(pm, output_path, progress=progress, file_label=label, preview_seconds=preview_seconds)
            logger.info("%s: Video creation %s", label, 'succeeded' if ok else 'FAILED')

            if ok:
                if not is_preview:
                    self.outputs = self._sidecar_outputs(pm, output_path, progress, label, idx, total)
                pm.set_output_video(output_path)
                successes += 1
                try:
                    from services.enterprise import events
                    events.emit(events.VIDEO_GENERATED, file=file_item.path.name)
                except Exception:
                    pass
                if progress:
                    progress((idx + 1) / total, f"{label}: Complete -> {output_path.name}")
            else:
                if progress:
                    progress((idx + 1) / total, f"{label}: Failed to create video")

            file_item.project_manager = pm
            file_item.has_project = True

        return successes

    def prepare_audio(
        self,
        files: List[FileItem],
        progress: Optional[ProgressCallback] = None,
    ) -> int:
        """Export slides and generate audio only — no video assembly.

        Use this to populate the Timeline editor before generating the
        final video.  Returns the number of files successfully prepared.
        """
        _ensure_temp_dir()
        audio_gen = self._create_tts_generator()

        total = len(files)
        successes = 0
        total_slides = sum(f.slide_count for f in files)
        slides_done = 0
        audio_start = None

        for idx, file_item in enumerate(files):
            if self.cancel_requested:
                break

            label = f"[{idx + 1}/{total}] {file_item.path.name}"

            if progress:
                progress(idx / total, f"{label}: Starting...")

            pm = self._get_or_create_project(file_item)

            # --- Export slides as images ---
            has_images = all(
                s.image_path and Path(s.image_path).exists()
                for s in pm.state.slides
            )
            if has_images:
                logger.info("%s: Slide images already present, skipping export", label)
            else:
                if progress:
                    progress((idx + 0.1) / total, f"{label}: Exporting slides...")
                try:
                    with slide_export_lock(progress, (idx + 0.1) / total, label):
                        exporter = PPTXExporter(file_item.path, pm.images_dir)
                        for exp in exporter.export_slides_as_images():
                            pm.update_slide_image(exp.index, exp.image_path)
                            if exp.video_path:
                                pm.update_slide_video(exp.index, exp.video_path, exp.has_animation)
                        self.images_backend = getattr(exporter, "backend", None)
                except Exception as e:
                    if progress:
                        progress(0, f"Error exporting slides: {e}")
                    continue

            # --- Generate audio ---
            if progress:
                progress((idx + 0.3) / total, f"{label}: Generating audio...")

            slides_needing = pm.get_slides_needing_regeneration(
                current_speed=self.speed,
                current_voice_id=self.voice_id,
                current_stability=self.stability,
                current_similarity_boost=self.similarity_boost,
                current_style=self.style,
            )

            logger.info("Prepare audio: speed=%s, voice=%s", self.speed, self.voice_id)
            if slides_needing:
                if audio_start is None:
                    audio_start = time.time()
                logger.info("%s: Generating audio for %d slides...", label, len(slides_needing))
                self._generate_audio_parallel(
                    audio_gen, pm, slides_needing,
                    idx, total, label, progress,
                    step_start_ref=[audio_start],
                    slides_done_ref=[slides_done],
                    total_slides=total_slides,
                )
                pm.update_generation_settings(
                    speed=self.speed, voice_id=self.voice_id,
                    stability=self.stability, similarity_boost=self.similarity_boost,
                    style=self.style,
                )

            slides_done += file_item.slide_count
            file_item.project_manager = pm
            file_item.has_project = True
            successes += 1

            if progress:
                progress((idx + 1) / total, f"{label}: Audio ready")

        return successes

    def rebuild_files(
        self,
        files: List[FileItem],
        output_dir: Path,
        progress: Optional[ProgressCallback] = None,
    ) -> int:
        """Rebuild videos using cached audio — zero API calls.

        Returns the number of successfully rebuilt videos.
        """
        _ensure_temp_dir()
        total = len(files)
        successes = 0

        for idx, file_item in enumerate(files):
            if self.cancel_requested:
                break

            pm = file_item.project_manager
            label = f"[{idx + 1}/{total}] {file_item.path.name}"

            if not pm or not pm.state:
                continue

            if progress:
                progress(idx / total, f"{label}: Rebuilding...")

            # Re-export slides if images are missing
            if not pm.all_images_ready():
                if progress:
                    progress((idx + 0.2) / total, f"{label}: Re-exporting slides...")
                with slide_export_lock(progress, (idx + 0.2) / total, label):
                    exporter = PPTXExporter(file_item.path, pm.images_dir)
                    for exp in exporter.export_slides_as_images():
                        pm.update_slide_image(exp.index, exp.image_path)
                        if exp.video_path:
                            pm.update_slide_video(exp.index, exp.video_path, exp.has_animation)
                    self.images_backend = getattr(exporter, "backend", None)

            # Create video + SRT (music is mixed at the video level)
            if progress:
                progress((idx + 0.5) / total, f"{label}: Assembling video...")

            output_path = get_output_filename(file_item.path, output_dir)
            ok = self._build_video(pm, output_path, progress=progress, file_label=label)

            if ok:
                self.outputs = self._sidecar_outputs(pm, output_path, progress, label, idx, total)
                pm.set_output_video(output_path)
                successes += 1
                if progress:
                    progress((idx + 1) / total, f"{label}: Complete -> {output_path.name}")

        return successes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _pause_after(self, slide) -> float:
        """The pause after a slide of the project state: its own
        ``pause_override`` when set, else this job's transition pause."""
        return effective_pause(getattr(slide, "pause_override", None), self.transition_pause)

    def _sidecar_outputs(
        self, pm: ProjectManager, output_path: Path,
        progress: Optional[ProgressCallback], file_label: str,
        file_idx: int = 0, total_files: int = 1,
    ) -> dict:
        """The files written beside a finished MP4 for this job's options -
        subtitles (per slide, or a Whisper pass over the render) and the extra
        export formats. Returns ``{kind: filename}`` for every file produced;
        a failure in any of them is logged and leaves that kind out, the video
        itself is already done. Not run for previews.
        """
        outputs: dict = {}
        if self.subtitles == "slide":
            try:
                srt = _generate_srt(
                    pm, output_path,
                    transition_pause=self.transition_pause,
                    voice_start_delay=self.voice_start_delay,
                    intro_offset=self.intro_duration if self.intro_text else 0.0,
                )
                if srt:
                    outputs["srt"] = srt.name
            except Exception as e:
                logger.error("Per-slide subtitles failed: %s", e)
        elif self.subtitles == "whisper":
            def _on_progress(_fraction: float, message: str):
                if progress:
                    progress((file_idx + 0.99) / total_files, f"{file_label}: Whisper subtitles - {message}")

            outputs.update(_whisper_subtitles(output_path, self.whisper_model, on_progress=_on_progress))
        outputs.update(generate_extra_formats(
            output_path, webm=self.export_webm, gif=self.export_gif, audio_only=self.export_audio_only,
        ))
        return outputs

    def _get_or_create_project(self, file_item: FileItem) -> ProjectManager:
        """Load an existing project or create a new one.

        A project.json that exists but cannot be read raises
        ``ProjectStateError`` out of the job (``ProjectManager.load``): the
        project is never recreated from the deck over the user's edits.
        """
        # Use existing project manager if already set (e.g. video imports)
        if file_item.project_manager and file_item.project_manager.state:
            return file_item.project_manager
        projects_base = getattr(file_item, '_projects_base', None) or (CONFIG_DIR / "projects")
        project_dir = get_project_dir(file_item.path, projects_base)
        pm = ProjectManager(project_dir)

        if (project_dir / "project.json").exists():
            pm.load()

        # No project.json yet: create it from the deck.
        if pm.state is None:
            reader = file_item.reader
            slide_notes = (
                [reader.get_speaker_notes(i) for i in range(reader.slide_count)]
                if reader else [""] * file_item.slide_count
            )
            pm.create_project(
                pptx_path=file_item.path,
                slide_notes=slide_notes,
                voice_id=self.voice_id,
                transition_pause=self.transition_pause,
                background_music_path=self.background_music_paths[0] if self.background_music_paths else None,
                music_volume=config.music_volume,
            )
        return pm

    def _generate_audio_parallel(
        self,
        audio_gen: TTSProvider,
        pm: ProjectManager,
        slide_indices: List[int],
        file_idx: int,
        total_files: int,
        file_label: str,
        progress: Optional[ProgressCallback],
        step_start_ref: Optional[list] = None,
        slides_done_ref: Optional[list] = None,
        total_slides: int = 0,
    ):
        """Generate audio for multiple slides using a thread pool."""
        completed = 0
        total_to_gen = len(slide_indices)

        def generate_single(slide_idx: int):
            slide = pm.state.slides[slide_idx]
            text = slide.speaker_notes
            if not text.strip():
                slide.needs_regeneration = False
                return slide_idx, None

            # Use per-slide voice override only when it matches this run's
            # provider; otherwise fall back to the provider-correct global voice
            # so a stale Edge override under Kokoro (or vice versa) can't fail.
            voice = effective_voice(
                getattr(slide, 'voice_override', None),
                self.voice_id,
                self.provider,
            )

            audio_path = pm.audio_dir / f"slide_{slide_idx + 1:03d}_audio.mp3"
            result = None
            for attempt in range(3):
                if self.cancel_requested:
                    return slide_idx, None
                result = audio_gen.generate_audio(
                    text=text,
                    voice_id=voice,
                    output_path=audio_path,
                    speed=self.speed,
                    stability=self.stability,
                    similarity_boost=self.similarity_boost,
                    style=self.style,
                    use_cache=True,
                )
                if result:
                    break
                if attempt < 2 and not self.cancel_requested:
                    time.sleep(2 ** attempt * 3)  # 3s, 6s
            return slide_idx, result

        max_workers = min(2, total_to_gen)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(generate_single, idx): idx
                for idx in slide_indices
            }

            for future in as_completed(futures):
                if self.cancel_requested:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                slide_idx, result = future.result()
                completed += 1

                if result:
                    from core.audio_mixer import _load_audio_with_retry
                    audio = _load_audio_with_retry(result)
                    duration = len(audio) / 1000.0
                    pm.update_slide_audio(slide_idx, result, duration, self.voice_id)

                if progress:
                    frac = (file_idx + 0.3 + 0.4 * completed / total_to_gen) / total_files
                    # Build ETA from per-slide audio timing only
                    eta_str = ""
                    if step_start_ref and slides_done_ref and total_slides > 0:
                        done_so_far = slides_done_ref[0] + completed
                        elapsed = time.time() - step_start_ref[0]
                        if done_so_far > 0:
                            per_slide = elapsed / done_so_far
                            remaining_audio = (total_slides - done_so_far) * per_slide
                            eta_str = f" | Audio ETA: ~{_fmt_eta(remaining_audio)}"
                    progress(frac, f"{file_label}: Audio {completed}/{total_to_gen}{eta_str}")

    def _calibrate_tts_baseline(
        self, segments: list, tts_gen, tmp_dir: Path,
        progress=None, file_label: str = "",
    ) -> float:
        """This run's TTS baseline speaking rate - :func:`calibrate_tts_baseline`
        at this job's provider and voice.

        The measurement itself is module-level so the timeline's audition plan
        can ask for the same number without building a ``VideoProcessor``; this
        stays as the re-voice loop's call site (and the seam its tests patch).
        """
        return calibrate_tts_baseline(
            segments, tts_gen, tmp_dir,
            provider=self.provider, voice_id=self.voice_id,
            progress=progress, file_label=file_label,
        )

    @staticmethod
    def _per_sentence_speed(
        text: str, orig_duration: float, tts_baseline_rate: float, user_speed: float,
    ) -> float:
        """This sentence's synthesis speed - :func:`per_sentence_speed`.

        Kept as a method because it is the seam the sync tests patch to watch
        which window each sentence was measured against; the rule itself is
        module-level so the audition plan reports the speed the render will
        really use rather than a second implementation of it.
        """
        return per_sentence_speed(text, orig_duration, tts_baseline_rate, user_speed)

    def _revoice_video(
        self, pm: ProjectManager, source_video: Path, output_path: Path,
        progress: Optional[ProgressCallback] = None,
        file_label: str = "",
    ) -> bool:
        """Re-voice a video with sentence-level time alignment.

        Uses Whisper segment timestamps for fine-grained sync: each sentence
        is individually padded or sped to match the original timing.

        Each sentence may carry the per-sentence adjustments the narration
        editor writes onto the transcript (``services.narration``), which ride
        into the engine inside ``original_segments`` and reach here through
        ``collect_revoice_segments``: ``muted`` leaves it out (and gives its
        room to the sentence before it), ``offset`` moves where it is pinned,
        ``voice`` gives it its own voice when that voice belongs to this run's
        provider, and ``speed`` is an explicit rate that bypasses both
        ``_per_sentence_speed`` and the tempo squeeze. An older project carries
        none of them and behaves exactly as before.

        Sentences whose synthesis failed are counted in ``self.failed_sentences``
        for the job to report.
        """
        from core.video_creator import replace_video_audio, trim_leading_silence_segment, _level_opening, _probe_duration
        from core.tts_provider import get_onset_profile
        from pydub import AudioSegment
        import subprocess as _sp

        _onset_profile = get_onset_profile(self.provider)

        tmp_dir = _job_scratch("revoice_")

        try:
            # Per-sentence Whisper segments, except where the user edited a
            # section's notes — then the edited text is spoken for that window.
            all_segments = collect_revoice_segments(pm.state.slides)

            if not all_segments:
                logger.error("No timing segments for re-voicing")
                return False

            sync_mode = getattr(pm.state, 'revoice_sync_mode', 'synced')
            is_free = sync_mode == "free"
            logger.info("Re-voice: %d segments, mode=%s", len(all_segments), sync_mode)

            tts_gen = self._create_tts_generator()

            # The last sentence is bounded by the VIDEO's end, not by where the
            # speaker stopped: replace_video_audio pads the narration to the
            # video with apad and -shortest trims to the video length, so the
            # tail after the final segment is room the last sentence may use.
            # Squeezing it into the speaker's own slot would compress it for no
            # reason. A probe failure (no ffprobe in the packaged app) simply
            # leaves the section's own end as the bound.
            actual_source = source_video
            if pm.state.source_video_path and Path(pm.state.source_video_path).exists():
                actual_source = Path(pm.state.source_video_path)
            video_end = _probe_duration(actual_source) or 0.0

            tts_baseline = DEFAULT_BASELINE_RATE
            if not is_free:
                tts_baseline = self._calibrate_tts_baseline(
                    all_segments, tts_gen, tmp_dir, progress, file_label,
                )
                logger.info("TTS baseline: %.1f chars/s", tts_baseline)
            else:
                logger.info("Free pace mode — no calibration, speed=%.1f", self.speed)

            # Sentence-paced synthesis. Every sentence is pinned to the moment
            # it was spoken in the original, so the narration keeps step with
            # the picture the whole way through: the pause that followed a
            # sentence is that sentence's slack, and assemble_master turns
            # whatever it does not use back into silence — which is how an
            # original pause survives instead of being squeezed out. (Pinning
            # only the first sentence of a section and letting the rest run on
            # was fine for a deck, where a section is one slide, but a re-voiced
            # video is a single section: every pause in it was dropped and the
            # narration drifted further ahead of the picture with each sentence.)
            # A sentence whose synthesis overruns the moment the next one is due
            # is sped up a little at TTS time (<= +30%) and, if still too long,
            # tempo-adjusted so the next sentence still lands on time. In free
            # mode nothing is pinned and nothing is sped up.
            def _tempo(clip, factor: float, name: str):
                """Speed a clip up by ``factor`` with ffmpeg atempo (pitch kept)."""
                src = tmp_dir / f"{name}_src.mp3"
                dst = tmp_dir / f"{name}_fast.mp3"
                clip.export(str(src), format="mp3")
                _sp.run([
                    "ffmpeg", "-i", str(src), "-filter:a", f"atempo={factor:.4f}", "-y", str(dst),
                ], capture_output=True, timeout=60)
                return AudioSegment.from_file(str(dst)) if dst.exists() else clip

            sections = group_by_section(all_segments)
            aligned_chunks = []
            done = 0
            failed_sentences = 0
            # Every sentence to speak, in order, each with the section it came
            # from — the section only supplies the fallback bound for its last
            # sentence now that each one is pinned on its own.
            #
            # A MUTED sentence is dropped here, before any window maths, and
            # that placement is the whole point: the sentence before a muted
            # one inherits its room (the window runs to the next sentence that
            # is actually spoken), and the last unmuted sentence picks up the
            # "runs to the end of the video" rule below. Filtering later would
            # have left a hole that nothing could use.
            spoken = [
                (seg, sec) for sec in sections for seg in sec["segments"]
                if (seg.get("text") or "").strip() and not seg.get("muted")
            ]
            # Counted over what will actually be spoken, so the progress line
            # reaches its total instead of stopping at 8/10 on a transcript with
            # two muted sentences.
            total_segments = len(spoken)
            for i, (seg, sec) in enumerate(spoken):
                done += 1
                # Where this sentence is pinned and when the next one is due.
                # One helper, shared with the timeline's audition plan, so the
                # speed the plan reports is the speed this loop will use.
                seg_start, next_start = sentence_window(spoken, i, video_end)
                if video_end > 0 and seg_start >= video_end:
                    # replace_video_audio muxes with -shortest, so the narration
                    # is cut at the video's end: a sentence pinned at or past it
                    # is simply not in the render. Counted rather than dropped in
                    # silence - a sentence vanishing with no evidence is the
                    # thing the count exists to stop - and not synthesised,
                    # since nothing could hear it.
                    failed_sentences += 1
                    logger.warning(
                        "Sentence %d is pinned at %.1fs, at or past the end of the %.1fs video "
                        "- its offset puts it outside the render",
                        done, seg_start, video_end,
                    )
                    continue
                window_s = max(0.0, next_start - seg_start)
                window_ms = int(window_s * 1000)
                text = seg["text"].strip()
                # An explicit per-sentence speed wins outright: it skips the
                # per-sentence rule AND the squeeze further down. That is the
                # "nothing guesses a rate" rule made concrete - the user asked
                # for this length and gets it, and any overrun is absorbed at
                # the next real pause, where the following sentence is still
                # pinned to its own moment.
                explicit_speed = _explicit_speed(seg)
                # ``sentence_speed`` owns the precedence (explicit wins, else
                # the fitting rule over this sentence's window) and the audition
                # plan asks it the same question, so what the timeline says a
                # sentence will be spoken at is what this loop speaks it at.
                # Free mode is the one case that never reaches it: nothing is
                # pinned there, so there is no window to fit to - but an
                # explicit speed still wins even in free mode, as it always has.
                speed = (
                    self.speed if (is_free and explicit_speed is None)
                    else sentence_speed(
                        seg, text, window_s, tts_baseline, self.speed,
                        rule=self._per_sentence_speed,
                    )
                )
                # A per-sentence voice, honoured only when it belongs to this
                # run's provider - the same fallback the deck path relies on,
                # so a stale Edge id under Kokoro (or the reverse) cannot fail
                # the synthesis.
                seg_voice = effective_voice(seg.get("voice"), self.voice_id, self.provider)
                seg_audio_path = tmp_dir / f"seg_{done:04d}.mp3"
                try:
                    tts_gen.generate_audio(
                        text=text, voice_id=seg_voice,
                        output_path=seg_audio_path, speed=speed,
                    )
                except Exception as e:
                    # This sentence stays silent; the next one is pinned to
                    # its own moment, so nothing after it shifts. Counted, so
                    # the job can say how many sentences are missing instead of
                    # leaving the evidence in the server log alone.
                    failed_sentences += 1
                    logger.warning("TTS failed for segment %d: %s", done, e)
                    continue
                if not seg_audio_path.exists():
                    # The generator reported nothing and wrote nothing: the
                    # same silent hole, counted the same way.
                    failed_sentences += 1
                    logger.warning("TTS produced no audio for segment %d", done)
                    continue
                clip = AudioSegment.from_file(str(seg_audio_path))
                # Trim leading silence with the active provider's onset
                # profile (threshold + trim cap + micro fade) so a quiet
                # Kokoro clip is not wiped out by a hardcoded threshold,
                # then lift the soft opening ramp to body level so the
                # start of every sentence does not read as a fade-in
                # (matches the deck path; without it Edge's ~20 ms ramp
                # sounds like each sentence fades in).
                clip = trim_leading_silence_segment(clip, profile=_onset_profile)
                clip = _level_opening(clip, profile=_onset_profile)

                # Squeeze only a sentence that would genuinely run over the
                # moment the next one is due. The tolerance is deliberately
                # loose: Whisper often reports one sentence ending exactly where
                # the next begins, so a tight bound would tempo-adjust most of a
                # clip and the differing factors would be audible as a wobble in
                # the speaking rate. A small overrun is inaudible on its own and
                # is absorbed at the next real pause, where the sentence after
                # it is still pinned to its own moment.
                #
                # A sentence with an explicit speed is never squeezed: the user
                # named the rate, and tempo-adjusting it afterwards would undo
                # the one thing they asked for.
                last = i + 1 == len(spoken)
                if (not is_free and explicit_speed is None and window_ms > 0
                        and len(clip) > window_ms * SQUEEZE_TOLERANCE):
                    spoken_ms = len(clip)
                    factor = spoken_ms / window_ms
                    if factor <= SQUEEZE_MAX_FACTOR:
                        clip = _tempo(clip, factor, f"seg{done:04d}")
                        logger.info("Sentence %d: %.1fs of speech for a %.1fs window — tempo x%.2f",
                                    done, spoken_ms / 1000, window_s, factor)
                    else:
                        logger.warning(
                            "Sentence %d: %.1fs of speech for a %.1fs window — beyond 2x, it will %s",
                            done, spoken_ms / 1000, window_s,
                            "run past the end of the video and be trimmed there" if last
                            else "run into the next sentence",
                        )

                # Pinned to its own moment: assemble_master fills the time
                # since the previous sentence ended with silence, which is
                # how the original pause is reproduced rather than dropped.
                aligned_chunks.append((seg_start, None, clip))
                if done % 10 == 0 and progress:
                    pct = 0.8 + 0.15 * (done / max(total_segments, 1))
                    progress(pct, f"{file_label}: Synthesising sentence {done}/{total_segments}")

            self.failed_sentences = failed_sentences
            if failed_sentences:
                logger.warning(
                    "Re-voice: %d of %d sentences could not be synthesised",
                    failed_sentences, len(spoken),
                )

            if not aligned_chunks:
                logger.error("No aligned audio chunks")
                return False

            master = assemble_master(aligned_chunks, is_free)

            master_path = tmp_dir / "master_revoice.mp3"
            master.export(str(master_path), format="mp3")
            logger.info("Sentence-aligned master audio: %.1fs (%d segments)", len(master) / 1000, len(aligned_chunks))

            bg_music = None
            music_vol = 0.15
            if self.background_music_paths:
                bg = Path(self.background_music_paths[0])
                if bg.exists():
                    bg_music = bg
                    music_vol = config.music_volume

            ok = replace_video_audio(
                source_video=actual_source,
                master_audio=master_path,
                output_path=output_path,
                background_music=bg_music,
                music_volume=music_vol,
            )
            if ok:
                # Only once the mux succeeded, so a failed re-voice leaves no
                # stray track behind claiming to belong to a video that is not
                # there. A copy that fails is not worth failing the job over:
                # the video is already made.
                #
                # Written aside and moved into place, because the name is the
                # same every run: a second re-voice whose copy failed would
                # otherwise leave the FIRST run's track sitting there, and the
                # project would go on offering it - in the old voice, and the
                # old language - as the narration for the new video. A copy
                # that dies part way through would leave a truncated file the
                # record swears is good. So on any failure both the partial
                # file and any previous run's track are removed, and the
                # project simply has no narration track rather than a lying one.
                target = narration_path_for(output_path)
                partial = target.with_name(target.name + ".part")
                try:
                    shutil.copy2(master_path, partial)
                    partial.replace(target)
                except OSError as exc:
                    partial.unlink(missing_ok=True)
                    target.unlink(missing_ok=True)
                    logger.warning("Could not keep the narration track: %s", exc)
            return ok

        except Exception as e:
            logger.error("Re-voice failed: %s", e)
            return False
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _build_video(
        self, pm: ProjectManager, output_path: Path,
        progress: Optional[ProgressCallback] = None,
        file_label: str = "",
        preview_seconds: float = 0,
    ) -> bool:
        """Assemble slide images + audio into an MP4 file.

        Copies audio files to assets/temp so antivirus scanners
        don't hold locks on the originals while moviepy reads them.
        If preview_seconds > 0, only includes enough slides to fill
        the preview duration.
        """
        from core.tts_provider import get_onset_profile

        tmp_dir = _job_scratch("build_")
        try:
            clip_infos = []
            preview_budget = preview_seconds if preview_seconds > 0 else float("inf")
            if preview_seconds > 0:
                logger.info("-- PREVIEW MODE: %ss budget --", preview_seconds)
            logger.info("-- Slide / Audio alignment check --")
            logger.info("%3s  %-30s  %8s  %s", '#', 'Notes', 'Duration', 'Audio File')
            logger.info("%3s  %s  %s  %s", '---', '-'*30, '-'*8, '-'*40)
            for s in pm.state.slides:
                # In preview mode, stop adding slides once we've filled the time budget
                if preview_budget <= 0:
                    logger.info("(preview budget reached — skipping remaining slides)")
                    break

                audio_path = None
                if s.audio_path and Path(s.audio_path).exists():
                    dest = tmp_dir / f"slide_{s.index:03d}.mp3"
                    shutil.copy2(s.audio_path, dest)
                    audio_path = dest

                # Alignment log: slide number, notes preview, duration, audio file
                notes_preview = (s.speaker_notes or "")[:30].replace("\n", " ")
                audio_name = Path(s.audio_path).name if s.audio_path else "(none)"
                dur_str = f"{s.audio_duration:.1f}s" if s.audio_duration > 0 else "-"
                try:
                    logger.info("%3d  %-30s  %8s  %s", s.index + 1, notes_preview, dur_str, audio_name)
                except UnicodeEncodeError:
                    safe_preview = notes_preview.encode("ascii", "replace").decode("ascii")
                    logger.info("%3d  %-30s  %8s  %s", s.index + 1, safe_preview, dur_str, audio_name)

                # Use video clip for animated slides, image for static
                video_path = None
                if getattr(s, 'video_path', None) and Path(s.video_path).exists():
                    video_path = Path(s.video_path)

                clip_infos.append(SlideClipInfo(
                    slide_index=s.index,
                    image_path=Path(s.image_path) if s.image_path else None,
                    video_path=video_path,
                    audio_path=audio_path,
                    # The slide's own pause override (the editor's "Pause after
                    # slide"); None lets the creator use the job's pause.
                    pause_after=getattr(s, "pause_override", None),
                ))

                # Deduct this slide's duration from preview budget
                slide_dur = s.audio_duration if s.audio_duration > 0 else 5.0
                preview_budget -= slide_dur + self._pause_after(s) + self.voice_start_delay
            # Calculate total video duration from known slide durations + transitions
            default_dur = 5.0
            video_duration = 0.0
            for s in pm.state.slides:
                video_duration += (s.audio_duration if s.audio_duration > 0 else default_dur) + self.voice_start_delay
            # Add the pause after every slide but the last, and the title cards
            video_duration += sum(self._pause_after(s) for s in pm.state.slides[:-1])
            if self.intro_text:
                video_duration += self.intro_duration
            if self.outro_text:
                video_duration += self.outro_duration
            # A transition needs real frames to play on; a static deck stays at 2 fps.
            fps = fps_for_transition(self.slide_transition, self.transition_duration)
            total_frames = int(video_duration * fps)

            logger.info("-- %d slides ready for video --", len(clip_infos))
            logger.info("Est. video duration: %.1fs  (%d frames at %dfps)", video_duration, total_frames, fps)

            # Assembly progress — show which slide moviepy is processing
            def _on_assembly_progress(slide_num: int, total: int, msg: str):
                if progress:
                    frac = 0.80 + 0.05 * slide_num / max(total, 1)
                    progress(frac, f"{file_label}: Assembling slide {slide_num}/{total}")

            # Encoding progress callback — reports frame-level % to the UI
            encode_start = [0.0]  # mutable so closure can update it

            def _on_encode_progress(frame: int, total_from_logger: int):
                if not progress:
                    return
                # Use our pre-calculated total if the logger hasn't reported one yet
                total = total_from_logger if total_from_logger > 0 else total_frames
                if total <= 0:
                    return
                # Capture wall-clock start on first real frame callback
                if encode_start[0] == 0.0:
                    encode_start[0] = time.time()
                pct = min(100, int(frame / total * 100))
                elapsed = time.time() - encode_start[0]
                eta = ""
                if frame > 10 and elapsed > 0:
                    remaining = elapsed / frame * (total - frame)
                    eta = f" | ETA: ~{_fmt_eta(remaining)}"
                progress(0.85 + 0.14 * frame / total,
                         f"{file_label}: Encoding {pct}%{eta}")

            # This job's own options (pinned at construction), never the
            # shared config: two jobs render at once.
            creator = VideoCreator(
                resolution=self.resolution,
                video_bitrate=self.video_bitrate,
                fps=fps,
                # This run's provider, not the studio default: the clips were
                # synthesised by self.provider and must be trimmed as such.
                onset_profile=get_onset_profile(self.provider),
                transition_pause=self.transition_pause,
                transition_sound_path=self.transition_sound_path,
                background_music_paths=self.background_music_paths,
                music_volume=config.music_volume,
                watermark_text=self.watermark_text,
                watermark_image=None,
                watermark_position=self.watermark_position,
                watermark_opacity=self.watermark_opacity,
                slide_transition=self.slide_transition,
                transition_duration=self.transition_duration,
                intro_text=self.intro_text,
                intro_subtitle=self.intro_subtitle,
                intro_duration=self.intro_duration,
                outro_text=self.outro_text,
                outro_duration=self.outro_duration,
                voice_start_delay=self.voice_start_delay,
            )

            # Collect slide titles for chapter metadata
            slide_titles = []
            for s in pm.state.slides:
                title = f"Slide {s.index + 1}"
                if s.speaker_notes and s.speaker_notes.strip():
                    first_line = s.speaker_notes.strip().split("\n")[0][:60]
                    title = first_line
                slide_titles.append(title)

            return creator.create_video(
                slide_clips=clip_infos, output_path=output_path,
                progress_callback=_on_assembly_progress,
                encoding_callback=_on_encode_progress,
                cancel_check=lambda: self.cancel_requested,
                slide_titles=slide_titles,
            )
        finally:
            if tmp_dir.exists():
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:
                    pass


def _fmt_eta(seconds: float) -> str:
    """Format seconds into a human-readable ETA string."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


# ------------------------------------------------------------------
# SRT subtitle generation (stateless, so a module-level function)
# ------------------------------------------------------------------

def _generate_srt(
    pm: ProjectManager, video_path: Path, *,
    transition_pause: float, voice_start_delay: float = 0.0, intro_offset: float = 0.0,
) -> Optional[Path]:
    """Generate an SRT subtitle file alongside the video: one cue per slide.

    Walks slides in order, using actual audio durations for timing, the way
    the master track is laid out: ``intro_offset`` (the intro card, which has
    no narration) first, then per narrated slide ``voice_start_delay`` of
    silence before its audio, with ``transition_pause`` between slides - or
    a slide's own ``pause_override`` after that slide, as the master track
    and the chapters use it. Slides without notes are skipped but still
    advance the timeline. Returns the SRT path, or None when no slide had notes.
    """
    srt_path = video_path.with_suffix(".srt")
    lines: List[str] = []
    current_time = float(intro_offset)
    sub_index = 1

    for slide in pm.state.slides:
        pause = effective_pause(getattr(slide, "pause_override", None), transition_pause)
        if not slide.speaker_notes.strip():
            current_time += 5.0
            current_time += pause
            continue

        # The master track opens a narrated slide with the voice start delay;
        # a slide whose narration failed holds its 5 s with no delay.
        if slide.audio_duration > 0:
            duration = slide.audio_duration
            start = current_time + voice_start_delay
        else:
            duration = 5.0
            start = current_time
        end = start + duration

        lines.append(str(sub_index))
        lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
        lines.append(slide.speaker_notes.strip())
        lines.append("")
        sub_index += 1

        current_time = end + pause

    if not lines:
        return None
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    return srt_path


def _whisper_subtitles(video_path: Path, model: str = "", on_progress=None) -> dict:
    """Word-level subtitles (SRT + VTT) from a Whisper pass over the rendered
    MP4 - a second transcription, run after the render. Written as
    ``<stem>.whisper.srt`` / ``.vtt`` beside the video so they never overwrite
    the per-slide ``<stem>.srt``. ``model`` "" = the engine's recommended
    default. Returns ``{"srt": name, "vtt": name}``, or {} on failure (logged).
    """
    # Imported here: the Whisper engine is heavy and loads only when used.
    from core.subtitle_generator import generate_subtitles
    from core.video_importer import recommended_default_model

    tmp_dir = _job_scratch("subs_")
    try:
        result = generate_subtitles(
            video_path, tmp_dir,
            model_size=model or recommended_default_model(),
            on_progress=on_progress,
        )
        outputs = {}
        for kind in ("srt", "vtt"):
            target = video_path.with_name(f"{video_path.stem}.whisper.{kind}")
            target.unlink(missing_ok=True)  # an earlier render's
            shutil.move(str(result[kind]), str(target))
            outputs[kind] = target.name
        logger.info("Whisper subtitles: %s, %s", outputs["srt"], outputs["vtt"])
        return outputs
    except Exception as e:
        logger.error("Whisper subtitles failed: %s", e)
        return {}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _srt_time(seconds: float) -> str:
    """Convert seconds to SRT format HH:MM:SS,mmm."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_extra_formats(video_path: Path, *, webm: bool = False, gif: bool = False, audio_only: bool = False) -> dict:
    """Generate additional export formats (WebM, GIF, audio MP3) from the MP4.

    The flags are this job's own (never the shared config). Uses ffmpeg
    directly. Returns ``{"webm"|"gif"|"mp3": filename}`` for every file
    produced; a format that fails is logged and left out.
    """
    from utils.config import FFMPEG_PATH
    outputs: dict = {}
    if not FFMPEG_PATH or not video_path.exists():
        return outputs

    import subprocess as sp

    flags = 0x08000000  # CREATE_NO_WINDOW on Windows

    # WebM
    if webm:
        webm_path = video_path.with_suffix(".webm")
        try:
            sp.run(
                [FFMPEG_PATH, "-i", str(video_path), "-c:v", "libvpx-vp9",
                 "-crf", "30", "-b:v", "0", "-c:a", "libopus", "-y", str(webm_path)],
                capture_output=True, timeout=600, creationflags=flags,
            )
            if webm_path.exists():
                outputs["webm"] = webm_path.name
                logger.info("WebM: %s", webm_path)
        except Exception as e:
            logger.error("WebM failed: %s", e)

    # GIF (first 30 seconds, scaled down)
    if gif:
        gif_path = video_path.with_suffix(".gif")
        try:
            sp.run(
                [FFMPEG_PATH, "-i", str(video_path), "-t", "30",
                 "-vf", "fps=5,scale=480:-1:flags=lanczos",
                 "-y", str(gif_path)],
                capture_output=True, timeout=300, creationflags=flags,
            )
            if gif_path.exists():
                outputs["gif"] = gif_path.name
                logger.info("GIF: %s", gif_path)
        except Exception as e:
            logger.error("GIF failed: %s", e)

    # Audio-only MP3
    if audio_only:
        mp3_path = video_path.with_stem(video_path.stem + "_audio").with_suffix(".mp3")
        try:
            sp.run(
                [FFMPEG_PATH, "-i", str(video_path), "-vn",
                 "-acodec", "libmp3lame", "-q:a", "2", "-y", str(mp3_path)],
                capture_output=True, timeout=300, creationflags=flags,
            )
            if mp3_path.exists():
                outputs["mp3"] = mp3_path.name
                logger.info("Audio MP3: %s", mp3_path)
        except Exception as e:
            logger.error("Audio MP3 failed: %s", e)

    return outputs
