"""The per-job generation options inside the engine (services/processing.py and
core/video_creator.py): a job carries its own transition, cards, watermark,
subtitle mode and export flags (pinned at construction, never read from the
shared config mid-run - two jobs render at once), forwards them to the
VideoCreator with a frame rate the transition can play on, offsets everything
timed against the slides by the intro card, and writes only the sidecar files
it was asked for.

No slides are rendered and no encode runs: the VideoCreator, ffmpeg, pydub and
the Whisper pass are captured or stubbed.
"""

import shutil
import subprocess
import sys
import tempfile
import types
import wave
from pathlib import Path
from typing import get_args

import pytest

import utils.config as config_module
from api import schemas
from core import subtitle_generator, video_creator, video_importer
from core.video_creator import SlideClipInfo, VideoCreator
from services import processing, studio_settings
from utils.config import config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")


def _pm(*slides):
    """A ProjectManager stand-in: ``slides`` are (speaker_notes, audio_duration)."""
    return types.SimpleNamespace(state=types.SimpleNamespace(slides=[
        types.SimpleNamespace(index=i, speaker_notes=notes, audio_duration=duration,
                              audio_path=None, image_path=None, video_path=None)
        for i, (notes, duration) in enumerate(slides)
    ]))


def _capture_creator(monkeypatch):
    seen = {}

    class _FakeCreator:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def create_video(self, **kwargs):
            return True

    monkeypatch.setattr(processing, "VideoCreator", _FakeCreator)
    return seen


def _fake_ffmpeg(monkeypatch, path="ffmpeg-test", fail_when=None):
    """Capture ffmpeg commands; the output file (the last argument) is created."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if fail_when and fail_when(cmd):
            raise OSError("ffmpeg crashed")
        Path(cmd[-1]).write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", path)
    return calls


# -- the request enums and the studio lists are one list ---------------------------

def test_the_request_enums_match_the_engine_lists():
    assert set(get_args(schemas.Transition)) == set(studio_settings.TRANSITIONS)
    assert set(get_args(schemas.WatermarkPosition)) == set(studio_settings.WATERMARK_POSITIONS)
    assert set(get_args(schemas.SubtitleMode)) == set(processing.SUBTITLE_MODES)


# -- VideoProcessor: per-job options, pinned at construction -----------------------

def test_processor_pins_the_config_defaults_at_construction():
    config._config.update({
        "slide_transition": "zoom-in", "transition_duration": 1.5, "transition_pause": 2.0,
        "intro_text": "Hi", "intro_subtitle": "there", "intro_duration": 4.0,
        "outro_text": "Bye", "outro_duration": 2.0,
        "watermark_text": "Brand", "watermark_position": "center", "watermark_opacity": 0.9,
        "voice_start_delay": 0.5,
    })
    p = processing.VideoProcessor()
    assert (p.slide_transition, p.transition_duration, p.transition_pause) == ("zoom-in", 1.5, 2.0)
    assert (p.intro_text, p.intro_subtitle, p.intro_duration) == ("Hi", "there", 4.0)
    assert (p.outro_text, p.outro_duration) == ("Bye", 2.0)
    assert (p.watermark_text, p.watermark_position, p.watermark_opacity) == ("Brand", "center", 0.9)
    assert p.voice_start_delay == 0.5
    assert p.subtitles == "slide" and p.whisper_model == ""
    assert (p.export_webm, p.export_gif, p.export_audio_only) == (False, False, False)
    assert p.outputs == {}

    # Changed after construction: the job keeps what it started with.
    config._config.update({"slide_transition": "none", "watermark_text": "", "transition_pause": 0.0})
    assert p.slide_transition == "zoom-in" and p.watermark_text == "Brand" and p.transition_pause == 2.0


def test_processor_takes_explicit_options_over_the_config():
    config._config.update({"slide_transition": "zoom-in", "transition_pause": 2.0, "watermark_text": "Brand"})
    p = processing.VideoProcessor(
        slide_transition="crossfade", transition_pause=0, watermark_text="", subtitles="none",
        whisper_model="tiny", export_gif=True, intro_text="Welcome", intro_duration=5,
    )
    assert p.slide_transition == "crossfade"
    assert p.transition_pause == 0.0 and isinstance(p.transition_pause, float)
    assert p.watermark_text == "", "an explicit empty text is no watermark, not the config's"
    assert p.subtitles == "none" and p.whisper_model == "tiny" and p.export_gif is True
    assert p.intro_text == "Welcome" and p.intro_duration == 5.0

    with pytest.raises(ValueError) as exc:
        processing.VideoProcessor(subtitles="burned")
    assert "Unknown subtitle mode 'burned'" in str(exc.value)


def test_build_video_forwards_the_jobs_options_to_the_video_creator(tmp_path, monkeypatch):
    seen = _capture_creator(monkeypatch)
    config._config.update({"slide_transition": "fade-to-black", "watermark_text": "config brand",
                           "watermark_image": str(tmp_path / "logo.png"), "voice_start_delay": 0.25})

    p = processing.VideoProcessor(
        slide_transition="slide-left", transition_duration=0.8, transition_pause=1.0,
        intro_text="Welcome", intro_subtitle="Q3", intro_duration=4.0,
        outro_text="Bye", outro_duration=2.0,
        watermark_text="ACME", watermark_position="top-left", watermark_opacity=0.3,
    )
    assert p._build_video(_pm(), tmp_path / "out.mp4")

    assert seen["slide_transition"] == "slide-left" and seen["transition_duration"] == 0.8
    assert seen["transition_pause"] == 1.0
    assert (seen["intro_text"], seen["intro_subtitle"], seen["intro_duration"]) == ("Welcome", "Q3", 4.0)
    assert (seen["outro_text"], seen["outro_duration"]) == ("Bye", 2.0)
    assert (seen["watermark_text"], seen["watermark_position"], seen["watermark_opacity"]) == ("ACME", "top-left", 0.3)
    assert seen["watermark_image"] is None, "text only: the image watermark is not offered"
    assert seen["voice_start_delay"] == 0.25
    assert seen["fps"] == video_creator.TRANSITION_FPS, "a transition needs real frames"

    p = processing.VideoProcessor(slide_transition="none")
    assert p._build_video(_pm(), tmp_path / "out.mp4")
    assert seen["fps"] == video_creator.STATIC_FPS, "a static deck stays cheap to encode"


def test_a_deck_with_an_animated_slide_renders_at_the_transition_rate(tmp_path, monkeypatch):
    """The whole picture is sampled at one rate: at 2 fps an animated slide
    played as two pictures a second (every static deck with one did, before
    T1), so a deck with an animated slide renders at 24 fps, transition or
    not."""
    seen = _capture_creator(monkeypatch)
    clip = tmp_path / "anim.mp4"
    clip.write_bytes(b"mp4")
    pm = _pm(("A", 2.0), ("B", 2.0))
    pm.state.slides[1].video_path = str(clip)
    assert processing.VideoProcessor(slide_transition="none")._build_video(pm, tmp_path / "out.mp4")
    assert seen["fps"] == video_creator.TRANSITION_FPS
    pm.state.slides[1].video_path = str(tmp_path / "gone.mp4")
    assert processing.VideoProcessor(slide_transition="none")._build_video(pm, tmp_path / "out.mp4")
    assert seen["fps"] == video_creator.STATIC_FPS, "a clip that is not there animates nothing"


def test_fps_for_transition():
    assert video_creator.fps_for_transition("none") == 2
    assert video_creator.fps_for_transition("") == 2
    assert video_creator.fps_for_transition("crossfade", 0.0) == 2, "the same gate as create_video"
    for transition in list(studio_settings.TRANSITIONS)[1:]:
        assert video_creator.fps_for_transition(transition, 0.5) == 24, transition
        assert video_creator.deck_fps(transition, 0.5) == 24, transition
    assert VideoCreator().fps == video_creator.STATIC_FPS
    assert video_creator.deck_fps("none", 0.5, animated=True) == 24, "an animated slide needs the frames too"


# -- every job step scratches in its own directory ----------------------------------

def _spy_mkdtemp(monkeypatch):
    made = []
    real = tempfile.mkdtemp

    def spy(**kwargs):
        path = real(**kwargs)
        made.append(Path(path))
        return path

    monkeypatch.setattr(processing.tempfile, "mkdtemp", spy)
    return made


def test_build_and_whisper_scratch_in_their_own_dirs_and_leave_other_jobs_alone(tmp_path, monkeypatch):
    """Two jobs render at once: the end-of-job sweep of the whole assets/temp
    used to wipe the other worker's build in progress."""
    _capture_creator(monkeypatch)
    _fake_whisper(monkeypatch)
    made = _spy_mkdtemp(monkeypatch)
    other = tmp_path / "temp" / "build_other-job"
    other.mkdir(parents=True)
    (other / "slide_000.mp3").write_bytes(b"theirs")
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")

    assert processing.VideoProcessor()._build_video(_pm(), video)
    processing.VideoProcessor(subtitles="whisper")._sidecar_outputs(_pm(("Notes", 1.0)), video, None, "x")

    assert len(made) == 2 and made[0].name.startswith("build_") and made[1].name.startswith("subs_")
    assert all(p.parent == tmp_path / "temp" for p in made), "under assets/temp"
    assert not any(p.exists() for p in made), "each step removed its own scratch"
    assert (other / "slide_000.mp3").read_bytes() == b"theirs", "the other job's scratch is untouched"


