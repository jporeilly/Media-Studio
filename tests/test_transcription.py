"""Tests for transcribe_project (the Whisper media engine mocked out) and for
the transcribe route's request-time choice of the Whisper model."""

import sys
import time
import types

import pytest
from fastapi.testclient import TestClient

from services import projects, transcription
from utils.config import config


@pytest.fixture(autouse=True)
def tmp_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path / "projects")
    # The studio's Whisper default lives in the config; keep it in memory.
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


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
    assert saved["transcribed_model"] == "base", "no model anywhere: the engine's recommended default"
    assert progress and progress[-1] == 1.0


def test_transcribe_service_uses_the_model_it_is_given(monkeypatch):
    _fake_engine(monkeypatch, [_Seg(0.0, 1.0, "Hi")])
    config._config["whisper_model"] = "small"  # NOT read here: the route resolves it
    rec = projects.import_upload("clip.mp4", b"video-bytes")

    transcription.transcribe_project(rec["id"], "tiny", lambda *a: None)
    assert projects.get_project(rec["id"])["transcribed_model"] == "tiny"
    transcription.transcribe_project(rec["id"], None, lambda *a: None)
    assert projects.get_project(rec["id"])["transcribed_model"] == "base", "the engine's recommended default"


# -- the route: the studio's Whisper model is pinned at request time -------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    """A logged-in admin TestClient with the auth DB isolated (the project
    store and the config already are, by the autouse fixture)."""
    from api import store as auth_store

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")

    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _wait_job(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def test_transcribe_route_resolves_the_model_at_request_time(client, monkeypatch):
    seen = []

    def fake_transcribe_project(pid, model, progress):
        seen.append(model)
        return {"segments": 0}

    monkeypatch.setattr(transcription, "transcribe_project", fake_transcribe_project)
    rec = projects.import_upload("clip.mp4", b"video-bytes")
    url = f"/api/projects/{rec['id']}/transcribe"

    config._config["whisper_model"] = "small"  # Settings › Studio
    assert _wait_job(client, client.post(url, json={}).json()["job_id"])["status"] == "done"
    assert _wait_job(client, client.post(url, json={"model": "tiny"}).json()["job_id"])["status"] == "done"
    config._config["whisper_model"] = ""
    assert _wait_job(client, client.post(url).json()["job_id"])["status"] == "done"
    # The studio model, an explicit request model, and None for "the engine decides".
    assert seen == ["small", "tiny", None]

    r = client.post(url, json={"model": "huge"})
    assert r.status_code == 400
    assert "Unknown Whisper model 'huge'" in r.json()["detail"]
    assert len(seen) == 3, "no job was started"


def test_transcribe_rejects_non_video(monkeypatch):
    _fake_engine(monkeypatch, [])
    rec = projects.import_upload("deck.pptx", b"x")
    with pytest.raises(ValueError):
        transcription.transcribe_project(rec["id"], None, lambda *a: None)


def test_transcribe_missing_project_raises():
    with pytest.raises(ValueError):
        transcription.transcribe_project("aabbccddeeff", None, lambda *a: None)
