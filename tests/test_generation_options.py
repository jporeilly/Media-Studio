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

import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import get_args

import numpy as np
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


def test_fps_for_transition():
    assert video_creator.fps_for_transition("none") == 2
    assert video_creator.fps_for_transition("") == 2
    assert video_creator.fps_for_transition("crossfade", 0.0) == 2, "the same gate as create_video"
    for transition in list(studio_settings.TRANSITIONS)[1:]:
        assert video_creator.fps_for_transition(transition, 0.5) == 24, transition
    assert VideoCreator().fps == video_creator.STATIC_FPS


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


# -- the horizontal slide transform runs once ------------------------------------

class _FakeClip:
    def __init__(self):
        self.mask = None
        self.duration = 5.0
        self.transforms = []

    def transform(self, func, apply_to=None):
        self.transforms.append((func, apply_to))
        return self


def test_horizontal_slide_transition_shifts_the_frame_once():
    creator = VideoCreator(resolution=(8, 2), slide_transition="slide-left", transition_duration=0.5)
    clip = _FakeClip()
    creator._apply_transition_effect(clip, slide_index=1, total_slides=3)
    assert len(clip.transforms) == 1, "applied twice, the frame moved by twice the offset"

    func, _ = clip.transforms[0]
    frame = np.stack([np.arange(8)] * 2).astype(np.uint8)[:, :, None].repeat(3, axis=2)
    # Halfway through the 0.5 s slide the offset is half the width: one shift, not two.
    out = func(lambda t: frame, 0.25)
    assert out[0, :, 0].tolist() == [4, 5, 6, 7, 0, 0, 0, 0]
    assert func(lambda t: frame, 1.0)[0, :, 0].tolist() == list(range(8)), "after the slide: the frame as is"


def test_vertical_slide_transition_also_runs_once():
    clip = _FakeClip()
    VideoCreator(slide_transition="slide-up")._apply_transition_effect(clip, 1, 3)
    assert len(clip.transforms) == 1


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
                    profile=None, intro_offset=0.0):
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


def test_transition_clip_lasts_the_slides_own_pause():
    creator = VideoCreator(transition_pause=1.0)
    assert creator.create_transition_clip().duration == 1.0
    assert creator.create_transition_clip(3.0).duration == 3.0
    assert creator.create_transition_clip(0.25).duration == 0.25


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
    assert cmd[cmd.index("-c:v") + 1] == "libx264" and cmd[cmd.index("-preset") + 1] == "ultrafast"
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


def test_the_mux_takes_the_edits_length_and_never_probes_for_it(tmp_path, monkeypatch):
    """An edited output's length is the sum of its kept ranges, known before
    ffmpeg runs, so the mux is told it: ``apad`` pads the narration to exactly
    that and ffprobe - which the packaged app does not have - is never asked."""
    calls = _fake_ffmpeg(monkeypatch)
    cut = tmp_path / "finished_cut.mp4"
    cut.write_bytes(b"mp4")
    master = tmp_path / "master.mp3"
    master.write_bytes(b"mp3")
    out = tmp_path / "finished_revoiced.mp4"

    assert video_creator.replace_video_audio(cut, master, out, video_duration=336.008) is True
    (cmd,) = calls
    assert "ffprobe" not in cmd[0]
    assert cmd[cmd.index("-af") + 1] == "apad=whole_dur=336.008"
    assert cmd[cmd.index("-c:v") + 1] == "copy", "the picture was already re-encoded by the cut; the mux copies it"
    assert out.exists()


def test_without_a_length_the_mux_probes_exactly_as_before(tmp_path, monkeypatch):
    """The unedited path is unchanged: no argument, one ffprobe, its answer in
    ``whole_dur`` - and a plain ``apad`` when the probe answers nothing."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "ffprobe" in cmd[0]:
            return types.SimpleNamespace(returncode=0, stdout="12.5\n", stderr="")
        Path(cmd[-1]).write_bytes(b"x")
        return types.SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"mp4")
    master = tmp_path / "master.mp3"
    master.write_bytes(b"mp3")

    assert video_creator.replace_video_audio(video, master, tmp_path / "out.mp4") is True
    assert calls[0][0] == "ffprobe" and calls[1][calls[1].index("-af") + 1] == "apad=whole_dur=12.500"

    calls.clear()
    monkeypatch.setattr(video_creator, "_probe_duration", lambda path: None)
    assert video_creator.replace_video_audio(video, master, tmp_path / "out2.mp4") is True
    assert calls[0][calls[0].index("-af") + 1] == "apad", "no length known: pad to infinity, -shortest bounds it"
