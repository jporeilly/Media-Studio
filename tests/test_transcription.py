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

    assert summary == {"segments": 2, "language": "en", "duration": 12.34, "adjustments_dropped": 0}
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
    first = _wait_job(client, client.post(url, json={}).json()["job_id"])
    assert first["status"] == "done" and first["project_id"] == rec["id"], "attached to its project"
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


# -- transcribing again (E7 #2) ----------------------------------------------------

# A project as a user leaves it after working on it: corrected words, four
# sentences' worth of adjustments on three sentences, a cut, a marker, a
# music clip, and a re-voice on disk.
_OLD_TRANSCRIPT = [
    {"start": 0.0, "end": 2.0, "text": "Hello there (corrected).", "offset": -0.4, "speed": 1.2},
    {"start": 2.0, "end": 4.0, "text": "This is a test.", "muted": True},
    {"start": 4.0, "end": 6.0, "text": "Goodbye.", "voice": "en-GB-RyanNeural", "provider": "edge_tts"},
    {"start": 6.0, "end": 8.0, "text": "Untouched."},
]
_EDIT = {
    "version": 2,
    "video": {"keep": [[0.0, 3.0], [5.0, 8.0]]},
    "narration": {"keep": [[0.0, 8.0]]},
    "music": [{"id": "bed", "file": "bed.mp3", "at": 0.0, "in": 0.0, "out": 3.0,
               "gain": 0.15, "fade_in": 0.5, "fade_out": 0.5}],
    "markers": [{"id": "k1", "at": 5.5, "name": "Outro"}],
}
_LAST_RENDER = {
    "revoiced_video": "clip_revoiced.mp4", "revoiced_at": "2020-01-01T00:00:00+00:00",
    "narration_audio": "clip_revoiced_narration.mp3", "revoiced_language": "Spanish",
    "revoice_failed_sentences": 1, "music_rendered": 1, "edit_rendered_at": "2020-01-01T00:00:00+00:00",
}


def _worked_on_project() -> str:
    rec = projects.import_upload("clip.mp4", b"video-bytes", owner_id="u1", owner_name="Olive Owner")
    record = projects.get_project(rec["id"])
    record.update({
        "transcript": [dict(seg) for seg in _OLD_TRANSCRIPT], "language": "en", "duration": 8.0,
        "transcribed_model": "tiny", "transcribed_device": "cpu", "transcribed_at": "2020-01-01T00:00:00+00:00",
        "edit": _EDIT, **_LAST_RENDER,
    })
    projects.save_project(record)
    return rec["id"]


def test_transcribing_again_replaces_the_words_drops_every_adjustment_and_keeps_the_edit(monkeypatch):
    """What a second transcription does to the record, deliberately: the
    sentences (words and windows) are Whisper's new ones with NO adjustment
    on any of them - not even on a sentence whose window is exactly the old
    one - and the count of sentences that lost theirs is reported; the edit
    (cuts, markers, music), the last re-voice and everything else on the
    record are kept; ``audio.wav`` is extracted again; the memoised speaking
    rate, measured from the old sentences, is dropped."""
    from services import narration

    extracted = []
    _fake_engine(monkeypatch, [_Seg(0.0, 2.0, "Hello there."), _Seg(2.0, 5.0, "This is a longer test.")])

    def _extract(video, out=None):
        extracted.append((video.name, out.name))
        out.write_bytes(b"RIFF-new")
        return out

    monkeypatch.setattr(sys.modules["core.video_importer"], "extract_audio", _extract)
    pid = _worked_on_project()
    before = projects.get_project(pid)
    narration._BASELINE_CACHE[(pid, "edge_tts", "en-US-AriaNeural")] = 14.5
    messages = []

    summary = transcription.transcribe_project(pid, "base", lambda f, m="": messages.append((f, m)))

    assert summary == {"segments": 2, "language": "en", "duration": 12.34, "adjustments_dropped": 3}
    assert messages[-1] == (1.0, "Transcribed 2 segments; the adjustments on 3 sentences were dropped")
    saved = projects.get_project(pid)
    # Replaced: Whisper's sentences, three keys each - nothing carried across,
    # even onto the first sentence, whose window is exactly the old one's.
    assert saved["transcript"] == [
        {"start": 0.0, "end": 2.0, "text": "Hello there."},
        {"start": 2.0, "end": 5.0, "text": "This is a longer test."},
    ]
    assert (saved["language"], saved["duration"], saved["transcribed_model"], saved["transcribed_device"]) == (
        "en", 12.34, "base", "cpu",
    )
    assert saved["transcribed_at"] > before["transcribed_at"], "a new stamp: the page keys the card on it"
    assert extracted == [("clip.mp4", "audio.wav")], "the audio is extracted again, which rebuilds a lost audio.wav"
    assert (projects.PROJECTS_DIR / pid / "audio.wav").read_bytes() == b"RIFF-new"
    # Kept: the edit exactly as stored, the last re-voice, and everything else.
    assert saved["edit"] == _EDIT
    for key, value in _LAST_RENDER.items():
        assert saved[key] == value, key
    replaced = {"transcript", "language", "duration", "transcribed_model", "transcribed_device", "transcribed_at"}
    assert {k: v for k, v in saved.items() if k not in replaced} == {k: v for k, v in before.items() if k not in replaced}
    # The speaking rate belonged to the old sentences.
    assert not [key for key in narration._BASELINE_CACHE if key[0] == pid]


def test_a_first_transcription_drops_nothing_and_says_nothing_about_it(monkeypatch):
    _fake_engine(monkeypatch, [_Seg(0.0, 1.0, "Hi.")])
    pid = projects.import_upload("clip.mp4", b"video-bytes")["id"]
    messages = []
    summary = transcription.transcribe_project(pid, None, lambda f, m="": messages.append(m))
    assert summary["adjustments_dropped"] == 0
    assert messages[-1] == "Transcribed 1 segment"
    assert projects.get_project(pid)["transcribed_at"]


def test_transcribe_again_is_refused_while_another_job_holds_the_project(client, monkeypatch):
    """The route's 409, the same as every job start on a busy project; no job
    is started and the transcript is untouched."""
    import threading

    from services import jobs

    started = []
    monkeypatch.setattr(transcription, "transcribe_project", lambda pid, model, progress: started.append(pid) or {})
    pid = _worked_on_project()
    running, release = threading.Event(), threading.Event()

    def work(progress):
        running.set()
        release.wait(10)

    held = jobs.start("revoice", work, project_id=pid, user_id=None)
    assert running.wait(5)
    try:
        r = client.post(f"/api/projects/{pid}/transcribe", json={})
        assert r.status_code == 409, r.text
        assert r.json()["detail"] == "A job is running for this project (revoice). Wait for it to finish, then try again."
        assert jobs.active_for(pid)["id"] == held, "the running job is still the one holding it"
    finally:
        release.set()
    while jobs.active_for(pid):
        time.sleep(0.01)
    assert started == [], "no transcription was started"
    assert projects.get_project(pid)["transcript"] == _OLD_TRANSCRIPT

    # Idle again: transcribing again is an ordinary start.
    job = _wait_job(client, client.post(f"/api/projects/{pid}/transcribe", json={}).json()["job_id"])
    assert job["status"] == "done" and started == [pid]