class _StubPM:
    """A ProjectManager whose slides are all rendered already (nothing to
    export or narrate), enough for process_files to reach the video step."""

    def __init__(self):
        self.state = types.SimpleNamespace(slides=[])
        self.images_dir = None
        self.output = None

    def get_slides_needing_regeneration(self, **kwargs):
        return []

    def update_generation_settings(self, **kwargs):
        pass

    def set_output_video(self, path):
        self.output = path


def test_process_files_never_sweeps_the_shared_scratch_dir(tmp_path, monkeypatch):
    _capture_creator(monkeypatch)
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: None)
    monkeypatch.setattr(processing.VideoProcessor, "_get_or_create_project", lambda self, fi: _StubPM())
    other = tmp_path / "temp" / "build_other-job"
    other.mkdir(parents=True)
    fi = types.SimpleNamespace(path=tmp_path / "deck.pptx", slide_count=0, project_manager=None, has_project=False)

    assert processing.VideoProcessor(subtitles="none").process_files([fi], output_dir=tmp_path / "out") == 1
    assert other.is_dir(), "another worker's build dir survived this job's end"
    assert fi.project_manager.output == tmp_path / "out" / "deck.mp4"


# -- each slide transition moves as its name says --------------------------------

def _offset(expression: str, t: float) -> int:
    """An ffmpeg offset expression of the slide-in (``trunc``, ``clip``,
    ``t``) evaluated at time ``t``."""
    import math

    return int(eval(expression, {"__builtins__": {}}, {  # noqa: S307 - our own expression, in a test
        "trunc": math.trunc, "clip": lambda x, lo, hi: min(max(x, lo), hi), "t": t,
    }))


