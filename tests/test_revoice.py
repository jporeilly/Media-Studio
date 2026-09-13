"""Tests for the re-voice endpoints + service, with the media/translation engines
mocked out.

No real render or Ollama call runs: a fake ``VideoProcessor`` writes a stub MP4
from ``_revoice_video`` and returns True, and translation is monkeypatched. The
store and the auth DB are redirected to a throwaway tmp dir. The point is the
wiring: a job is submitted, the transcript is reconstructed onto a re-voiceable
ProjectState, ``revoiced_video`` is saved, and the file is then downloadable —
while the project store's own record (transcript, kind) survives.
"""

import time

import pytest
from fastapi.testclient import TestClient

from core import translator
from services import processing
from services import projects as store


class _FakeVideoProcessor:
    """Stands in for services.processing.VideoProcessor.

    ``_revoice_video`` writes a stub MP4 and captures the reconstructed
    ProjectManager so tests can assert how the transcript was reconstructed.
    """

    last = None
    captured = None

    def __init__(self, voice_id="", resolution=(1920, 1080), speed=1.0, video_bitrate="", **kwargs):
        self.voice_id = voice_id
        self.resolution = resolution
        self.speed = speed
        _FakeVideoProcessor.last = self

    def _revoice_video(self, pm, source_video, output_path, progress=None, file_label=""):
        if progress:
            progress(0.9, "revoicing")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"FAKEREVOICE")
        _FakeVideoProcessor.captured = pm
        return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A logged-in TestClient with the store and auth DB isolated to tmp."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")

    from api import store as auth_store

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")

    from api.app import app

    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 200
        yield c


def _wait_job(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def _video_with_transcript():
    """Import a video project and give it a two-segment transcript."""
    rec = store.import_upload("clip.mp4", b"video-bytes")
    store.set_transcript(
        rec["id"],
        [
            {"start": 0.0, "end": 2.0, "text": "Hello there."},
            {"start": 2.0, "end": 4.0, "text": "This is a test."},
        ],
    )
    return rec["id"]


def test_revoice_video_with_transcript_runs_and_saves(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.last = None
    pid = _video_with_transcript()

    # No re-voiced video before the job.
    assert client.get(f"/api/projects/{pid}/revoiced-video").status_code == 404

    r = client.post(
        f"/api/projects/{pid}/revoice",
        json={"voice_id": "en-US-GuyNeural", "speed": 1.1},
    )
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    # The voice and speed reached the processor.
    assert _FakeVideoProcessor.last.voice_id == "en-US-GuyNeural"
    assert _FakeVideoProcessor.last.speed == 1.1

    # revoiced_video is persisted, and the store record survived (transcript/kind).
    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["kind"] == "video"
    assert len(saved["transcript"]) == 2

    v = client.get(f"/api/projects/{pid}/revoiced-video")
    assert v.status_code == 200
    assert v.content == b"FAKEREVOICE"
    assert v.headers["content-type"].startswith("video/mp4")


def test_revoice_reconstructs_segments_on_the_slide(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.captured = None
    pid = _video_with_transcript()

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    pm = _FakeVideoProcessor.captured
    slide = pm.state.slides[0]
    # Whisper segments carried over verbatim, spanning the whole clip.
    assert slide.original_segments[0]["text"] == "Hello there."
    assert slide.original_start_time == 0.0
    assert slide.original_end_time == 4.0
    # Untranslated: notes == the joined segment text (engine's per-sentence path).
    assert slide.speaker_notes == "Hello there. This is a test."
    assert pm.state.source_video_path.endswith("clip.mp4")


def test_revoice_translates_when_language_given(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.captured = None

    calls = {}

    def fake_translate_notes(notes, target_language, ollama_url, ollama_model, on_progress=None):
        calls["args"] = (notes, target_language, ollama_url, ollama_model)
        return ["Hola. Esto es una prueba."]

    monkeypatch.setattr(translator, "translate_notes", fake_translate_notes)

    pid = _video_with_transcript()
    r = client.post(
        f"/api/projects/{pid}/revoice",
        json={"voice_id": "v", "language": "Spanish"},
    )
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    # Ollama translation was invoked with the joined transcript + the display name.
    assert calls["args"][0] == ["Hello there. This is a test."]
    assert calls["args"][1] == "Spanish"

    pm = _FakeVideoProcessor.captured
    slide = pm.state.slides[0]
    # Translated text on speaker_notes; source-language segments preserved so the
    # engine spreads the translated sentences across the section window.
    assert slide.speaker_notes == "Hola. Esto es una prueba."
    assert slide.original_segments[0]["text"] == "Hello there."

    saved = store.get_project(pid)
    assert saved["revoiced_language"] == "Spanish"


def test_revoice_rejects_a_deck(client):
    rec = store.import_upload("deck.pptx", b"pptx-bytes")
    r = client.post(f"/api/projects/{rec['id']}/revoice", json={"voice_id": "v"})
    assert r.status_code == 400


def test_revoice_rejects_video_without_transcript(client):
    rec = store.import_upload("clip.mp4", b"video-bytes")
    r = client.post(f"/api/projects/{rec['id']}/revoice", json={"voice_id": "v"})
    assert r.status_code == 400
    assert "Transcribe" in r.json()["detail"]


def test_revoice_missing_project_is_404(client):
    r = client.post("/api/projects/aabbccddeeff/revoice", json={"voice_id": "v"})
    assert r.status_code == 404


def test_revoiced_video_missing_project_is_404(client):
    assert client.get("/api/projects/aabbccddeeff/revoiced-video").status_code == 404


def test_languages_endpoint_lists_targets(client):
    r = client.get("/api/languages")
    assert r.status_code == 200
    langs = r.json()["languages"]
    names = [x["name"] for x in langs]
    assert "Spanish" in names and "French" in names
    assert all(x.get("subtag") for x in langs)  # every entry carries a subtag
