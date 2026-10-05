"""The onset profile (leading-silence trim threshold, cap, opening boost) used
when a deck is ASSEMBLED must be that of the provider that SYNTHESISED the
clips - the job's provider - not the studio's configured default. Kokoro is
~2 dB quieter than Edge: trimming a Kokoro clip with Edge's profile eats its
opening, and the default can change while a job runs.

No audio is decoded: pydub is replaced by a stub for the master-audio test and
the trim/level calls are captured. (Since T1 the master track is the only place
a deck's narration is trimmed and levelled: the per-clip moviepy opener that
trimmed again is gone with moviepy's assembly.)
"""

import sys
import types
from pathlib import Path

import pytest

from core import video_creator
from core.tts_provider import EDGE_ONSET, KOKORO_ONSET
from services import processing
from utils.config import config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


# -- the pipeline passes its own provider's profile --------------------------

class _FakePM:
    def __init__(self):
        self.state = types.SimpleNamespace(slides=[])


def _capture_creator(monkeypatch):
    seen = {}

    class _FakeCreator:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def create_video(self, **kwargs):
            return True

    monkeypatch.setattr(processing, "VideoCreator", _FakeCreator)
    return seen


def test_deck_assembly_uses_the_jobs_provider_profile_not_the_studio_default(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    config._config["tts_provider"] = "edge_tts"  # the studio default
    seen = _capture_creator(monkeypatch)

    assert processing.VideoProcessor(provider="kokoro")._build_video(_FakePM(), tmp_path / "out.mp4")
    assert seen["onset_profile"] is KOKORO_ONSET


def test_deck_assembly_defaults_to_the_configured_provider_when_none_is_given(tmp_path, monkeypatch):
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    config._config["tts_provider"] = "kokoro"
    seen = _capture_creator(monkeypatch)

    processing.VideoProcessor()._build_video(_FakePM(), tmp_path / "out.mp4")
    assert seen["onset_profile"] is KOKORO_ONSET


# -- VideoCreator threads the profile down to the master track ------------------

class _Seg:
    """A pydub.AudioSegment stand-in: only lengths matter here."""

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
        Path(path).write_bytes(b"MASTER")


def _stub_pydub(monkeypatch):
    fake = types.ModuleType("pydub")
    fake.AudioSegment = _Seg
    monkeypatch.setitem(sys.modules, "pydub", fake)


def _capture_trim_and_level(monkeypatch):
    trimmed, levelled = [], []
    monkeypatch.setattr(video_creator, "_trim_leading_silence", lambda path, profile=None: trimmed.append(profile) or path)
    monkeypatch.setattr(video_creator, "_level_opening", lambda audio, profile=None: levelled.append(profile) or audio)
    return trimmed, levelled


def _one_clip(tmp_path):
    audio = tmp_path / "slide_000.mp3"
    audio.write_bytes(b"mp3")
    return [video_creator.SlideClipInfo(slide_index=0, audio_path=audio)]


def test_build_master_audio_uses_the_given_profile(tmp_path, monkeypatch):
    _stub_pydub(monkeypatch)
    trimmed, levelled = _capture_trim_and_level(monkeypatch)
    config._config["tts_provider"] = "edge_tts"

    master, durations = video_creator._build_master_audio(
        _one_clip(tmp_path), voice_start_delay=0.5, transition_pause=0.0, profile=KOKORO_ONSET,
    )
    try:
        assert trimmed == [KOKORO_ONSET] and levelled == [KOKORO_ONSET]
        assert durations == [2.0]  # 0.5 s delay + the 1.5 s stub clip
        assert master.read_bytes() == b"MASTER"
    finally:
        master.unlink(missing_ok=True)


def test_build_master_audio_falls_back_to_the_configured_provider(tmp_path, monkeypatch):
    _stub_pydub(monkeypatch)
    trimmed, levelled = _capture_trim_and_level(monkeypatch)
    config._config["tts_provider"] = "edge_tts"

    master, _ = video_creator._build_master_audio(_one_clip(tmp_path), voice_start_delay=0.0, transition_pause=0.0)
    master.unlink(missing_ok=True)
    assert trimmed == [EDGE_ONSET] and levelled == [EDGE_ONSET]


def test_video_creator_hands_its_profile_to_the_master_track(tmp_path, monkeypatch):
    """The render trims and levels the narration in ONE place, the master
    track, with the creator's profile (the job's provider); a legacy creator
    with none hands None on, and the track falls back to the studio's."""
    seen = []

    def fake_master(slide_clips, voice_start_delay, transition_pause, transition_sound_path=None, profile=None,
                    intro_offset=0.0, gaps_out=None):
        seen.append(profile)
        return None, []

    monkeypatch.setattr(video_creator, "_build_master_audio", fake_master)
    creator = video_creator.VideoCreator(onset_profile=KOKORO_ONSET)
    assert creator.onset_profile is KOKORO_ONSET
    # No clips: the render stops right after the master track (the part under test).
    assert creator.create_video([], tmp_path / "out.mp4") is False
    assert video_creator.VideoCreator().create_video([], tmp_path / "out.mp4") is False
    assert seen == [KOKORO_ONSET, None]
    assert video_creator.VideoCreator().onset_profile is None, "legacy callers keep the config-driven fallback"
