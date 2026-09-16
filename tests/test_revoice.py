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
from utils.config import config


class _FakeVideoProcessor:
    """Stands in for services.processing.VideoProcessor.

    ``_revoice_video`` writes a stub MP4 and captures the reconstructed
    ProjectManager so tests can assert how the transcript was reconstructed.
    """

    last = None
    captured = None

    def __init__(self, voice_id="", resolution=(1920, 1080), speed=1.0, video_bitrate="", provider="", **kwargs):
        self.voice_id = voice_id
        self.resolution = resolution
        self.speed = speed
        self.provider = provider
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
    """A logged-in TestClient with the store, the auth DB and the config
    (the studio's narration defaults) isolated."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)

    from api import store as auth_store

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")

    from api.app import app

    with TestClient(app) as c:
        # The seeded admin must change its password before the API serves it
        # anything but the auth routes (api/deps.py); this suite is not about
        # that gate, so start from an admin that has already done so.
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
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
    assert job["project_id"] == pid, "the job is attached to its project"

    # The voice and speed reached the processor, on the configured provider.
    assert _FakeVideoProcessor.last.voice_id == "en-US-GuyNeural"
    assert _FakeVideoProcessor.last.speed == 1.1
    assert _FakeVideoProcessor.last.provider == "edge_tts"

    # revoiced_video is persisted, and the store record survived (transcript/kind).
    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["kind"] == "video"
    assert len(saved["transcript"]) == 2

    v = client.get(f"/api/projects/{pid}/revoiced-video")
    assert v.status_code == 200
    assert v.content == b"FAKEREVOICE"
    assert v.headers["content-type"].startswith("video/mp4")

    # Every re-voice rewrites the same filename, so the record carries a stamp
    # the page can hang on the URL. Without it the browser answers the next
    # request from the copy it cached before the file was replaced, and a
    # cached body whose file has moved under it does not decode: the finished
    # video came back as a black frame at 0:00, and its download as bytes that
    # are no longer a video.
    assert saved.get("revoiced_at"), "a re-voice must stamp when it happened"

    # The server half of the same fix: without an explicit Cache-Control a
    # browser MAY apply heuristic freshness and never revalidate at all.
    assert v.headers.get("cache-control") == "no-cache"
    # The track route carries it too - the narration is rewritten by every run
    # exactly as the video is. Asserted on ``picture`` because this fake
    # processor writes no narration file, so that kind is honestly a 404 here.
    t = client.get(f"/api/projects/{pid}/tracks/picture")
    assert t.status_code == 200
    assert t.headers.get("cache-control") == "no-cache"


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


def test_revoice_runs_the_requested_provider_with_its_default_voice(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.last = None
    config._config["kokoro_voice"] = "bm_lewis"  # Settings › Studio: the Kokoro default
    pid = _video_with_transcript()

    r = client.post(f"/api/projects/{pid}/revoice", json={"provider": "kokoro"})
    assert r.status_code == 200, r.text
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert _FakeVideoProcessor.last.provider == "kokoro"
    assert _FakeVideoProcessor.last.voice_id == "bm_lewis"
    assert _FakeVideoProcessor.captured.state.slides[0].voice_id == "bm_lewis"

    r = client.post(f"/api/projects/{pid}/revoice", json={"provider": "kokoro", "voice_id": "en-US-AriaNeural"})
    assert r.status_code == 400
    assert "looks like an Edge TTS voice" in r.json()["detail"]


def test_revoice_translates_with_the_studio_ollama_model(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    config._config["ollama_model"] = "gemma3:12b"
    calls = {}

    def fake_translate_notes(notes, target_language, ollama_url, ollama_model, on_progress=None):
        calls["model"] = ollama_model
        return ["Bonjour."]

    monkeypatch.setattr(translator, "translate_notes", fake_translate_notes)
    pid = _video_with_transcript()
    r = client.post(f"/api/projects/{pid}/revoice", json={"language": "French"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert calls["model"] == "gemma3:12b"


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


class _KeepsNarration(_FakeVideoProcessor):
    """A re-voice that also leaves the narration track behind, as the real
    engine does once the audio swap has succeeded."""

    def _revoice_video(self, pm, source_video, output_path, progress=None, file_label=""):
        ok = super()._revoice_video(pm, source_video, output_path, progress=progress, file_label=file_label)
        processing.narration_path_for(output_path).write_bytes(b"NARRATION")
        return ok


def test_the_narration_track_is_recorded_and_downloadable(client, monkeypatch):
    """The new voice on its own, for editing the video in another tool."""
    monkeypatch.setattr(processing, "VideoProcessor", _KeepsNarration)
    pid = _video_with_transcript()

    assert client.get(f"/api/projects/{pid}/tracks/narration").status_code == 404

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    assert store.get_project(pid)["narration_audio"] == "clip_revoiced_narration.mp3"
    got = client.get(f"/api/projects/{pid}/tracks/narration")
    assert got.status_code == 200
    assert got.content == b"NARRATION"
    assert got.headers["content-type"].startswith("audio/mpeg")


def test_no_narration_is_claimed_when_the_engine_kept_none(client, monkeypatch):
    """A project re-voiced before the track was kept - or by an engine whose
    copy failed - must not advertise one."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    pid = _video_with_transcript()

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    assert "narration_audio" not in store.get_project(pid)
    assert client.get(f"/api/projects/{pid}/tracks/narration").status_code == 404


def test_a_stale_narration_claim_is_cleared_by_the_next_re_voice(client, monkeypatch):
    """The record is rewritten from what is actually on disk, so a re-voice
    that kept no track cannot leave the previous one's claim standing."""
    monkeypatch.setattr(processing, "VideoProcessor", _KeepsNarration)
    pid = _video_with_transcript()
    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert store.get_project(pid).get("narration_audio")

    processing.narration_path_for(store.PROJECTS_DIR / pid / "clip_revoiced.mp4").unlink()
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    assert "narration_audio" not in store.get_project(pid)