@pytest.mark.parametrize("kind, edge, pad, axis", [
    ("slide-left", "right", "pad=w=16:h=4:x=8:y=0", "x"),
    ("slide-right", "left", "pad=w=16:h=4:x=0:y=0", "x"),
    ("slide-up", "bottom", "pad=w=8:h=8:x=0:y=4", "y"),
    ("slide-down", "top", "pad=w=8:h=8:x=0:y=0", "y"),
])
def test_each_slide_transition_enters_from_the_edge_its_name_says(kind, edge, pad, axis):
    """The incoming slide is padded with black on the side it moves away from
    and cut back to the frame by a moving window: half way through, the
    window is half a frame along - shifted ONCE (moviepy's old horizontal
    transform was once applied twice) - and it ends on the slide itself.
    Before T1, slide-left drew as slide-right and slide-up as slide-down."""
    creator = VideoCreator(resolution=(8, 4), slide_transition=kind, transition_duration=0.5)
    (_, segment, _) = [s for s in creator.deck_segments(
        [SlideClipInfo(slide_index=i) for i in range(3)], [5.0, 5.0, 5.0], [0.0, 0.0, 0.0]) if s.kind == "slide"]
    assert segment.effect_in == f"slide-from-{edge}"
    # The transition's clock is the slide's own time plus a shift (never
    # negative: ``tests/test_deck_render.py``), so 0 of the slide is at ``shift``.
    shift = video_creator.transition_clock_shift(24)
    drawn = video_creator._transition_filter(segment.effect_in, True, segment, (8, 4), shift)
    assert drawn.startswith(pad + ":color=black,crop=w=8:h=4:"), drawn
    expression = drawn.split(f"{axis}='")[1].split("'")[0]
    size = 8 if axis == "x" else 4
    start, half, done = (_offset(expression, shift + t) for t in (0.0, 0.25, 0.5))
    assert _offset(expression, shift - 0.01) == _offset(expression, shift), "before its start: not yet moving"
    if edge in ("right", "bottom"):          # the window starts on the black and slides onto the slide
        assert (start, half, done) == (0, size // 2, size)
    else:                                    # the window starts past the slide and slides back onto it
        assert (start, half, done) == (size, size // 2, 0)


# -- the intro card offsets everything timed against the slides ----------------------

def test_video_creator_knows_its_intro_offset():
    assert VideoCreator(intro_text="Welcome", intro_duration=4.0).intro_offset == 4.0
    assert VideoCreator(intro_duration=4.0).intro_offset == 0.0, "no text, no card"
    assert VideoCreator().intro_offset == 0.0


class _Seg:
    """A pydub.AudioSegment stand-in whose export records its length."""

    def __init__(self, ms=0):
        self.ms = ms

    @classmethod
    def silent(cls, duration=0):
        return cls(duration)

    @classmethod
    def empty(cls):
        return cls(0)

    @classmethod
    def from_file(cls, path):
        return cls(1500)

    def __add__(self, other):
        return _Seg(self.ms + other.ms)

    def __len__(self):
        return self.ms

    def export(self, path, **kwargs):
        Path(path).write_text(str(self.ms))


def _stub_audio(monkeypatch):
    fake = types.ModuleType("pydub")
    fake.AudioSegment = _Seg
    monkeypatch.setitem(sys.modules, "pydub", fake)
    monkeypatch.setattr(video_creator, "_trim_leading_silence", lambda path, profile=None: path)
    monkeypatch.setattr(video_creator, "_level_opening", lambda audio, profile=None: audio)


def _two_clips(tmp_path):
    clips = []
    for i in range(2):
        audio = tmp_path / f"slide_{i:03d}.mp3"
        audio.write_bytes(b"mp3")
        clips.append(SlideClipInfo(slide_index=i, audio_path=audio))
    return clips


def test_master_audio_opens_with_the_intro_cards_silence(tmp_path, monkeypatch):
    _stub_audio(monkeypatch)
    config._config["tts_provider"] = "edge_tts"

    master, durations = video_creator._build_master_audio(
        _two_clips(tmp_path), voice_start_delay=0.5, transition_pause=1.0, intro_offset=3.0,
    )
    try:
        assert durations == [2.0, 2.0], "the slides' own on-screen times are unchanged"
        # 3 s of intro silence, then delay + clip, the pause, delay + clip.
        assert int(master.read_text()) == 3000 + 2000 + 1000 + 2000
    finally:
        master.unlink(missing_ok=True)

    master, _ = video_creator._build_master_audio(_two_clips(tmp_path), voice_start_delay=0.5, transition_pause=1.0)
    try:
        assert int(master.read_text()) == 5000, "no intro card: no leading silence"
    finally:
        master.unlink(missing_ok=True)


def test_create_video_hands_the_intro_offset_to_the_master_track(tmp_path, monkeypatch):
    seen = {}

    def fake_master(slide_clips, voice_start_delay, transition_pause, transition_sound_path=None,
                    profile=None, intro_offset=0.0, gaps_out=None):
        seen["intro_offset"] = intro_offset
        return None, []

    monkeypatch.setattr(video_creator, "_build_master_audio", fake_master)
    creator = VideoCreator(intro_text="Welcome", intro_duration=4.0)
    # No clips: create_video stops right after the master track (the part under test).
    assert creator.create_video(slide_clips=[], output_path=tmp_path / "x.mp4") is False
    assert seen["intro_offset"] == 4.0


def test_chapter_spans_follow_the_intro_card_and_the_pauses():
    assert video_creator.chapter_spans([2.0, 1.5], transition_pause=0.5, intro_offset=3.0) == [(3000, 5000), (5500, 7000)]
    assert video_creator.chapter_spans([2.0, 1.5], transition_pause=0.5) == [(0, 2000), (2500, 4000)]
    assert video_creator.chapter_spans([], 0.5, 3.0) == []


def test_embedded_chapters_start_after_the_intro_card(tmp_path, monkeypatch):
    metadata = {}

    def fake_run(cmd, **kwargs):
        # [ffmpeg, -i, video, -i, metadata, ...]: keep the chapter file before it is cleaned up.
        metadata["text"] = Path(cmd[4]).read_text(encoding="utf-8")
        return types.SimpleNamespace(returncode=1, stderr=b"not really ffmpeg")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")

    creator = VideoCreator(intro_text="Welcome", intro_duration=3.0, transition_pause=0.5, voice_start_delay=0.25)
    clips = [SlideClipInfo(slide_index=0, duration=2.0), SlideClipInfo(slide_index=1, duration=1.5)]
    creator._embed_chapters(video, clips, ["Opening", "Closing"])

    assert "START=3000\nEND=5000\ntitle=Opening" in metadata["text"]
    assert "START=5500\nEND=7000\ntitle=Closing" in metadata["text"]
    assert not video.with_suffix(".chapters.txt").exists()


# -- the per-slide SRT ------------------------------------------------------------

def test_slide_srt_is_offset_by_the_intro_card_the_voice_delay_and_the_pause(tmp_path):
    pm = _pm(("First slide.", 2.0), ("", 0.0), ("Third slide.", 1.5))
    srt = processing._generate_srt(pm, tmp_path / "deck.mp4",
                                   transition_pause=0.5, voice_start_delay=0.25, intro_offset=3.0)
    assert srt == tmp_path / "deck.srt"
    text = srt.read_text(encoding="utf-8")
    # 3 s intro, 0.25 s delay, 2 s narration | 0.5 s pause | 5 s silent slide | 0.5 s pause | 0.25 s delay ...
    assert "1\n00:00:03,250 --> 00:00:05,250\nFirst slide." in text
    assert "2\n00:00:11,500 --> 00:00:13,000\nThird slide." in text
    assert text.count("-->") == 2, "a slide without notes gets no cue"


def test_slide_srt_without_offsets_is_todays_layout(tmp_path):
    srt = processing._generate_srt(_pm(("A", 2.0), ("B", 1.0)), tmp_path / "deck.mp4", transition_pause=1.0)
    text = srt.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,000" in text
    assert "00:00:03,000 --> 00:00:04,000" in text


def test_slide_srt_is_not_written_without_notes(tmp_path):
    assert processing._generate_srt(_pm(("", 0.0)), tmp_path / "deck.mp4", transition_pause=0.5) is None
    assert not (tmp_path / "deck.srt").exists()


def test_slide_srt_adds_the_voice_delay_only_to_narrated_slides(tmp_path):
    """A slide with notes whose narration failed holds 5 s with no delay in
    the master track; its cue and every later cue must not drift by the delay."""
    pm = _pm(("Narrated.", 2.0), ("No audio.", 0.0), ("Narrated too.", 1.0))
    srt = processing._generate_srt(pm, tmp_path / "deck.mp4", transition_pause=0.5, voice_start_delay=1.0)
    text = srt.read_text(encoding="utf-8")
    assert "00:00:01,000 --> 00:00:03,000\nNarrated." in text
    assert "00:00:03,500 --> 00:00:08,500\nNo audio." in text, "3.0 + the pause, no delay, 5 s"
    assert "00:00:10,000 --> 00:00:11,000\nNarrated too." in text, "8.5 + the pause + the delay"


def test_a_failed_slide_srt_leaves_the_video_and_the_other_outputs(tmp_path, monkeypatch):
    _fake_ffmpeg(monkeypatch)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(processing, "_generate_srt", boom)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    p = processing.VideoProcessor(subtitles="slide", export_gif=True)
    assert p._sidecar_outputs(_pm(("Notes", 2.0)), video, None, "x") == {"gif": "deck.gif"}
    assert video.exists()


# -- extra formats: explicit flags, never the config ---------------------------------

def test_extra_formats_run_only_the_requested_ones(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")

    assert processing.generate_extra_formats(video) == {}
    assert calls == []

    out = processing.generate_extra_formats(video, webm=True, audio_only=True)
    assert out == {"webm": "deck.webm", "mp3": "deck_audio.mp3"}
    assert [c[0] for c in calls] == ["ffmpeg-test", "ffmpeg-test"]
    assert "libvpx-vp9" in calls[0] and calls[0][-1].endswith("deck.webm")
    assert "-vn" in calls[1] and calls[1][-1].endswith("deck_audio.mp3")
    assert not (tmp_path / "deck.gif").exists()

    out = processing.generate_extra_formats(video, gif=True)
    assert out == {"gif": "deck.gif"}
    assert calls[2][calls[2].index("-t") + 1] == "30", "the first 30 s"

    # The old config flags are not consulted any more.
    config._config.update({"export_webm": True, "export_gif": True, "export_audio_only": True})
    assert processing.generate_extra_formats(video) == {}
    assert len(calls) == 3


def test_extra_formats_need_ffmpeg_and_the_video(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, path=None)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    assert processing.generate_extra_formats(video, webm=True, gif=True, audio_only=True) == {}
    calls = _fake_ffmpeg(monkeypatch)
    assert processing.generate_extra_formats(tmp_path / "missing.mp4", webm=True) == {}
    assert calls == []


def test_a_failed_format_is_left_out_and_the_rest_still_run(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, fail_when=lambda cmd: "libvpx-vp9" in cmd)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    assert processing.generate_extra_formats(video, webm=True, audio_only=True) == {"mp3": "deck_audio.mp3"}
    assert len(calls) == 2


# -- subtitles: none | slide | whisper -------------------------------------------------

def _fake_whisper(monkeypatch, fail=False):
    seen = {}

    def fake_generate_subtitles(audio_path, output_dir, model_size="medium", language=None, on_progress=None):
        if fail:
            raise RuntimeError("no model")
        seen.update(audio=Path(audio_path), model=model_size)
        if on_progress:
            on_progress(0.5, "Transcribing... 5s / 10s")
        srt = Path(output_dir) / f"{Path(audio_path).stem}.srt"
        vtt = Path(output_dir) / f"{Path(audio_path).stem}.vtt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
        vtt.write_text("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nhello\n", encoding="utf-8")
        return {"srt": srt, "vtt": vtt, "segments": []}

    monkeypatch.setattr(subtitle_generator, "generate_subtitles", fake_generate_subtitles)
    monkeypatch.setattr(video_importer, "recommended_default_model", lambda: "base")
    return seen


def test_whisper_subtitles_get_distinct_names_and_the_jobs_model(tmp_path, monkeypatch):
    seen = _fake_whisper(monkeypatch)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    stale = tmp_path / "deck.srt"
    stale.write_text("per-slide cues from an earlier render", encoding="utf-8")
    messages = []

    p = processing.VideoProcessor(subtitles="whisper", whisper_model="small")
    outputs = p._sidecar_outputs(_pm(("Notes", 2.0)), video, lambda f, m: messages.append((f, m)), "[1/1] deck.pptx")

    assert outputs == {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt"}
    assert (tmp_path / "deck.whisper.srt").read_text(encoding="utf-8").startswith("1\n00:00:00,000")
    assert (tmp_path / "deck.whisper.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    assert stale.read_text(encoding="utf-8").startswith("per-slide"), "the per-slide SRT is never overwritten"
    assert seen == {"audio": video, "model": "small"}, "the render itself is transcribed, with the job's model"
    assert messages and "Whisper subtitles" in messages[-1][1] and "Transcribing" in messages[-1][1]
    assert list((tmp_path / "temp").glob("subs_*")) == [], "the scratch dir is removed"

    processing.VideoProcessor(subtitles="whisper")._sidecar_outputs(_pm(("Notes", 2.0)), video, None, "x")
    assert seen["model"] == "base", '"" = the engine\'s recommended default'


def test_subtitles_none_writes_nothing(tmp_path, monkeypatch):
    _fake_whisper(monkeypatch)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    p = processing.VideoProcessor(subtitles="none")
    assert p._sidecar_outputs(_pm(("Notes", 2.0)), video, None, "x") == {}
    assert sorted(f.name for f in tmp_path.iterdir()) == ["deck.mp4"]


def test_subtitles_slide_writes_the_per_slide_srt_only(tmp_path, monkeypatch):
    _fake_whisper(monkeypatch)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    config._config["voice_start_delay"] = 0.0  # DEFAULT_CONFIG's value (the property alone falls back to 1.0)
    p = processing.VideoProcessor(subtitles="slide", transition_pause=0.0, intro_text="Welcome", intro_duration=2.0)
    assert p._sidecar_outputs(_pm(("Notes", 2.0)), video, None, "x") == {"srt": "deck.srt"}
    assert sorted(f.name for f in tmp_path.iterdir()) == ["deck.mp4", "deck.srt"]
    assert "00:00:02,000 --> 00:00:04,000" in (tmp_path / "deck.srt").read_text(encoding="utf-8"), "after the intro card"


def test_a_failed_whisper_pass_leaves_the_video_and_the_other_outputs(tmp_path, monkeypatch):
    _fake_whisper(monkeypatch, fail=True)
    _fake_ffmpeg(monkeypatch)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    p = processing.VideoProcessor(subtitles="whisper", export_gif=True)
    assert p._sidecar_outputs(_pm(("Notes", 2.0)), video, None, "x") == {"gif": "deck.gif"}
    assert video.exists()


# -- the Whisper pass extracts audio with the resolved ffmpeg and cleans up ------------

def test_extract_audio_uses_the_resolved_ffmpeg_and_refuses_without_one(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, path="C:/tools/ffmpeg.exe")
    out = video_importer.extract_audio(tmp_path / "clip.mp4", tmp_path / "audio.wav")
    assert out == tmp_path / "audio.wav" and out.exists()
    assert calls[0][0] == "C:/tools/ffmpeg.exe" and calls[0][-1] == str(out)
    assert "pcm_s16le" in calls[0] and "16000" in calls[0], "Whisper's 16 kHz mono WAV, as before"

    calls = _fake_ffmpeg(monkeypatch, path=None)
    with pytest.raises(RuntimeError) as exc:
        video_importer.extract_audio(tmp_path / "clip.mp4", tmp_path / "audio2.wav")
    assert "FFmpeg is not available" in str(exc.value)
    assert calls == []


class _FakeWhisperModel:
    def __init__(self, fail=False):
        self.fail = fail

    def transcribe(self, path, **kwargs):
        if self.fail:
            raise RuntimeError("model crashed")
        word = types.SimpleNamespace(word=" hello", start=0.0, end=0.5)
        segment = types.SimpleNamespace(words=[word], end=0.5)
        return [segment], types.SimpleNamespace(language="en", duration=0.5)


def _fake_extract(scratch: Path):
    def fake_extract(video, output_path=None):
        scratch.mkdir(parents=True, exist_ok=True)
        wav = scratch / "extracted_audio.wav"
        wav.write_bytes(b"wav")
        return wav
    return fake_extract


def test_generate_subtitles_removes_the_extracted_audio(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(subtitle_generator, "extract_audio", _fake_extract(scratch))
    monkeypatch.setattr(subtitle_generator, "_get_whisper_model", lambda size: _FakeWhisperModel())

    result = subtitle_generator.generate_subtitles(tmp_path / "deck.mp4", tmp_path / "subs", model_size="tiny")
    assert result["srt"].read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:00,500\nhello")
    assert result["vtt"].read_text(encoding="utf-8").startswith("WEBVTT")
    assert not scratch.exists(), "the extracted WAV and its temp dir are gone"

    monkeypatch.setattr(subtitle_generator, "_get_whisper_model", lambda size: _FakeWhisperModel(fail=True))
    with pytest.raises(RuntimeError):
        subtitle_generator.generate_subtitles(tmp_path / "deck.mp4", tmp_path / "subs", model_size="tiny")
    assert not scratch.exists(), "gone after a failed pass too"


# -- the subtitle clocks round to whole milliseconds first -----------------------------

def test_the_subtitle_clocks_round_to_whole_milliseconds_first():
    """Rounding the fraction on its own gave 1000 when it rounded up, and
    ``00:00:01,1000`` is not a timestamp in either format. Whole milliseconds
    first, the carry rides up into the seconds, minutes and hours."""
    srt, vtt = subtitle_generator.format_srt_time, subtitle_generator.format_vtt_time
    assert srt(1.9996) == "00:00:02,000", "not 00:00:01,1000"
    assert vtt(1.9996) == "00:00:02.000", "not 00:00:01.1000"
    assert srt(59.9997) == "00:01:00,000" and vtt(59.9997) == "00:01:00.000", "the carry reaches the minute"
    assert srt(3599.9994) == "00:59:59,999" and vtt(3599.9994) == "00:59:59.999", "no round-up, no carry"
    # 3599.9995 s is exactly 3,599,999.5 ms - a tie, and round() carries it into
    # the hour. (The old clock printed it as 00:59:59,999 only because subtracting
    # the integer part left a fraction just under 999.5.)
    assert srt(3599.9995) == "01:00:00,000" and srt(3599.9996) == "01:00:00,000"
    assert srt(-1.5) == "00:00:00,000" and vtt(-0.001) == "00:00:00.000", "negative is clamped at zero"
    assert srt(0) == "00:00:00,000" and srt(3725.15) == "01:02:05,150"


def test_the_srt_and_vtt_sidecars_carry_valid_clocks_when_a_fraction_rounds_up(tmp_path):
    """The generate job's sidecars are written from the same clocks: a segment
    whose start and end both round up must reach the files whole."""
    segments = [{"start": 1.9996, "end": 59.9997, "text": "hello"}]
    subtitle_generator._write_srt(segments, tmp_path / "deck.srt")
    subtitle_generator._write_vtt(segments, tmp_path / "deck.vtt")
    srt = (tmp_path / "deck.srt").read_text(encoding="utf-8")
    vtt = (tmp_path / "deck.vtt").read_text(encoding="utf-8")
    assert "00:00:02,000 --> 00:01:00,000" in srt and ",1000" not in srt
    assert vtt.startswith("WEBVTT") and "00:00:02.000 --> 00:01:00.000" in vtt and ".1000" not in vtt


# -- burn_subtitles calls the resolved ffmpeg ----------------------------------------

def test_burn_subtitles_uses_the_resolved_ffmpeg(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch, path="C:/tools/ffmpeg.exe")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"v")
    srt = tmp_path / "v.srt"
    srt.write_text("1\n", encoding="utf-8")

    assert subtitle_generator.burn_subtitles(video, srt, tmp_path / "out" / "v_sub.mp4") is True
    assert calls[0][0] == "C:/tools/ffmpeg.exe"
    assert calls[0][-1].endswith("v_sub.mp4")

    calls = _fake_ffmpeg(monkeypatch, path=None)
    assert subtitle_generator.burn_subtitles(video, srt, tmp_path / "out" / "v_sub2.mp4") is False
    assert calls == [], "no ffmpeg, no attempt"


# -- a slide's own pause override reaches the render, everywhere at once ----------------

def test_effective_pause_takes_a_valid_override_else_the_jobs_pause():
    assert video_creator.effective_pause(2.5, 1.0) == 2.5
    assert video_creator.effective_pause(0, 1.0) == 0.0, "an explicit zero is a real value"
    assert video_creator.effective_pause(3, 1.0) == 3.0
    for bad in (None, True, -1, "3", "x"):
        assert video_creator.effective_pause(bad, 1.0) == 1.0, bad
    assert video_creator.pause_after(SlideClipInfo(slide_index=0, pause_after=4.0), 1.0) == 4.0
    assert video_creator.pause_after(SlideClipInfo(slide_index=0), 1.0) == 1.0
    assert video_creator.pause_after(object(), 1.0) == 1.0, "a clip without the field"


def test_chapter_spans_honour_per_slide_pauses():
    assert video_creator.chapter_spans([2.0, 2.0, 2.0], 1.0, pauses=[1.0, 3.0, 1.0]) == [(0, 2000), (3000, 5000), (8000, 10000)]
    assert video_creator.chapter_spans([2.0, 2.0], 1.0, 0.5, pauses=[0.0, 9.0]) == [(500, 2500), (2500, 4500)], "a zero override is no gap"
    assert video_creator.chapter_spans([2.0, 2.0, 2.0], 1.0, pauses=[3.0]) == [(0, 2000), (5000, 7000), (8000, 10000)], "short list: the rest use the job's"
    assert video_creator.chapter_spans([2.0, 2.0], 1.0) == video_creator.chapter_spans([2.0, 2.0], 1.0, pauses=None)


def test_master_track_srt_and_chapters_agree_when_a_slide_overrides_the_pause(tmp_path, monkeypatch):
    """Three narrated slides, 0.5 s voice delay, a 1 s pause, and slide 2
    pausing 3 s after it: the master track, the per-slide SRT and the chapter
    spans all place slide 3 at 8 s."""
    _stub_audio(monkeypatch)
    config._config["tts_provider"] = "edge_tts"
    clips = []
    for i in range(3):
        audio = tmp_path / f"slide_{i:03d}.mp3"
        audio.write_bytes(b"mp3")
        clips.append(SlideClipInfo(slide_index=i, audio_path=audio, pause_after=3.0 if i == 1 else None))

    master, durations = video_creator._build_master_audio(clips, voice_start_delay=0.5, transition_pause=1.0)
    try:
        assert durations == [2.0, 2.0, 2.0]
        # (0.5 + 1.5) | 1 s | (0.5 + 1.5) | 3 s | (0.5 + 1.5)
        assert int(master.read_text()) == 2000 + 1000 + 2000 + 3000 + 2000
    finally:
        master.unlink(missing_ok=True)

    pm = _pm(("One.", 1.5), ("Two.", 1.5), ("Three.", 1.5))
    pm.state.slides[1].pause_override = 3.0
    text = processing._generate_srt(pm, tmp_path / "deck.mp4", transition_pause=1.0, voice_start_delay=0.5).read_text(encoding="utf-8")
    assert "00:00:00,500 --> 00:00:02,000\nOne." in text
    assert "00:00:03,500 --> 00:00:05,000\nTwo." in text
    assert "00:00:08,500 --> 00:00:10,000\nThree." in text, "3 s after slide two, not 1 s"

    metadata = {}

    def fake_run(cmd, **kwargs):
        metadata["text"] = Path(cmd[4]).read_text(encoding="utf-8")
        return types.SimpleNamespace(returncode=1, stderr=b"not really ffmpeg")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    creator = VideoCreator(transition_pause=1.0, voice_start_delay=0.5)
    chapter_clips = [SlideClipInfo(slide_index=i, duration=2.0, pause_after=3.0 if i == 1 else None) for i in range(3)]
    creator._embed_chapters(video, chapter_clips, ["One", "Two", "Three"])
    assert "START=0\nEND=2000\ntitle=One" in metadata["text"]
    assert "START=3000\nEND=5000\ntitle=Two" in metadata["text"]
    assert "START=8000\nEND=10000\ntitle=Three" in metadata["text"]


def test_the_pause_after_a_slide_lasts_its_own_pause():
    """With no master track (no narration anywhere) the picture's pause is
    the slide's own, else the job's; with one it is the gap the track left
    (``tests/test_deck_render.py``), which is the same number unless a
    transition sound outlasts it."""
    creator = VideoCreator(transition_pause=1.0)
    clips = [SlideClipInfo(slide_index=i, pause_after=p) for i, p in enumerate((None, 3.0, 0.25, None))]
    pauses = [s.seconds for s in creator.deck_segments(clips, [], gaps=[]) if s.kind == "pause"]
    assert pauses == [1.0, 3.0, 0.25], "after the last slide, none"
    followed = [s.seconds for s in creator.deck_segments(clips, [2.0] * 4, gaps=[1.0, 3.0, 0.25, 0.0]) if s.kind == "pause"]
    assert followed == [1.0, 3.0, 0.25]


def test_build_video_threads_each_slides_pause_override_into_its_clip(tmp_path, monkeypatch):
    seen = {}

    class _Creator:
        def __init__(self, **kwargs):
            seen["transition_pause"] = kwargs["transition_pause"]

        def create_video(self, slide_clips, **kwargs):
            seen["pauses"] = [c.pause_after for c in slide_clips]
            return True

    monkeypatch.setattr(processing, "VideoCreator", _Creator)
    pm = _pm(("A", 2.0), ("B", 2.0), ("C", 2.0))
    pm.state.slides[0].pause_override = 2.5
    pm.state.slides[1].pause_override = None
    # slide 2 has no pause_override attribute at all (an older state)
    processor = processing.VideoProcessor(transition_pause=1.0)
    assert processor._build_video(pm, tmp_path / "out.mp4")
    assert seen["pauses"] == [2.5, None, None], "the creator falls back to the job's pause for None"
    assert seen["transition_pause"] == 1.0
    assert processor._pause_after(pm.state.slides[0]) == 2.5 and processor._pause_after(pm.state.slides[2]) == 1.0


# -- the edit's picture step, and the mux told its length (vertical 6, phase E1) --

# The real 5m41s source's edit from the spec: 5 s cut out of 341.008.
KEEP = [[0.0, 47.3], [52.3, 341.008]]


class _FakeFfmpeg:
    """A ``subprocess.Popen`` stand-in for the picture cut, which POLLS ffmpeg
    rather than blocking on it: records the command, "runs" for ``polls``
    polls, then writes the output file (the last argument) and exits with
    ``returncode`` - or, with a huge ``polls``, never exits on its own, so the
    loop has to kill it."""

    script: dict = {}
    instances: list = []

    def __init__(self, cmd, **kwargs):
        self.cmd = list(cmd)
        self.kwargs = kwargs
        self.returncode = None
        self.polls = 0
        self.killed = False
        type(self).instances.append(self)
        if self.script.get("raise"):
            raise self.script["raise"]

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.polls >= self.script.get("polls", 0):
            if self.script.get("writes", True):
                Path(self.cmd[-1]).write_bytes(b"x")
            stderr = self.kwargs.get("stderr")
            if self.script.get("stderr") and hasattr(stderr, "write"):
                stderr.write(self.script["stderr"])
            self.returncode = self.script.get("returncode", 0)
            return self.returncode
        self.polls += 1
        return None

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def _fake_cut(monkeypatch, path="ffmpeg-test", **script):
    """Install the fake ffmpeg the cut polls; returns the processes started."""
    _FakeFfmpeg.script = script
    _FakeFfmpeg.instances = []
    monkeypatch.setattr(subprocess, "Popen", _FakeFfmpeg)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", path)
    monkeypatch.setattr(video_creator, "CUT_POLL_SECONDS", 0)
    # The source's frame rate is read from ffmpeg's header before the cut
    # runs; with Popen faked there is no header, so it is answered here.
    monkeypatch.setattr(video_creator, "source_frame_rate", lambda source: "30")
    return _FakeFfmpeg.instances


def _leaves_nothing(dst: Path) -> bool:
    part = dst.with_suffix(".part.mp4")
    return not dst.exists() and not part.exists() and not part.with_suffix(".log").exists()


def test_the_cut_uses_the_resolved_ffmpeg_and_the_proven_filtergraph(tmp_path, monkeypatch):
    """One ffmpeg run, and ``FFMPEG_PATH`` rather than the bare name: the older
    call sites work only because utils.config prepends the binary's directory
    to PATH at import, and this is the first step that runs ONLY for an edited
    project. Every range trimmed and re-timed from zero, then concatenated;
    picture only; a full re-encode (a cut lands on P-frames, so a stream copy
    cannot start there); and NO frame rate - the graph carries the source's
    own cadence, and it must never be ``fps_for_transition``'s 2 fps."""
    started = _fake_cut(monkeypatch, path="C:/tools/ffmpeg.exe")
    source = tmp_path / "finished.mp4"
    source.write_bytes(b"mp4")
    dst = tmp_path / "scratch" / "finished_cut.mp4"

    assert video_creator.cut_picture(source, KEEP, dst) is True
    (proc,) = started
    cmd = proc.cmd
    assert cmd[0] == "C:/tools/ffmpeg.exe"
    assert cmd[cmd.index("-ss") + 1] == "0.000" and cmd.index("-ss") < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == str(source)
    # The graph as the spec wrote it: an edit from the head has no offset.
    assert cmd[cmd.index("-filter_complex") + 1] == video_creator.cut_filtergraph(KEEP) == (
        "[0:v]trim=start=0.000:end=47.300,setpts=PTS-STARTPTS[v0];"
        "[0:v]trim=start=52.300:end=341.008,setpts=PTS-STARTPTS[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[v]"
    )
    assert cmd[cmd.index("-map") + 1] == "[v]" and "-an" in cmd and "-nostats" in cmd
    # The final render's encode since Q1 (``test_encode_settings`` has the why).
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and cmd[cmd.index("-preset") + 1] == "medium"
    assert "copy" not in cmd and "-r" not in cmd and "-b:v" not in cmd
    part = dst.with_suffix(".part.mp4")
    assert cmd[-2:] == ["-y", str(part)], "written aside, then published"
    assert proc.kwargs["stdin"] is subprocess.DEVNULL and hasattr(proc.kwargs["stderr"], "write"), (
        "stderr goes to a file, never a pipe nobody reads"
    )
    assert dst.exists() and not part.exists() and not part.with_suffix(".log").exists()


def test_the_cut_seeks_to_the_first_kept_range_and_offsets_every_trim(tmp_path, monkeypatch):
    """``trim`` is a filter and runs AFTER the decode, so without an input
    seek ffmpeg decodes every frame from 0 to the last kept end and discards
    most of them: a 5 s keep at the tail of the 341 s corpus source cost 3.9 s
    without the seek and 0.87 s with it, and on a two-hour source the seekless
    cut was killed by its own timeout. ``-ss`` before ``-i`` starts the decode
    at the first kept range and resets the input's timestamps, so every trim
    is offset by it. Frame-exact - the decoded frames' framemd5 is identical
    with and without the seek - which is a dev-only check, since this suite
    fakes ffmpeg (the recipe is in ``cut_picture``'s docstring)."""
    started = _fake_cut(monkeypatch)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")
    keep = [[52.3, 60.0], [70.0, 80.5]]

    assert video_creator.cut_picture(source, keep, tmp_path / "cut.mp4") is True
    (proc,) = started
    cmd = proc.cmd
    assert cmd.index("-ss") < cmd.index("-i"), "an INPUT seek: before -i, never after"
    assert cmd[cmd.index("-ss") + 1] == "52.300"
    assert cmd[cmd.index("-filter_complex") + 1] == (
        "[0:v]trim=start=0.000:end=7.700,setpts=PTS-STARTPTS[v0];"
        "[0:v]trim=start=17.700:end=28.200,setpts=PTS-STARTPTS[v1];"
        "[v0][v1]concat=n=2:v=1:a=0[v]"
    )
    assert video_creator.cut_filtergraph(keep, 52.3) == cmd[cmd.index("-filter_complex") + 1]


def test_a_single_range_still_concatenates_and_a_preset_bitrate_is_passed_through(tmp_path, monkeypatch):
    started = _fake_cut(monkeypatch)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")

    assert video_creator.cut_picture(source, [[10.0, 20.0]], tmp_path / "cut.mp4", video_bitrate="10M") is True
    (proc,) = started
    cmd = proc.cmd
    assert cmd[cmd.index("-ss") + 1] == "10.000"
    assert cmd[cmd.index("-filter_complex") + 1] == (
        "[0:v]trim=start=0.000:end=10.000,setpts=PTS-STARTPTS[v0];[v0]concat=n=1:v=1:a=0[v]"
    )
    assert cmd[cmd.index("-b:v") + 1] == "10M"
    assert video_creator.cut_filtergraph([[0, 1], [2, 3], [4, 5]]).endswith("[v0][v1][v2]concat=n=3:v=1:a=0[v]")


def test_the_cuts_timeout_is_bound_by_the_decode_reach_not_the_output():
    """The work is how far into the source ffmpeg has to decode - from the
    first kept start to the last kept end - not how much comes out. A 5 s keep
    at the tail of a two-hour source decodes nothing but those 5 s once the
    seek is in place; a 5 s keep at the head PLUS one at the tail decodes the
    whole two hours. Sized to the output, the second was killed at 75 s."""
    assert video_creator.cut_timeout([[300.0, 305.0]]) == pytest.approx(75.0)
    assert video_creator.cut_timeout([[0.0, 5.0], [300.0, 305.0]]) == pytest.approx(975.0), (
        "the same 10 s of output, 300 s of decode"
    )
    assert video_creator.cut_timeout([[7200.0, 7205.0]]) == pytest.approx(75.0)
    assert video_creator.cut_timeout(KEEP) == pytest.approx(60 + 3 * 341.008)


def test_a_stalled_cut_is_killed_at_its_deadline_and_leaves_nothing_behind(tmp_path, monkeypatch):
    """The loop keeps the timeout: an ffmpeg that never finishes is killed
    once the deadline passes, not waited on."""
    started = _fake_cut(monkeypatch, polls=10**9)
    ticks = iter(range(0, 100_000, 50))
    monkeypatch.setattr(video_creator, "_clock", lambda: float(next(ticks)))
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")
    dst = tmp_path / "cut.mp4"

    assert video_creator.cut_picture(source, [[300.0, 305.0]], dst) is False
    (proc,) = started
    assert proc.killed is True
    assert proc.polls <= 3, "a 75 s deadline on a clock ticking 50 s a poll"
    assert _leaves_nothing(dst)


def test_a_cancel_while_ffmpeg_runs_kills_it(tmp_path, monkeypatch):
    """Cancel means stop. This is the one step of a re-voice that looks at the
    flag, and a cut can run for a minute or more, so it is polled while
    ffmpeg runs and the process is killed - not merely its result discarded
    when it is done."""
    started = _fake_cut(monkeypatch, polls=10**9)
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")
    dst = tmp_path / "cut.mp4"
    asked = {"n": 0}

    def cancel_check():
        asked["n"] += 1
        return asked["n"] >= 3  # not before it starts; on the second poll

    assert video_creator.cut_picture(source, KEEP, dst, cancel_check=cancel_check) is False
    (proc,) = started
    assert proc.killed is True and proc.returncode == -9
    assert _leaves_nothing(dst)


def test_the_cut_consults_the_cancel_flag_before_ffmpeg_starts_and_before_publishing(tmp_path, monkeypatch):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")
    dst = tmp_path / "cut.mp4"

    started = _fake_cut(monkeypatch)
    assert video_creator.cut_picture(source, KEEP, dst, cancel_check=lambda: True) is False
    assert started == [] and _leaves_nothing(dst)

    # A cancel that lands between ffmpeg finishing and the publish: the
    # finished part is discarded rather than published. (The fake exits on
    # its first poll, so the flag is read exactly twice: before the start and
    # before the publish.)
    started = _fake_cut(monkeypatch)
    answers = iter([False, True])
    assert video_creator.cut_picture(source, KEEP, dst, cancel_check=lambda: next(answers, True)) is False
    (proc,) = started
    assert proc.killed is False and proc.returncode == 0
    assert _leaves_nothing(dst)


def test_a_failed_cut_answers_false_and_leaves_nothing_behind(tmp_path, monkeypatch):
    """The same contract as ``replace_video_audio``: never an exception past
    the boundary, and never a half-written picture at the destination."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")
    dst = tmp_path / "cut.mp4"

    _fake_cut(monkeypatch, returncode=1, stderr="Invalid argument")
    assert video_creator.cut_picture(source, KEEP, dst) is False
    assert _leaves_nothing(dst), "the partial file and the log are removed"

    _fake_cut(monkeypatch, writes=False)
    assert video_creator.cut_picture(source, KEEP, dst) is False, "exit 0 but no picture"
    assert _leaves_nothing(dst)

    _fake_cut(monkeypatch, **{"raise": OSError("ffmpeg crashed")})
    assert video_creator.cut_picture(source, KEEP, dst) is False, "ffmpeg could not even start"
    assert _leaves_nothing(dst)

    started = _fake_cut(monkeypatch, path=None)
    assert video_creator.cut_picture(source, KEEP, dst) is False, "no ffmpeg at all"
    assert started == []
    started = _fake_cut(monkeypatch)
    assert video_creator.cut_picture(source, [], dst) is False, "nothing to keep"
    assert started == []


def _fake_ffmpeg_measuring(monkeypatch, picture_seconds):
    """Capture ffmpeg commands, answering the PROBE the way the real binary
    does: ``[ffmpeg, "-hide_banner", "-i", <file>]`` with no output file exits 1
    and prints the input's header on stderr, as text (the probe runs with
    ``text=True``). ``picture_seconds`` None is a file ffmpeg cannot measure.
    Any other command is the mux, whose output file (the last argument) is
    created."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["-hide_banner", "-i"] and len(cmd) == 4:
            if picture_seconds is None:
                header = f"{cmd[3]}: Invalid data found when processing input\n"
            else:
                hours = int(picture_seconds // 3600)
                minutes = int(picture_seconds % 3600 // 60)
                seconds = picture_seconds % 60
                header = (
                    f"Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '{cmd[3]}':\n"
                    f"  Duration: {hours:02d}:{minutes:02d}:{seconds:05.2f}, "
                    "start: 0.000000, bitrate: 87 kb/s\n"
                    "At least one output file must be specified\n"
                )
            return types.SimpleNamespace(returncode=1, stdout="", stderr=header)
        Path(cmd[-1]).write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    return calls


@pytest.mark.parametrize("measured, told, pad", [
    (400.0, 336.008, "apad=whole_dur=400.000"),   # the picture outlives the belt: the probe wins
    (300.0, 336.008, "apad=whole_dur=336.008"),   # the belt outlives the probe: the belt raises it
    (None, 336.008, "apad=whole_dur=336.008"),    # unmeasurable: the belt holds
    (336.0, None, "apad=whole_dur=336.000"),      # told nothing: the measurement alone
    (None, None, None),                           # neither: no pad at all, never an unbounded one
])
def test_the_mux_measures_the_picture_and_pads_to_the_larger(tmp_path, monkeypatch, measured, told, pad):
    """The mux's contract since the F1 fix round, through ``replace_video_audio``
    itself with the probe answered in its real call shape.

    The PROBE ALWAYS RUNS - through ffmpeg, never a prober - because the
    length a caller can offer (the edit's sum of kept ranges for a cut, the
    extracted audio's length otherwise) is not the picture's, and trusting it
    alone deleted the tail of a picture that outlived its sound. The larger of
    the two is padded to, since an over-long pad costs one ``-shortest`` trim
    and a short one silently drops picture. The caller's value is the fallback
    when the probe cannot answer; with neither there is no pad at all.
    """
    calls = _fake_ffmpeg_measuring(monkeypatch, measured)
    cut = tmp_path / "finished_cut.mp4"
    cut.write_bytes(b"mp4")
    master = tmp_path / "master.mp3"
    master.write_bytes(b"mp3")
    out = tmp_path / "finished_revoiced.mp4"

    assert video_creator.replace_video_audio(cut, master, out, video_duration=told) is True
    assert len(calls) == 2, (
        f"the picture must be measured before the mux whatever the caller said; ran {calls}"
    )
    probe, cmd = calls
    assert probe == ["ffmpeg-test", "-hide_banner", "-i", str(cut)], "the picture is measured, with ffmpeg"
    assert not any("ffprobe" in str(arg) for call in calls for arg in call)
    assert _pad_of(cmd) == pad
    assert cmd[cmd.index("-c:v") + 1] == "copy", "the picture was already re-encoded by the cut; the mux copies it"
    assert out.exists()


# -- F1: no argv the mux builds can run for ever, and nothing asks for ffprobe --

def _mux_fixtures(tmp_path):
    """A stub picture and narration for the command-shape checks."""
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")
    master = tmp_path / "master.mp3"
    master.write_bytes(b"mp3")
    return video, master


def _pad_of(cmd):
    """The ``apad`` step of the mux's one audio chain, or None when it has
    none. The chain always opens with the unity up-mix and the 48 kHz
    resample (Q1); the pad, when there is one, is its last step."""
    steps = cmd[cmd.index("-af") + 1].split(",")
    assert steps[:2] == [video_creator.UPMIX_STEREO, video_creator.RESAMPLE_48K], steps
    pads = [step for step in steps if step.startswith("apad")]
    assert len(pads) <= 1 and (not pads or steps[-1] == pads[0]), steps
    return pads[0] if pads else None


@pytest.mark.parametrize("video_duration, pad", [
    (12.5, "apad=whole_dur=12.500"),
    (336.008, "apad=whole_dur=336.008"),
    ("7.25", "apad=whole_dur=7.250"),       # a length that arrived as text still bounds the pad
    (None, None),
    (0, None),
    (0.0, None),
    (-5.0, None),                            # a nonsense length is UNKNOWN, never a pad
    ("", None),
    ("later", None),
    (float("inf"), None),
    (float("nan"), None),
])
def test_the_mux_command_can_never_run_for_ever(tmp_path, monkeypatch, video_duration, pad):
    """THE invariant of F1: every argv ``_build_replace_audio_cmd`` builds
    terminates on the bundled ffmpeg.

    A bare ``apad`` pads for ever and ffmpeg 7.1 - the build the app ships -
    never finishes it (measured: killed at 60 s with a 48-byte stub), so the
    pad is emitted only for a length that is really known, and a length that
    is None, zero, negative, unparseable or not finite means NO pad at all
    rather than an unbounded one. And argv[0] is the resolved binary, never a
    bare name that a customer's PATH does not have.
    """
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    video, master = _mux_fixtures(tmp_path)
    cmd = video_creator._build_replace_audio_cmd(video, master, tmp_path / "out.mp4", video_duration)

    assert cmd[0] == "ffmpeg-test", "the resolved path, never a bare binary name (trap 3)"
    assert Path(cmd[0]).name not in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe")
    assert not any("ffprobe" in str(arg) for arg in cmd)
    assert _pad_of(cmd) == pad, "no length known: no pad at all, so the mux ends with the shorter stream"
    assert all("apad" not in str(arg) or "whole_dur=" in str(arg) for arg in cmd), (
        "an unbounded apad is the command that hangs"
    )
    assert "-shortest" in cmd and cmd[-1].endswith("out.mp4")


def test_the_mux_refuses_when_there_is_no_ffmpeg(tmp_path, monkeypatch, caplog):
    """No ffmpeg means False **and the specific log line** - never an argv
    naming a binary the host may not have.

    The return value alone cannot hold this guard: delete the early refusal and
    ``_build_replace_audio_cmd``'s RuntimeError is swallowed by the function's
    broad ``except`` and returns False too, so the check would stay green while
    the one line that tells an operator WHY nothing renders was lost. The log
    line is what distinguishes the two paths, so the log line is asserted.
    """
    monkeypatch.setattr(config_module, "FFMPEG_PATH", None)
    video, master = _mux_fixtures(tmp_path)
    with caplog.at_level("ERROR"):
        assert video_creator.replace_video_audio(video, master, tmp_path / "out.mp4") is False
    assert "Cannot replace the audio: ffmpeg is not available" in caplog.text, (
        "refused up front, not caught as an unexplained exception"
    )
    assert "Error replacing video audio" not in caplog.text, "not the catch-all"
    with pytest.raises(RuntimeError):
        video_creator._build_replace_audio_cmd(video, master, tmp_path / "out.mp4", 5.0)


def test_without_a_length_the_mux_asks_ffmpeg_never_ffprobe(tmp_path, monkeypatch):
    """The picture is measured out of ffmpeg's own header, never with a
    prober: the engine's code must answer on any machine, and the old bare
    ``["ffprobe", ...]`` probe answered nothing on every 0.9.0 install, which
    shipped none (F1)."""
    header = (
        "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':\n"
        "  Duration: 00:00:12.50, start: 0.000000, bitrate: 43 kb/s\n"
        "At least one output file must be specified\n"
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "-c:v" not in cmd:  # the probe: ffmpeg -hide_banner -i <file>
            return types.SimpleNamespace(returncode=1, stdout="", stderr=header)
        Path(cmd[-1]).write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    video, master = _mux_fixtures(tmp_path)

    assert video_creator.replace_video_audio(video, master, tmp_path / "out.mp4") is True
    probe, mux = calls
    assert probe == ["ffmpeg-test", "-hide_banner", "-i", str(video)]
    assert not any("ffprobe" in str(arg) for call in calls for arg in call), "nothing asks for ffprobe"
    assert _pad_of(mux) == "apad=whole_dur=12.500"

    calls.clear()
    monkeypatch.setattr(video_creator, "_probe_duration", lambda path: None)
    assert video_creator.replace_video_audio(video, master, tmp_path / "out2.mp4") is True
    (mux,) = calls
    assert _pad_of(mux) is None, "nothing could say how long the picture is: no pad, so the mux still ends"


# -- F1: the checks that need a REAL ffmpeg ------------------------------------

BUNDLED_FFMPEG = Path(r"C:\Media-Studio-Enterprise\app\bin\ffmpeg.exe")


def _real_ffmpegs() -> list[str]:
    """Every real ffmpeg on this machine, for the checks a fake cannot make.

    Both, when there are two: the one the app resolves here AND - on a machine
    with the product installed - the 7.1-essentials build that actually SHIPS.
    They do not behave the same, which is the whole of F1: an unbounded
    ``apad`` completes on this box's ffmpeg 8.0.1 in 0.2 s and never finishes
    on the bundled 7.1, so a regression test that only ever sees the developer's
    binary is exactly the test that let the bug out.
    """
    found = []
    resolved = config_module.FFMPEG_PATH
    if resolved and Path(resolved).is_file():
        found.append(str(resolved))
    if BUNDLED_FFMPEG.is_file() and str(BUNDLED_FFMPEG) not in found:
        found.append(str(BUNDLED_FFMPEG))
    return found


REAL_FFMPEGS = _real_ffmpegs()
needs_ffmpeg = pytest.mark.skipif(not REAL_FFMPEGS, reason="no real ffmpeg on this machine")


def _wav_seconds(path: Path) -> float:
    """A WAV's length from its header - what ``waveform.duration_for`` reads,
    and so what a re-voice job would thread into the mux."""
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _picture_longer_than_its_audio(ffmpeg: str, tmp_path: Path,
                                   video_seconds: float = 10.0, audio_seconds: float = 6.0):
    """A picture of ``video_seconds`` whose AUDIO STREAM stops at
    ``audio_seconds`` - a mic that stopped before the capture did, a clip
    ending on a silent card - plus the ``audio.wav`` the app's own
    ``extract_audio`` makes from it (the file every stored duration is
    measured on) and a short narration to re-voice it with.
    """
    video = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={video_seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-map", "0:v:0", "-map", "1:a:0", str(video)],
                   capture_output=True, timeout=120, check=True)
    extracted = video_importer.extract_audio(video, tmp_path / "audio.wav")
    narration = tmp_path / "narration.mp3"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=2",
                    "-c:a", "libmp3lame", str(narration)],
                   capture_output=True, timeout=120, check=True)
    return video, extracted, narration


def _real_media(ffmpeg: str, tmp_path: Path, seconds: float = 5.0, audio_seconds: float = 3.0):
    """A real ``seconds`` picture and a real, SHORTER narration, made with the
    very binary under test."""
    video = tmp_path / "clip.mp4"
    audio = tmp_path / "narration.mp3"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=30:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-t", str(seconds), str(video)],
                   capture_output=True, timeout=120, check=True)
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_seconds}",
                    "-c:a", "libmp3lame", str(audio)],
                   capture_output=True, timeout=120, check=True)
    return video, audio


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_mux_with_no_known_length_finishes_on_the_real_ffmpeg(tmp_path, monkeypatch, ffmpeg):
    """The shipped regression, end to end on the binary itself.

    A 5 s picture, a 3 s narration and NOTHING able to say how long the picture
    is - the path every 0.9.0 install took, having no ffprobe to ask. The mux must
    COMPLETE and leave a playable file with a ``moov`` atom, not the 48-byte
    ``ftyp`` stub the unbounded ``apad`` left behind while it ran on for its
    ten-minute timeout. Every ffmpeg run inside is bounded to 30 s so a
    re-stall fails this test instead of hanging the suite.
    """
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, audio = _real_media(ffmpeg, tmp_path)
    # Nothing knows the length: the caller passes none and the probe is blind.
    monkeypatch.setattr(video_creator, "_probe_duration", lambda path: None)

    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kwargs: real_run(cmd, **{**kwargs, "timeout": 30}))

    out = tmp_path / "revoiced.mp4"
    assert video_creator.replace_video_audio(video, audio, out) is True, (
        "the mux did not finish - an unbounded pad is back"
    )
    written = out.read_bytes()
    assert b"moov" in written, f"a playable file, not a {len(written)}-byte stub"
    assert len(written) > 1000


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_known_length_pads_the_narration_to_the_picture(tmp_path, monkeypatch, ffmpeg):
    """The ordinary path, on the real binary: told the picture's length, the
    mux pads the shorter narration out to it and keeps the picture's tail."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, audio = _real_media(ffmpeg, tmp_path)

    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kwargs: real_run(cmd, **{**kwargs, "timeout": 30}))

    out = tmp_path / "revoiced.mp4"
    assert video_creator.replace_video_audio(video, audio, out, video_duration=5.0) is True
    assert b"moov" in out.read_bytes()
    assert video_creator._probe_duration(out) == pytest.approx(5.0, abs=0.2), (
        "the whole picture is kept, with a silent tail"
    )


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_the_probe_answers_with_no_ffprobe_anywhere(tmp_path, monkeypatch, ffmpeg):
    """``_probe_duration`` on a real file with ffprobe made unfindable: the
    engine's own probe must not need one, whatever the host has installed -
    the old one spawned a bare ffprobe and answered nothing without it."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, audio = _real_media(ffmpeg, tmp_path, seconds=4.0, audio_seconds=2.0)

    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    assert shutil.which("ffprobe") is None, "the point of the test: no ffprobe to be found"

    assert video_creator._probe_duration(video) == pytest.approx(4.0, abs=0.2)
    assert video_creator._probe_duration(audio) == pytest.approx(2.0, abs=0.2)
    # And it says "I cannot tell" - quickly, never a stall - for the rest.
    assert video_creator._probe_duration(tmp_path / "not_there.mp4") is None
    not_media = tmp_path / "notes.txt"
    not_media.write_text("hello", encoding="utf-8")
    assert video_creator._probe_duration(not_media) is None


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_chapters_are_read_out_of_ffmpegs_own_header(tmp_path, monkeypatch, ffmpeg):
    """``get_video_chapters`` used to shell out to ffprobe; it reads the header
    ffmpeg prints for any input instead (F1). Nothing in the product calls it -
    ``import_video`` has no caller - so this is the sweep's fix, not a
    user-visible one.

    Parametrised like its siblings **because the parser is a regex over that
    header**, which is the one thing that can differ between builds: proving it
    on the developer's 8.0.1 alone would say nothing about the 7.1 that ships.
    """
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, _ = _real_media(ffmpeg, tmp_path)
    meta = tmp_path / "chapters.txt"
    meta.write_text(
        ";FFMETADATA1\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=2000\ntitle=First slide\n"
        "[CHAPTER]\nTIMEBASE=1/1000\nSTART=2000\nEND=5000\ntitle=Second, with comma\n",
        encoding="utf-8",
    )
    chaptered = tmp_path / "chaptered.mp4"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-i", str(meta), "-map_metadata", "1", "-c", "copy", str(chaptered)],
                   capture_output=True, timeout=120, check=True)

    # A host with no ffprobe: nothing can find one, here or in a subprocess.
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    assert video_importer.get_video_chapters(chaptered) == [
        {"start": 0.0, "end": 2.0, "title": "First slide"},
        {"start": 2.0, "end": 5.0, "title": "Second, with comma"},
    ]
    assert video_importer.get_video_chapters(video) == [], "no chapters, no guesses"
    assert video_importer.get_video_duration(video) == pytest.approx(5.0, abs=0.2)


