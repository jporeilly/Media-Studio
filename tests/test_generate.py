"""Tests for the generate + video-download endpoints.

The media engine is mocked out (fake ``VideoProcessor`` / ``FileItem``) so no
slides are rendered and no encode runs — the point is the endpoint wiring: a job
is submitted, it saves ``output_video`` on the project, and the video is then
downloadable. The store and the auth DB are redirected to a throwaway tmp dir.
"""

import time

import pytest
from fastapi.testclient import TestClient

from services import file_item as file_item_module
from services import processing
from services import projects as store
from utils.helpers import get_output_filename


class _FakeFileItem:
    """Stands in for services.file_item.FileItem — records how it was loaded."""

    def __init__(self, path, projects_base=None):
        self.path = path
        self.projects_base = projects_base
        self.loaded = None

    def load(self):
        self.loaded = "deck"
        return True

    def load_pdf(self):
        self.loaded = "pdf"
        return True


class _FakeVideoProcessor:
    """Stands in for services.processing.VideoProcessor — writes a stub MP4."""

    last = None

    def __init__(self, voice_id="", resolution=(1920, 1080), speed=1.0, video_bitrate="", **kwargs):
        self.voice_id = voice_id
        self.resolution = resolution
        self.speed = speed
        self.video_bitrate = video_bitrate
        _FakeVideoProcessor.last = self

    def process_files(self, files, output_dir, progress=None, preview_seconds=0):
        if progress:
            progress(0.5, "rendering")
        out = get_output_filename(files[0].path, output_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"FAKEMP4")
        return 1


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


def test_generate_on_deck_renders_and_saves_output_video(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    monkeypatch.setattr(file_item_module, "FileItem", _FakeFileItem)
    _FakeVideoProcessor.last = None

    rec = store.import_upload("deck.pptx", b"pptx-bytes")
    pid = rec["id"]

    # No video before generation.
    assert client.get(f"/api/projects/{pid}/video").status_code == 404

    r = client.post(
        f"/api/projects/{pid}/generate",
        json={"voice_id": "en-US-AriaNeural", "speed": 1.1, "preset": "vimeo_1080p"},
    )
    assert r.status_code == 200
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job

    # The preset's resolution and bitrate reached the processor.
    assert _FakeVideoProcessor.last.resolution == (1920, 1080)
    assert _FakeVideoProcessor.last.video_bitrate == "10M"
    assert _FakeVideoProcessor.last.speed == 1.1

    # output_video is persisted, and the file now downloads as video/mp4.
    saved = store.get_project(pid)
    assert saved["output_video"] == "deck.mp4"

    v = client.get(f"/api/projects/{pid}/video")
    assert v.status_code == 200
    assert v.content == b"FAKEMP4"
    assert v.headers["content-type"].startswith("video/mp4")


def test_generate_rejects_a_video_project(client):
    rec = store.import_upload("clip.mp4", b"video-bytes")
    r = client.post(f"/api/projects/{rec['id']}/generate", json={"voice_id": "x"})
    assert r.status_code == 400


def test_generate_missing_project_is_404(client):
    r = client.post("/api/projects/aabbccddeeff/generate", json={"voice_id": "x"})
    assert r.status_code == 404


def test_video_missing_project_is_404(client):
    assert client.get("/api/projects/aabbccddeeff/video").status_code == 404
