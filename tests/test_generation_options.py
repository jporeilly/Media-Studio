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