# -- F1 fix round: the length must be the PICTURE's, and never an absurd one --

def test_pad_seconds_takes_the_larger_and_never_a_junk_number(tmp_path, monkeypatch):
    """The rule, without a binary: the picture is measured, the caller's number
    may only RAISE that measurement, and anything unusable on either side is
    ignored rather than padded to.

    The asymmetry is the point. A pad longer than the picture costs one
    ``-shortest`` trim; a pad shorter than it deletes picture silently.
    """
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"mp4")

    def probes(seconds):
        monkeypatch.setattr(video_creator, "_probe_duration", lambda path: seconds)

    probes(10.0)
    assert video_creator.pad_seconds(source, None) == 10.0, "measured"
    assert video_creator.pad_seconds(source, 6.014) == 10.0, (
        "the audio's length must not shorten a longer picture - the G1 regression"
    )
    assert video_creator.pad_seconds(source, 20.0) == 20.0, "a longer belt still raises it"
    assert video_creator.pad_seconds(source, float("inf")) == 10.0
    assert video_creator.pad_seconds(source, video_creator.MAX_MEDIA_SECONDS + 1) == 10.0

    probes(None)
    assert video_creator.pad_seconds(source, 6.014) == 6.014, "unmeasurable: the belt holds"
    assert video_creator.pad_seconds(source, None) is None
    assert video_creator.pad_seconds(source, -3) is None


