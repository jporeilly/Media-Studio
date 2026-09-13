"""Tests for transcribe_project, with the Whisper media engine mocked out."""

import sys
import types

import pytest

from services import projects, transcription


@pytest.fixture(autouse=True)
def tmp_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path / "projects")


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def _fake_engine(monkeypatch, segments):
    """Install a fake core.video_importer so no ffmpeg / Whisper is needed."""
    mod = types.ModuleType("core.video_importer")
    mod.extract_audio = lambda video, out=None: out
    mod.recommended_default_model = lambda: "base"
    mod.last_load_device = lambda: "cpu"

    def transcribe_audio(audio, model_size=None, on_progress=None):
        if on_progress:
            on_progress(0.5, "halfway")
        return segments, "en", 12.34

    mod.transcribe_audio = transcribe_audio
    monkeypatch.setitem(sys.modules, "core.video_importer", mod)


def test_transcribe_video_project_saves_transcript(monkeypatch):
    _fake_engine(monkeypatch, [_Seg(0.0, 2.0, "Hello"), _Seg(2.0, 4.0, "world")])
    rec = projects.import_upload("clip.mp4", b"video-bytes")

    progress = []
    summary = transcription.transcribe_project(rec["id"], None, lambda f, m="": progress.append(f))

    assert summary == {"segments": 2, "language": "en", "duration": 12.34}
    saved = projects.get_project(rec["id"])
    assert saved["language"] == "en"
    assert saved["transcript"][0] == {"start": 0.0, "end": 2.0, "text": "Hello"}
    assert saved["transcribed_device"] == "cpu"
    assert progress and progress[-1] == 1.0


def test_transcribe_rejects_non_video(monkeypatch):
    _fake_engine(monkeypatch, [])
    rec = projects.import_upload("deck.pptx", b"x")
    with pytest.raises(ValueError):
        transcription.transcribe_project(rec["id"], None, lambda *a: None)


def test_transcribe_missing_project_raises():
    with pytest.raises(ValueError):
        transcription.transcribe_project("aabbccddeeff", None, lambda *a: None)