class _DropsSentences(_FakeVideoProcessor):
    """A re-voice where two sentences could not be synthesised."""

    def _revoice_video(self, pm, source_video, output_path, progress=None, file_label=""):
        self.failed_sentences = 2
        return super()._revoice_video(pm, source_video, output_path, progress=progress, file_label=file_label)


def test_the_per_sentence_adjustments_travel_into_the_engine(client, monkeypatch):
    """``original_segments`` is a plain List[dict] that ProjectManager saves with
    asdict, so the adjustment keys survive the round trip for free - no engine
    dataclass needs a new field."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.captured = None
    pid = _video_with_transcript()
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4, "speed": 1.15}).status_code == 200

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    segments = _FakeVideoProcessor.captured.state.slides[0].original_segments
    assert segments[0] == {"start": 0.0, "end": 2.0, "text": "Hello there."}, "an untouched sentence is unchanged"
    assert segments[1]["offset"] == -0.4 and segments[1]["speed"] == 1.15


def test_a_re_voice_reports_the_sentences_it_could_not_synthesise(client, monkeypatch):
    """A failed sentence leaves a silent hole. Counted in the job result and
    kept on the record, so the page can still say so after the job is gone."""
    monkeypatch.setattr(processing, "VideoProcessor", _DropsSentences)
    pid = _video_with_transcript()

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["failed_sentences"] == 2
    assert "2 sentences could not be synthesised" in job["message"]
    assert store.get_project(pid)["revoice_failed_sentences"] == 2

    # A clean run clears it: a stale count would claim sentences are missing
    # from a narration that has every one of them.
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    job = _wait_job(client, r.json()["job_id"])
    assert job["result"]["failed_sentences"] == 0
    assert "revoice_failed_sentences" not in store.get_project(pid)


class _AdjustsMidway(_FakeVideoProcessor):
    """Stands in for a user adjusting a sentence while the job runs: the record
    on disk changes after ``revoice_project`` read its copy."""

    def _revoice_video(self, pm, source_video, output_path, progress=None, file_label=""):
        record = store.get_project(_AdjustsMidway.pid)
        record["transcript"][0]["offset"] = -0.4
        store.save_project(record)
        return super()._revoice_video(pm, source_video, output_path, progress=progress, file_label=file_label)


def test_a_re_voice_saves_onto_the_record_as_it_is_now(client, monkeypatch):
    """The record is re-read before it is written: the transcript on it carries
    the user's adjustments, and writing back the copy read when the job started
    would revert anything saved since."""
    monkeypatch.setattr(processing, "VideoProcessor", _AdjustsMidway)
    _AdjustsMidway.pid = _video_with_transcript()

    r = client.post(f"/api/projects/{_AdjustsMidway.pid}/revoice", json={"voice_id": "v"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    saved = store.get_project(_AdjustsMidway.pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["transcript"][0]["offset"] == -0.4, "the adjustment made during the job survived"


def test_the_picture_and_the_original_audio_are_downloadable(client):
    """Both already existed on disk and were simply never served: the imported
    video, and the audio extracted from it when it was transcribed."""
    pid = _video_with_transcript()

    picture = client.get(f"/api/projects/{pid}/tracks/picture")
    assert picture.status_code == 200
    assert picture.content == b"video-bytes"
    assert picture.headers["content-disposition"].startswith("attachment")

    # audio.wav is written by the transcription step, which is mocked here.
    assert client.get(f"/api/projects/{pid}/tracks/original-audio").status_code == 404
    (store.PROJECTS_DIR / pid / "audio.wav").write_bytes(b"WAV")
    original = client.get(f"/api/projects/{pid}/tracks/original-audio")
    assert original.status_code == 200 and original.content == b"WAV"


def test_an_unknown_track_names_the_ones_that_exist(client):
    pid = _video_with_transcript()
    r = client.get(f"/api/projects/{pid}/tracks/subtitles")
    assert r.status_code == 404
    assert "picture" in r.json()["detail"] and "narration" in r.json()["detail"]


def test_a_track_filename_cannot_escape_the_project_directory(client):
    """The record is the only source of the filename, but a tampered one must
    not read an arbitrary file - the same guard the video routes use."""
    pid = _video_with_transcript()
    rec = store.get_project(pid)
    rec["narration_audio"] = "../../../../Windows/win.ini"
    store.save_project(rec)

    assert client.get(f"/api/projects/{pid}/tracks/narration").status_code == 404


def test_tracks_are_refused_for_a_deck_or_a_pdf(client):
    """Only a video has tracks. Every kind of project has a source file, so a
    route that checked only the track name would hand back a deck's .pptx as
    the "picture" - labelled as video."""
    for filename, body in (("slides.pptx", b"PK-deck"), ("paper.pdf", b"%PDF-1.4")):
        pid = store.import_upload(filename, body)["id"]
        r = client.get(f"/api/projects/{pid}/tracks/picture")
        assert r.status_code == 400, f"{filename}: {r.status_code}"
        assert "video projects" in r.json()["detail"]


def test_the_picture_keeps_its_own_container_type(client):
    """.mov, .mkv, .avi, .webm and .m4v are all importable, so the picture
    cannot be announced as video/mp4 on the way out."""
    pid = store.import_upload("clip.mov", b"quicktime-bytes")["id"]
    store.set_transcript(pid, [{"start": 0.0, "end": 1.0, "text": "Hello."}])

    r = client.get(f"/api/projects/{pid}/tracks/picture")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("video/quicktime"), r.headers["content-type"]
    assert "clip.mov" in r.headers["content-disposition"]