@pytest.mark.parametrize("value, expected", [
    (video_creator.MAX_MEDIA_SECONDS, video_creator.MAX_MEDIA_SECONDS),
    (video_creator.MAX_MEDIA_SECONDS - 0.5, video_creator.MAX_MEDIA_SECONDS - 0.5),
    (video_creator.MAX_MEDIA_SECONDS + 0.001, None),
    (2147483647.99, None),          # what the metadata line used to yield
    (7200.0, 7200.0),               # a two-hour recording, the longest real one here
])
def test_usable_duration_refuses_an_absurd_length(value, expected):
    """A week is the bound: a real file is never longer, and a number past it
    is a mis-parse or a caller's bug. Rejecting it HERE closes the stall class
    for every future path that hands the mux a bad number, wherever it came
    from - the belt to the probe's anchored regex."""
    assert video_creator.usable_duration(value) == expected


# ffmpeg's real header for a 10 s file tagged with a duration-shaped comment,
# captured from the SHIPPED binary (both builds print it identically).
TAGGED_HEADER = """\
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Metadata:
    major_brand     : isom
    comment         : Duration: 596523:14:07.99
  Duration: 00:00:10.00, start: 0.000000, bitrate: 87 kb/s
  Stream #0:0[0x1](und): Video: h264 (High), yuv420p, 320x240, 30 fps
At least one output file must be specified
"""


