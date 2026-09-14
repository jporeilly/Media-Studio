"""Tests for the generate + video-download endpoints and the request schema.

The media engine is mocked out (fake ``VideoProcessor`` / ``FileItem``) so no
slides are rendered and no encode runs — the point is the endpoint wiring: a job
is submitted, every generation option reaches the processor, it saves
``output_video`` and the sidecar ``outputs`` on the project, and those are then
downloadable. The store and the auth DB are redirected to a throwaway tmp dir.
"""

import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.schemas import GenerateRequest
from services import file_item as file_item_module
from services import processing
from services import projects as store
from utils.config import config
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
    """Stands in for services.processing.VideoProcessor — writes a stub MP4 (or
    a stub preview) plus the sidecar files the job's options ask for, and
    records them in ``outputs`` the way the real processor does. The
    generation options arrive as ``kwargs`` for the tests to inspect."""

    last = None

    def __init__(self, voice_id="", resolution=(1920, 1080), speed=1.0, video_bitrate="", provider="", **kwargs):
        self.voice_id = voice_id
        self.resolution = resolution
        self.speed = speed
        self.video_bitrate = video_bitrate
        self.provider = provider
        self.kwargs = kwargs
        self.preview_seconds = None
        self.outputs = {}
        _FakeVideoProcessor.last = self

    def process_files(self, files, output_dir, progress=None, preview_seconds=0):
        self.preview_seconds = preview_seconds
        if progress:
            progress(0.5, "rendering")
        out = get_output_filename(files[0].path, output_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        if preview_seconds > 0:
            out.with_stem(out.stem + "_preview").write_bytes(b"FAKEPREVIEW")
            return 1
        out.write_bytes(b"FAKEMP4")
        sidecars = {}
        mode = self.kwargs.get("subtitles", "slide")
        if mode == "slide":
            sidecars["srt"] = out.with_suffix(".srt").name
        elif mode == "whisper":
            sidecars["srt"] = f"{out.stem}.whisper.srt"
            sidecars["vtt"] = f"{out.stem}.whisper.vtt"
        if self.kwargs.get("export_webm"):
            sidecars["webm"] = out.with_suffix(".webm").name
        if self.kwargs.get("export_gif"):
            sidecars["gif"] = out.with_suffix(".gif").name
        if self.kwargs.get("export_audio_only"):
            sidecars["mp3"] = f"{out.stem}_audio.mp3"
        for kind, name in sidecars.items():
            (out.parent / name).write_bytes(f"FAKE{kind.upper()}".encode())
        self.outputs = sidecars
        return 1


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
    assert job["project_id"] == pid, "the job is attached to its project (the slide editor refuses writes meanwhile)"

    # The preset's resolution and bitrate reached the processor, and with no
    # provider named the job runs on the configured one (Edge by default).
    assert _FakeVideoProcessor.last.resolution == (1920, 1080)
    assert _FakeVideoProcessor.last.video_bitrate == "10M"
    assert _FakeVideoProcessor.last.speed == 1.1
    assert _FakeVideoProcessor.last.provider == "edge_tts"
    assert _FakeVideoProcessor.last.voice_id == "en-US-AriaNeural"

    # output_video is persisted, and the file now downloads as video/mp4.
    saved = store.get_project(pid)
    assert saved["output_video"] == "deck.mp4"
    assert saved["outputs"] == {"srt": "deck.srt"}, "today's per-slide SRT, now recorded on the project"

    v = client.get(f"/api/projects/{pid}/video")
    assert v.status_code == 200
    assert v.content == b"FAKEMP4"
    assert v.headers["content-type"].startswith("video/mp4")


def _deck(client, monkeypatch) -> str:
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    monkeypatch.setattr(file_item_module, "FileItem", _FakeFileItem)
    _FakeVideoProcessor.last = None
    return store.import_upload("deck.pptx", b"pptx-bytes")["id"]


def test_generate_runs_the_requested_provider_with_its_default_voice(client, monkeypatch):
    pid = _deck(client, monkeypatch)
    config._config["kokoro_voice"] = "bf_emma"  # Settings › Studio: the Kokoro default

    # No voice_id: the provider's configured default voice is used.
    r = client.post(f"/api/projects/{pid}/generate", json={"provider": "kokoro"})
    assert r.status_code == 200, r.text
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert _FakeVideoProcessor.last.provider == "kokoro"
    assert _FakeVideoProcessor.last.voice_id == "bf_emma"

    # An explicit voice wins.
    r = client.post(f"/api/projects/{pid}/generate", json={"provider": "kokoro", "voice_id": "am_adam"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert _FakeVideoProcessor.last.voice_id == "am_adam"


def test_generate_defaults_to_the_configured_provider(client, monkeypatch):
    pid = _deck(client, monkeypatch)
    config._config.update({"tts_provider": "kokoro", "kokoro_voice": "af_sky"})

    r = client.post(f"/api/projects/{pid}/generate", json={})
    assert r.status_code == 200, r.text
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert _FakeVideoProcessor.last.provider == "kokoro"
    assert _FakeVideoProcessor.last.voice_id == "af_sky"


def test_generate_refuses_a_voice_from_the_other_provider(client, monkeypatch):
    pid = _deck(client, monkeypatch)
    r = client.post(f"/api/projects/{pid}/generate", json={"provider": "kokoro", "voice_id": "en-US-AriaNeural"})
    assert r.status_code == 400
    assert "looks like an Edge TTS voice" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/generate", json={"provider": "polly"})
    assert r.status_code == 400
    assert _FakeVideoProcessor.last is None, "no job was started"


def test_generate_rejects_a_video_project(client):
    rec = store.import_upload("clip.mp4", b"video-bytes")
    r = client.post(f"/api/projects/{rec['id']}/generate", json={"voice_id": "x"})
    assert r.status_code == 400


def test_generate_missing_project_is_404(client):
    r = client.post("/api/projects/aabbccddeeff/generate", json={"voice_id": "x"})
    assert r.status_code == 404


def test_video_missing_project_is_404(client):
    assert client.get("/api/projects/aabbccddeeff/video").status_code == 404


# -- the request schema ------------------------------------------------------

def test_generate_request_defaults_are_todays_behaviour():
    """Nothing sent = the studio's defaults for the render options (null),
    per-slide subtitles, no cards, no watermark, no extras, a full render."""
    assert GenerateRequest().model_dump() == {
        "provider": None, "voice_id": None, "speed": 1.0, "preset": "youtube_1080p",
        "slide_transition": None, "transition_duration": None, "transition_pause": None,
        "intro_text": "", "intro_subtitle": "", "intro_duration": 3.0,
        "outro_text": "", "outro_duration": 3.0,
        "watermark_text": None, "watermark_position": None, "watermark_opacity": None,
        "subtitles": "slide",
        "export_webm": False, "export_gif": False, "export_audio_only": False,
        "preview_seconds": 0,
    }


@pytest.mark.parametrize(
    "field, value",
    [
        ("speed", 0.4), ("speed", 2.1),
        ("slide_transition", "wipe"),
        ("transition_duration", 0.05), ("transition_duration", 2.5),
        ("transition_pause", -0.1), ("transition_pause", 5.1),
        ("intro_duration", 0.5), ("intro_duration", 10.5),
        ("outro_duration", 0), ("outro_duration", 11),
        ("watermark_position", "middle"),
        ("watermark_opacity", 0.05), ("watermark_opacity", 1.1),
        ("subtitles", "burned"),
        ("export_gif", "maybe"),
        ("preview_seconds", -1), ("preview_seconds", 121),
    ],
)
def test_generate_request_refuses_values_outside_the_bounds(field, value):
    with pytest.raises(ValidationError):
        GenerateRequest(**{field: value})


def test_generate_request_accepts_the_bounds_and_every_enum_value():
    ok = GenerateRequest(speed=0.5, transition_duration=0.1, transition_pause=5.0, intro_duration=1.0,
                         outro_duration=10.0, watermark_opacity=1.0, preview_seconds=120)
    assert ok.speed == 0.5 and ok.preview_seconds == 120
    for transition in ("none", "fade-to-black", "fade-to-white", "crossfade",
                       "slide-left", "slide-right", "slide-up", "slide-down", "zoom-in"):
        assert GenerateRequest(slide_transition=transition).slide_transition == transition
    for position in ("top-left", "top-right", "bottom-left", "bottom-right", "center"):
        assert GenerateRequest(watermark_position=position).watermark_position == position
    for mode in ("none", "slide", "whisper"):
        assert GenerateRequest(subtitles=mode).subtitles == mode


def test_card_texts_are_stripped_and_capped():
    r = GenerateRequest(intro_text="   ", intro_subtitle=" Q3 review ", outro_text="\n\t")
    assert (r.intro_text, r.intro_subtitle, r.outro_text) == ("", "Q3 review", ""), "whitespace-only makes no card"
    assert GenerateRequest(intro_text="x" * 200).intro_text == "x" * 200
    for field in ("intro_text", "intro_subtitle", "outro_text"):
        with pytest.raises(ValidationError):
            GenerateRequest(**{field: "x" * 201})


def test_generate_request_forbids_unknown_keys(client, monkeypatch):
    with pytest.raises(ValidationError):
        GenerateRequest(music_volume=0.5)
    pid = _deck(client, monkeypatch)
    r = client.post(f"/api/projects/{pid}/generate", json={"speed": 1.0, "music_volume": 0.5})
    assert r.status_code == 422
    assert "music_volume" in r.text
    assert _FakeVideoProcessor.last is None, "no job was started"


# -- every option reaches the processor ----------------------------------------

def test_generate_forwards_every_option_to_the_processor(client, monkeypatch):
    pid = _deck(client, monkeypatch)
    config._config["whisper_model"] = "small"  # Settings › Studio: the Whisper model a whisper pass uses

    r = client.post(f"/api/projects/{pid}/generate", json={
        "voice_id": "en-US-AriaNeural", "speed": 1.2, "preset": "vimeo_1080p",
        "slide_transition": "crossfade", "transition_duration": 0.7, "transition_pause": 1.2,
        "intro_text": "Welcome", "intro_subtitle": "Q3 review", "intro_duration": 4,
        "outro_text": "Thank you", "outro_duration": 2.5,
        "watermark_text": "ACME", "watermark_position": "top-left", "watermark_opacity": 0.3,
        "subtitles": "whisper",
        "export_webm": True, "export_gif": True, "export_audio_only": True,
    })
    assert r.status_code == 200, r.text
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    p = _FakeVideoProcessor.last
    assert p.speed == 1.2 and p.resolution == (1920, 1080)
    assert p.kwargs == {
        "slide_transition": "crossfade", "transition_duration": 0.7, "transition_pause": 1.2,
        "intro_text": "Welcome", "intro_subtitle": "Q3 review", "intro_duration": 4.0,
        "outro_text": "Thank you", "outro_duration": 2.5,
        "watermark_text": "ACME", "watermark_position": "top-left", "watermark_opacity": 0.3,
        "subtitles": "whisper", "whisper_model": "small",
        "export_webm": True, "export_gif": True, "export_audio_only": True,
    }
    assert p.preview_seconds == 0

    saved = store.get_project(pid)
    assert saved["output_video"] == "deck.mp4"
    assert saved["outputs"] == {
        "srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt",
        "webm": "deck.webm", "gif": "deck.gif", "mp3": "deck_audio.mp3",
    }


def test_generate_falls_back_to_the_studio_render_defaults(client, monkeypatch):
    """The transition, pause and watermark come from Settings › Studio when the
    request leaves them out or sends null; an explicit value (even "") wins."""
    pid = _deck(client, monkeypatch)
    config._config.update({
        "slide_transition": "fade-to-black", "transition_duration": 1.0, "transition_pause": 2.0,
        "watermark_text": "Studio brand", "watermark_position": "center", "watermark_opacity": 0.8,
    })

    r = client.post(f"/api/projects/{pid}/generate", json={})
    assert r.status_code == 200, r.text
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    k = _FakeVideoProcessor.last.kwargs
    assert k["slide_transition"] == "fade-to-black"
    assert k["transition_duration"] == 1.0
    assert k["transition_pause"] == 2.0
    assert k["watermark_text"] == "Studio brand"
    assert k["watermark_position"] == "center"
    assert k["watermark_opacity"] == 0.8
    # The rest is the engine's own default: no cards, per-slide subtitles, no extras.
    assert k["intro_text"] == "" and k["intro_subtitle"] == "" and k["intro_duration"] == 3.0
    assert k["outro_text"] == "" and k["outro_duration"] == 3.0
    assert k["subtitles"] == "slide" and k["whisper_model"] == ""
    assert (k["export_webm"], k["export_gif"], k["export_audio_only"]) == (False, False, False)

    r = client.post(f"/api/projects/{pid}/generate",
                    json={"watermark_text": "", "slide_transition": None, "transition_pause": 0})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    k = _FakeVideoProcessor.last.kwargs
    assert k["watermark_text"] == "", "an explicit empty text is no watermark for this render"
    assert k["slide_transition"] == "fade-to-black", "null is the studio default"
    assert k["transition_pause"] == 0.0


def test_generate_saves_onto_the_record_as_it_is_when_the_job_ends(client, monkeypatch):
    """A preview and a full render of one project can run at once: the job
    must save onto the record as it is then, not the copy captured at request
    time, or it drops what the other job saved meanwhile."""
    pid = _deck(client, monkeypatch)

    class _RacingProcessor(_FakeVideoProcessor):
        def process_files(self, files, output_dir, progress=None, preview_seconds=0):
            # The other job finishes while this one renders and saves its preview.
            rec = store.get_project(pid)
            rec["outputs"] = {"preview": "deck_preview.mp4"}
            store.save_project(rec)
            return super().process_files(files, output_dir, progress, preview_seconds)

    monkeypatch.setattr(processing, "VideoProcessor", _RacingProcessor)
    r = client.post(f"/api/projects/{pid}/generate", json={})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    saved = store.get_project(pid)
    assert saved["outputs"] == {"preview": "deck_preview.mp4", "srt": "deck.srt"}
    assert saved["output_video"] == "deck.mp4"
    assert saved["rendered_at"], "the players cache-bust on it"


def test_generate_reports_a_failed_render_as_a_job_error(client, monkeypatch):
    class _FailingProcessor(_FakeVideoProcessor):
        def process_files(self, files, output_dir, progress=None, preview_seconds=0):
            return 0

    pid = _deck(client, monkeypatch)
    monkeypatch.setattr(processing, "VideoProcessor", _FailingProcessor)
    r = client.post(f"/api/projects/{pid}/generate", json={})
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "error"
    assert "could not be rendered" in job["error"]
    saved = store.get_project(pid)
    assert "output_video" not in saved and "outputs" not in saved


# -- previews --------------------------------------------------------------------

def test_preview_renders_a_separate_short_clip(client, monkeypatch):
    pid = _deck(client, monkeypatch)

    r = client.post(f"/api/projects/{pid}/generate", json={"preview_seconds": 15})
    assert r.status_code == 200, r.text
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done"
    assert _FakeVideoProcessor.last.preview_seconds == 15
    assert job["result"] == {"preview": "deck_preview.mp4"}

    saved = store.get_project(pid)
    assert saved["outputs"] == {"preview": "deck_preview.mp4"}
    assert saved["rendered_at"]
    assert "output_video" not in saved, "a preview never stands in for the full render"
    assert client.get(f"/api/projects/{pid}/video").status_code == 404

    v = client.get(f"/api/projects/{pid}/outputs/preview")
    assert v.status_code == 200
    assert v.content == b"FAKEPREVIEW"
    assert v.headers["content-type"].startswith("video/mp4")
    assert v.headers["content-disposition"].startswith("inline")

    # A full render afterwards keeps the preview listed beside its own sidecars.
    r = client.post(f"/api/projects/{pid}/generate", json={})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    saved = store.get_project(pid)
    assert saved["output_video"] == "deck.mp4"
    assert saved["outputs"] == {"preview": "deck_preview.mp4", "srt": "deck.srt"}


# -- the outputs route -----------------------------------------------------------

@pytest.mark.parametrize(
    "kind, media_type",
    [("srt", "application/x-subrip"), ("vtt", "text/vtt"), ("webm", "video/webm"),
     ("gif", "image/gif"), ("mp3", "audio/mpeg")],
)
def test_outputs_route_serves_each_kind_as_a_download(client, monkeypatch, kind, media_type):
    pid = _deck(client, monkeypatch)
    r = client.post(f"/api/projects/{pid}/generate", json={
        "subtitles": "whisper", "export_webm": True, "export_gif": True, "export_audio_only": True,
    })
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    r = client.get(f"/api/projects/{pid}/outputs/{kind}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(media_type)
    assert r.headers["content-disposition"].startswith("attachment")
    assert store.get_project(pid)["outputs"][kind] in r.headers["content-disposition"]
    assert r.content == f"FAKE{kind.upper()}".encode()


def test_outputs_route_is_404_for_a_kind_the_project_does_not_have(client, monkeypatch):
    pid = _deck(client, monkeypatch)
    assert client.get(f"/api/projects/{pid}/outputs/srt").status_code == 404, "nothing rendered yet"

    r = client.post(f"/api/projects/{pid}/generate", json={"subtitles": "none"})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"
    assert store.get_project(pid)["outputs"] == {}
    for kind in ("srt", "vtt", "webm", "gif", "mp3", "preview"):
        assert client.get(f"/api/projects/{pid}/outputs/{kind}").status_code == 404, kind
    assert client.get(f"/api/projects/{pid}/outputs/exe").status_code == 404, "unknown kind"
    assert client.get("/api/projects/aabbccddeeff/outputs/srt").status_code == 404, "unknown project"


def test_outputs_route_refuses_a_path_outside_the_project(client, monkeypatch):
    pid = _deck(client, monkeypatch)
    r = client.post(f"/api/projects/{pid}/generate", json={})
    assert _wait_job(client, r.json()["job_id"])["status"] == "done"

    # A tampered project.json must never read a file outside the project dir.
    secret = store.PROJECTS_DIR.parent / "secret.txt"
    secret.write_text("not for download")
    record = store.get_project(pid)
    record["outputs"]["srt"] = "../../secret.txt"
    store.save_project(record)
    assert client.get(f"/api/projects/{pid}/outputs/srt").status_code == 404
