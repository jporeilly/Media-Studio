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
    source = None  # the picture the engine was told to mux onto

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
        _FakeVideoProcessor.source = source_video
        return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A logged-in TestClient with the store, the auth DB and the config
    (the studio's narration defaults) isolated."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    # An edited re-voice cuts the picture into a scratch directory under here.
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")

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


# ── the edit: the render is this same job (porting vertical 6, phase E1) ──────

def _wav(pid: str, seconds: float) -> None:
    """The audio the transcribe step extracts, at the length the edit is
    measured against."""
    import wave

    import numpy as np

    with wave.open(str(store.PROJECTS_DIR / pid / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(1000)
        wf.writeframes(np.zeros(int(round(seconds * 1000)), dtype="<i2").tobytes())


def _cut_recorder(monkeypatch, *, ok=True, on_call=None):
    """Stand in for ``core.video_creator.cut_picture``: records what it was
    asked to cut and writes the intermediate the engine will be pointed at."""
    from pathlib import Path

    import core.video_creator as vc

    calls = []

    def fake_cut(source, keep, dst, video_bitrate="", cancel_check=None):
        calls.append({"source": Path(source), "keep": keep, "dst": Path(dst),
                      "video_bitrate": video_bitrate, "cancel_check": cancel_check})
        if on_call:
            on_call()
        if ok:
            Path(dst).write_bytes(b"CUT-PICTURE")
        return ok

    monkeypatch.setattr(vc, "cut_picture", fake_cut)
    return calls


def _revoice(client, pid):
    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert r.status_code == 200, r.text
    return _wait_job(client, r.json()["job_id"])


def test_a_project_with_no_edit_renders_exactly_as_before(client, monkeypatch):
    """The path every project has always taken, byte for byte: the engine is
    handed the source itself, nothing is cut, nothing new is stamped."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    cuts = _cut_recorder(monkeypatch)
    pid = _video_with_transcript()
    _wav(pid, 4.0)

    assert _revoice(client, pid)["status"] == "done"
    assert cuts == []
    source = store.PROJECTS_DIR / pid / "clip.mp4"
    assert _FakeVideoProcessor.source == source
    assert _FakeVideoProcessor.captured.state.source_video_path == str(source)
    slide = _FakeVideoProcessor.captured.state.slides[0]
    assert slide.original_segments == [
        {"start": 0.0, "end": 2.0, "text": "Hello there."},
        {"start": 2.0, "end": 4.0, "text": "This is a test."},
    ]
    assert "edit_rendered_at" not in store.get_project(pid)


def test_an_edit_that_keeps_everything_takes_the_untouched_path(client, monkeypatch):
    """A split removes nothing, so there is no picture step and no stamp."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    cuts = _cut_recorder(monkeypatch)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[0.0, 2.0], [2.0, 4.0]]}).status_code == 200

    assert _revoice(client, pid)["status"] == "done"
    assert cuts == []
    assert _FakeVideoProcessor.source == store.PROJECTS_DIR / pid / "clip.mp4"
    assert _FakeVideoProcessor.captured.state.slides[0].original_segments[1] == {"start": 2.0, "end": 4.0, "text": "This is a test."}
    assert "edit_rendered_at" not in store.get_project(pid)


def test_an_edit_cuts_the_picture_first_and_renders_onto_the_cut(client, monkeypatch):
    """The render IS the re-voice job with the projection applied: the kept
    ranges become a picture-only intermediate under the job's scratch
    directory, the engine is told THAT is the source, and it is handed the
    transcript in timeline seconds - adjustments riding along untouched - so it
    sees a transcript in a shorter recording and does what it always does."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    cuts = _cut_recorder(monkeypatch)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4}).status_code == 200
    keep = [[0.0, 1.0], [1.5, 4.0]]  # 3.5 s out of 4
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": keep}).status_code == 200
    transcript_before = store.get_project(pid)["transcript"]

    job = _revoice(client, pid)
    assert job["status"] == "done", job

    (cut,) = cuts
    assert cut["source"] == store.PROJECTS_DIR / pid / "clip.mp4"
    assert cut["keep"] == keep
    assert cut["dst"].name == "clip_cut.mp4"
    assert cut["dst"].parent.parent == processing.TEMP_DIR, "under the job's scratch directory"
    assert cut["video_bitrate"] == "", "the default output preset's: the codec's own default"
    from services import jobs as jobs_module

    assert cut["cancel_check"] is jobs_module.cancel_requested_here

    # The engine muxes onto the cut picture, not the source.
    assert _FakeVideoProcessor.source == cut["dst"]
    pm = _FakeVideoProcessor.captured
    assert pm.state.source_video_path == str(cut["dst"])
    slide = pm.state.slides[0]
    assert slide.original_segments == [
        {"start": 0.0, "end": 1.0, "text": "Hello there."},                    # clamped at its range's end
        {"start": 1.5, "end": 3.5, "text": "This is a test.", "offset": -0.4},  # moved with the picture, offset untouched
    ], "timeline seconds; the projection's own index does not reach the engine"
    assert (slide.original_start_time, slide.original_end_time) == (0.0, 3.5)
    assert slide.speaker_notes == "Hello there. This is a test.", "the joined PROJECTED text, so the engine takes the per-sentence path"

    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["edit_rendered_at"] == saved["revoiced_at"], "stamped beside revoiced_at: same filename, so the cache-buster must move"
    assert saved["transcript"] == transcript_before, "the transcript never moves"
    assert saved["transcript"][1] == {"start": 2.0, "end": 4.0, "text": "This is a test.", "offset": -0.4}
    assert not cut["dst"].parent.exists(), "the scratch directory is removed once the mux is done"


def test_a_sentence_the_edit_removes_is_not_spoken(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _cut_recorder(monkeypatch)
    rec = store.import_upload("clip.mp4", b"video-bytes")
    pid = rec["id"]
    store.set_transcript(pid, [
        {"start": 0.0, "end": 2.0, "text": "Kept."},
        {"start": 3.0, "end": 4.0, "text": "Cut away."},
        {"start": 6.0, "end": 8.0, "text": "Kept too."},
    ])
    _wav(pid, 8.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[0.0, 2.5], [5.0, 8.0]]}).status_code == 200

    assert _revoice(client, pid)["status"] == "done"
    slide = _FakeVideoProcessor.captured.state.slides[0]
    assert [s["text"] for s in slide.original_segments] == ["Kept.", "Kept too."]
    assert [(s["start"], s["end"]) for s in slide.original_segments] == [(0.0, 2.0), (3.5, 5.5)]
    assert slide.speaker_notes == "Kept. Kept too."


def test_a_whole_source_run_clears_the_edit_stamp(client, monkeypatch):
    """The record must never claim an edit the file on disk does not carry -
    the same idiom as ``narration_audio``."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _cut_recorder(monkeypatch)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[0.0, 3.0]]}).status_code == 200
    assert _revoice(client, pid)["status"] == "done"
    assert store.get_project(pid).get("edit_rendered_at")

    assert client.delete(f"/api/projects/{pid}/edit").status_code == 200
    assert _revoice(client, pid)["status"] == "done"
    assert "edit_rendered_at" not in store.get_project(pid)


def test_a_cut_that_fails_fails_the_job_with_the_reason(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.captured = None
    _cut_recorder(monkeypatch, ok=False)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[0.0, 3.0]]}).status_code == 200

    job = _revoice(client, pid)
    assert job["status"] == "error"
    assert "could not be cut" in job["error"]
    assert _FakeVideoProcessor.captured is None, "the engine never ran"
    saved = store.get_project(pid)
    assert "revoiced_video" not in saved and "edit_rendered_at" not in saved


def test_a_cancel_during_the_cut_ends_the_job_as_cancelled(client, monkeypatch):
    """The picture step is the one place a re-voice consults the cancel flag
    (``_revoice_video`` never has); a cancelled cut ends the job the way the
    AI loops end theirs, with nothing rendered and nothing stamped."""
    from services import jobs as jobs_module

    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.captured = None

    def _cancel_this_job():
        jobs_module.cancel(jobs_module.current_job_id())

    _cut_recorder(monkeypatch, ok=False, on_call=_cancel_this_job)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[0.0, 3.0]]}).status_code == 200

    job = _revoice(client, pid)
    assert job["status"] == "done" and job["message"] == "Cancelled", job
    assert job["result"] == {"cancelled": True}
    assert _FakeVideoProcessor.captured is None
    assert "revoiced_video" not in store.get_project(pid)


def test_an_edit_that_cuts_every_spoken_sentence_is_refused(client, monkeypatch):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    cuts = _cut_recorder(monkeypatch)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[2.5, 4.0]]}).status_code == 200, "no sentence starts here"

    job = _revoice(client, pid)
    assert job["status"] == "error"
    assert "removes every sentence" in job["error"]
    assert cuts == []


def test_an_edit_whose_audio_is_gone_is_refused_rather_than_rendered_on_trust(client, monkeypatch):
    """The audio removed by hand after the edit was made: the ranges cannot be
    measured, so the edit is neither applied nor silently ignored."""
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    cuts = _cut_recorder(monkeypatch)
    pid = _video_with_transcript()
    _wav(pid, 4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": [[0.0, 3.0]]}).status_code == 200
    (store.PROJECTS_DIR / pid / "audio.wav").unlink()

    job = _revoice(client, pid)
    assert job["status"] == "error"
    assert "extracted audio is missing" in job["error"]
    assert cuts == []