def test_a_duration_shaped_metadata_value_is_not_the_duration(monkeypatch):
    """ffmpeg prints the input's Metadata block BEFORE its own ``Duration:``
    line, so an unanchored search read ``comment: Duration: 596523:14:07.99``
    out of a 10 s file and put ``apad=whole_dur=2147483647.990`` into the mux -
    the shipped stall, reached through the fixed code. ffmpeg's own line is at
    two spaces of indent with a comma after it; metadata values are deeper."""
    monkeypatch.setattr(video_creator, "_ffmpeg_header", lambda path: TAGGED_HEADER)
    assert video_creator._probe_duration(Path("clip.mp4")) == 10.0
    assert video_creator._pad_filter(10.0) == "apad=whole_dur=10.000"

    # A WAV's line has no ", start:" at all - it must still be read.
    monkeypatch.setattr(video_creator, "_ffmpeg_header",
                        lambda path: "  Duration: 00:00:06.01, bitrate: 256 kb/s\n")
    assert video_creator._probe_duration(Path("audio.wav")) == pytest.approx(6.01)


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_picture_longer_than_its_audio_keeps_all_of_its_picture(tmp_path, monkeypatch, ffmpeg):
    """G1, end to end on the real binary: a 10 s picture whose audio stream
    stops at 6 s, re-voiced with the number the job can actually offer.

    Every length this app stores is measured on the extracted audio - the
    ``audio.wav`` built here by the app's own ``extract_audio`` - so trusting
    it alone padded to 6.01 s and ``-shortest`` threw four seconds of picture
    away, with a success and no warning. The mux measures the picture itself
    now, and the whole of it survives.
    """
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, extracted, narration = _picture_longer_than_its_audio(ffmpeg, tmp_path)
    told = _wav_seconds(extracted)
    assert 5.9 < told < 6.2, f"the stored duration is the AUDIO's, not the picture's: {told}"

    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kwargs: real_run(cmd, **{**kwargs, "timeout": 30}))

    out = tmp_path / "revoiced.mp4"
    assert video_creator.replace_video_audio(video, narration, out, video_duration=told) is True
    assert b"moov" in out.read_bytes()
    assert video_creator._probe_duration(out) == pytest.approx(10.0, abs=0.2), (
        "the whole picture, padded with silence - not the audio's 6 s"
    )


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_bogus_duration_tag_neither_fools_nor_stalls_the_mux(tmp_path, monkeypatch, ffmpeg):
    """G2 on the real binaries: the same 10 s picture, tagged the way a
    remuxed file often is. The probe must answer 10 s, and the mux must finish
    - the 596,523-hour pad was still running at 60 s on the bundled 7.1."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, _, narration = _picture_longer_than_its_audio(ffmpeg, tmp_path)
    tagged = tmp_path / "tagged.mp4"
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(video),
                    "-c", "copy", "-metadata", "comment=Duration: 596523:14:07.99", str(tagged)],
                   capture_output=True, timeout=120, check=True)

    assert video_creator._probe_duration(tagged) == pytest.approx(10.0, abs=0.2)

    real_run = subprocess.run
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kwargs: real_run(cmd, **{**kwargs, "timeout": 30}))
    out = tmp_path / "revoiced.mp4"
    assert video_creator.replace_video_audio(tagged, narration, out) is True, (
        "the mux did not finish - a metadata tag is being padded to"
    )
    assert b"moov" in out.read_bytes()


def test_untitled_chapters_are_numbered_as_the_ffprobe_version_numbered_them():
    """The parser alone, with no video: a chapter whose metadata carries no
    title keeps the old ``Chapter N`` name, and a stream block ends a chapter's
    metadata so a stream's own title is never stolen for it."""
    header = (
        "  Chapters:\n"
        "    Chapter #0:0: start 0.000000, end 2.000000\n"
        "      Metadata:\n"
        "        title           : Opening\n"
        "    Chapter #0:1: start 2.000000, end 4.500000\n"
        "  Stream #0:0[0x1](und): Video: h264\n"
        "      Metadata:\n"
        "        title           : the video stream, not a chapter\n"
    )
    assert video_importer.parse_chapters(header) == [
        {"start": 0.0, "end": 2.0, "title": "Opening"},
        {"start": 2.0, "end": 4.5, "title": "Chapter 2"},
    ]
    assert video_importer.parse_chapters("") == []
