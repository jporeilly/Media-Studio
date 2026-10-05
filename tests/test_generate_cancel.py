"""T2: a render's Cancel reaches the processor, and what a cancel leaves.

``POST /api/jobs/{id}/cancel`` on a ``generate`` job used to return 200 while
the render ran to its end: the processor honoured ``cancel_requested`` at
every stage and nothing ever set it. Now the generate job binds the check to
itself in its own thread (``jobs.cancel_check_here``) and hands it to the
processor, which asks it around the slide export, before every slide the
narration synthesises - ON THE POOL WORKER that would synthesise it -, while
ffmpeg encodes (T1: killed within a poll, the ``.part.mp4`` removed), before
the subtitles and before and during each extra format (each one ffmpeg run
written aside, killed the same way). The job ends ``done`` with
``result = {"cancelled": True, "stage": ...}`` and a closing line in the
user's terms.

The rule these tests hold: **before the new video is written** the project is
exactly as it was - the record (``output_video``, ``outputs``,
``rendered_at``) is not written, the previous video and every sidecar it
names are byte for byte what they were - and **after it** the new video is
kept and recorded with the sidecars that were finished, the unfinished ones
absent from ``outputs`` and from disk. A cancel is never reported as a
failure, nor a failure as a cancel.

The route-level tests run a REAL job through the real route, the real
``VideoProcessor`` and the real deck render on every real ffmpeg on the
machine (``test_generation_options.REAL_FFMPEGS``: the dev box's and the
bundled 7.1), over a deck whose stills and narration clips are already there
(so no PowerPoint and no voice service); the encode and the WebM are slowed
to real time from the test side so the cancel lands while ffmpeg runs, and
the time from the cancel to the job's end is measured and printed
("cancel-to-stop"). The narration tests stub the voice service.
"""

import hashlib
import shutil
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api import store as auth_store
from api.routers.jobs import CANCEL_FORBIDDEN
from core import video_creator
from core.project_manager import ProjectManager, get_project_dir
from services import file_item as file_item_module
from services import jobs, processing
from services import projects as store
from utils import config as config_module
from utils.config import config

from test_generate import _FakeFileItem, _FakeVideoProcessor
from test_generation_options import REAL_FFMPEGS, needs_ffmpeg

ON_EVERY_FFMPEG = pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
# The one real ffmpeg a test about threads, not binaries, runs on.
ONE_FFMPEG = REAL_FFMPEGS[:1]

PASSWORD = "Owner-pass-12345"
VOICE = "en-US-AriaNeural"
# How long a cancel may take to end the job while ffmpeg runs: a poll
# (``CUT_POLL_SECONDS`` 0.5 s), the kill, and the scratch's removal.
STOP_WITHIN = 2.5


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    # Every scratch the render makes lands under tmp: the processor's
    # (``_job_scratch``) and the deck render's (``config_module.TEMP_DIR``).
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")


def _signed_in(app, username: str, password: str) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture
def cast():
    """The admin, a project's owner (an editor) and another editor, each with
    their own cookie jar, plus the accounts."""
    from api.app import app

    with TestClient(app):
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        owner = auth_store.create_user("owner", PASSWORD, "Olive Owner", role="editor", must_change_password=False)
        yield {
            "admin": _signed_in(app, "admin", "admin"),
            "owner": _signed_in(app, "owner", PASSWORD),
            "admin_account": seeded,
            "owner_account": owner,
        }


@pytest.fixture
def admin(cast) -> TestClient:
    return cast["admin"]


# ── a deck on disk, with its stills and narration already made ────────────────

class _DeckItem:
    """Stands in for ``services.file_item.FileItem``: loads the engine project
    the test prepared under the project directory (the real one would read
    the .pptx through PowerPoint's reader)."""

    def __init__(self, path, projects_base=None):
        self.path = Path(path)
        self._projects_base = Path(projects_base)
        self.reader = None
        self.project_manager = None
        self.has_project = False
        self.slide_count = 0

    def load(self) -> bool:
        pm = ProjectManager(get_project_dir(self.path, self._projects_base))
        pm.load()
        self.project_manager = pm
        self.has_project = True
        self.slide_count = len(pm.state.slides)
        return True

    load_pdf = load


class _FakeTTS:
    """The voice service: copies a prepared clip to the slide's audio path
    (or does what ``on_call`` says) and records every call."""

    def __init__(self, clip: Path, on_call=None):
        self.clip = clip
        self.calls: list = []
        self.on_call = on_call

    def generate_audio(self, text, voice_id, output_path, **kwargs):
        self.calls.append({"text": text, "voice_id": voice_id, "thread": threading.current_thread().name})
        if self.on_call is not None:
            verdict = self.on_call(len(self.calls))
            if verdict is not None:
                return verdict
        shutil.copy2(self.clip, output_path)
        return Path(output_path)


def _noise_mp3(ffmpeg: str, path: Path, seconds: float) -> Path:
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"anoisesrc=color=pink:amplitude=0.25:sample_rate=24000:duration={seconds}:seed=7",
                    "-ac", "1", "-c:a", "libmp3lame", "-q:a", "4", str(path)],
                   check=True, capture_output=True, timeout=60)
    return path


def _still(path: Path) -> Path:
    image = Image.new("RGB", (320, 180), (40, 120, 200))
    image.paste((230, 220, 40), (100, 50, 220, 130))
    image.save(path)
    return path


def _import_deck(owner: dict) -> str:
    return store.import_upload("deck.pptx", b"pptx-bytes", owner_id=owner["id"], owner_name=owner["display_name"])["id"]


def _deck_on_disk(ffmpeg: str, pid: str, slides: int = 3, with_audio: bool = True) -> ProjectManager:
    """The engine project of ``pid``'s deck as a render finds it: ``slides``
    slides with notes, a still each and - with ``with_audio`` - a 2 s
    narration clip each, already made, so the render exports nothing and
    synthesises nothing."""
    project_dir = store.PROJECTS_DIR / pid
    source = project_dir / "deck.pptx"
    pm = ProjectManager(get_project_dir(source, project_dir))
    pm.create_project(pptx_path=source, slide_notes=[f"Slide {i + 1} notes." for i in range(slides)], voice_id=VOICE)
    still = _still(pm.images_dir / "slide.png")
    for i in range(slides):
        pm.update_slide_image(i, still)
    if with_audio:
        clip = _noise_mp3(ffmpeg, pm.audio_dir / "clip.mp3", 2.0)
        for i in range(slides):
            audio = pm.audio_dir / f"slide_{i + 1:03d}_audio.mp3"
            shutil.copy2(clip, audio)
            pm.update_slide_audio(i, audio, 2.0, VOICE)
    return pm


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """The real processor over the deck on disk: the file item stand-in, and
    the voice service stubbed (``tts.on_call`` lets a test script it)."""
    monkeypatch.setattr(file_item_module, "FileItem", _DeckItem)
    tts = _FakeTTS(tmp_path / "tts_clip.mp3")
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)
    return {"tts": tts}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _poll(client: TestClient, job_id: str) -> dict:
    r = client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _wait_until(client, job_id, condition, what: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while True:
        job = _poll(client, job_id)
        if condition(job):
            return job
        assert job["status"] not in ("done", "error"), f"the job ended before {what}: {job}"
        assert time.time() < deadline, f"{what} never happened: {job}"
        time.sleep(0.02)


def _wait_end(client, job_id, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while True:
        job = _poll(client, job_id)
        if job["status"] in ("done", "error"):
            return job
        assert time.time() < deadline, f"the job did not end: {job}"
        time.sleep(0.02)


def _cancel_at(client, job_id, condition, what: str, label: str) -> tuple:
    """Wait for ``condition`` on the job, cancel it through the route, and
    return (the ended job, the seconds from the cancel to its end)."""
    _wait_until(client, job_id, condition, what)
    t0 = time.monotonic()
    r = client.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["cancel_requested"] is True
    job = _wait_end(client, job_id)
    elapsed = time.monotonic() - t0
    print(f"cancel-to-stop [{label}]: {elapsed:.2f} s")
    return job, elapsed


def _generate(client, pid, **body) -> str:
    r = client.post(f"/api/projects/{pid}/generate", json={"voice_id": VOICE, **body})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def _spy_ffmpeg(monkeypatch) -> list:
    """Every process started while the test runs, as it was started."""
    started: list = []
    real_popen = subprocess.Popen

    class _Spy(real_popen):
        def __init__(self, cmd, *args, **kwargs):
            super().__init__(cmd, *args, **kwargs)
            started.append(self)

    monkeypatch.setattr(subprocess, "Popen", _Spy)
    return started


def _left_behind(pid: str, tmp_path: Path) -> list:
    """What a render that did not finish must not leave: a part file of any
    kind in the project directory, and a scratch folder of any step."""
    project_dir = store.PROJECTS_DIR / pid
    left = [p.name for p in project_dir.glob("*.part.*")]
    temp = tmp_path / "temp"
    if temp.exists():
        left += [p.name for p in temp.iterdir()]
    return left


def _slow_encode(monkeypatch) -> None:
    """Every chunk of the deck render at real time: ffmpeg's ``realtime``
    filter on the chunk's graph, so a 10 s deck encodes for 10 s and a
    cancel lands while ffmpeg runs."""
    real_run = video_creator._run_until_done

    def run(cmd, log, timeout, cancelled, **kwargs):
        if "-progress" in cmd:
            graph = Path(kwargs["cwd"]) / cmd[cmd.index("-/filter_complex") + 1]
            text = graph.read_text(encoding="utf-8")
            at = text.rfind("[v]")
            graph.write_text(text[:at] + ",realtime[v]" + text[at + 3:], encoding="utf-8")
        return real_run(cmd, log, timeout, cancelled, **kwargs)

    monkeypatch.setattr(video_creator, "_run_until_done", run)


def _slow_formats(monkeypatch) -> None:
    """Every extra format read at real time (``-re``), so the WebM of a 10 s
    video takes 10 s and a cancel lands while ffmpeg writes it."""
    real_run = video_creator._run_until_done

    def run(cmd, log, timeout, cancelled, **kwargs):
        if "-progress" not in cmd and "-i" in cmd:
            at = cmd.index("-i")
            cmd = cmd[:at] + ["-re"] + cmd[at:]
        return real_run(cmd, log, timeout, cancelled, **kwargs)

    monkeypatch.setattr(video_creator, "_run_until_done", run)


# ── through the routes, on every real ffmpeg ──────────────────────────────────

@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_cancel_during_the_encode_leaves_the_previous_render_as_it_was(admin, cast, engine, monkeypatch, tmp_path, ffmpeg):
    """Render once, then cancel a second render while ffmpeg encodes: the
    job ends ``done`` and cancelled at the encode within about two seconds,
    nothing is left behind, the record is untouched and the first render's
    video and SRT are byte for byte what they were."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid

    first = _wait_end(admin, _generate(admin, pid))
    assert first["status"] == "done" and first["result"] == {"video": "deck.mp4", "outputs": {"srt": "deck.srt"}}, first
    before = store.get_project(pid)
    assert before["output_video"] == "deck.mp4" and before["outputs"] == {"srt": "deck.srt"}
    video, srt = project_dir / "deck.mp4", project_dir / "deck.srt"
    shas = {"video": _sha(video), "srt": _sha(srt)}
    assert _left_behind(pid, tmp_path) == []

    _slow_encode(monkeypatch)
    started = _spy_ffmpeg(monkeypatch)
    job_id = _generate(admin, pid)
    job, elapsed = _cancel_at(admin, job_id, lambda j: "Encoding" in j["message"], "the encode", f"encode {Path(ffmpeg).parent.parent.name}")

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "encode"}
    assert job["message"] == processing.RENDER_CANCELLED_BEFORE_VIDEO
    assert elapsed < STOP_WITHIN, f"the cancel took {elapsed:.2f} s to end the job"
    assert started, "ffmpeg ran"
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
    (encode,) = [proc for proc in started if "-progress" in proc.args]
    assert encode.returncode != 0, "the encode was stopped, not left to finish"
    assert _left_behind(pid, tmp_path) == []
    assert store.get_project(pid) == before, "the record is untouched"
    assert _sha(video) == shas["video"] and _sha(srt) == shas["srt"], "the previous render and its sidecar are byte-identical"
    assert jobs.active_for(pid) is None


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_cancel_of_a_preview_leaves_the_previous_preview(admin, cast, engine, monkeypatch, tmp_path, ffmpeg):
    """The 15-second preview goes through the same processor on the same
    check: cancelled while it encodes, the previous preview file and the
    record are as they were, and the line says so in the preview's terms."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid
    preview = project_dir / "deck_preview.mp4"
    preview.write_bytes(b"the previous preview")
    record = store.get_project(pid)
    record.update({"outputs": {"preview": preview.name}, "rendered_at": "2020-01-01T00:00:00+00:00"})
    store.save_project(record)
    before = store.get_project(pid)

    _slow_encode(monkeypatch)
    started = _spy_ffmpeg(monkeypatch)
    job_id = _generate(admin, pid, preview_seconds=15)
    job, elapsed = _cancel_at(admin, job_id, lambda j: "Encoding" in j["message"], "the preview's encode", "preview")

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "encode"}
    assert job["message"] == processing.PREVIEW_CANCELLED
    assert elapsed < STOP_WITHIN
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
    assert _left_behind(pid, tmp_path) == []
    assert store.get_project(pid) == before, "the record is untouched"
    assert preview.read_bytes() == b"the previous preview"
    assert not (project_dir / "deck.mp4").exists(), "a preview never writes the full video"


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_cancel_during_the_extra_formats_keeps_the_new_video_and_drops_the_unfinished_format(
    admin, cast, engine, monkeypatch, tmp_path, ffmpeg,
):
    """Cancelled while the WebM is written (after the video and its SRT are
    done): the job ends ``done`` and cancelled at the formats within about
    two seconds, the new video is recorded with its subtitles, the WebM and
    the GIF are absent from the record and from disk - the part file went
    with its ffmpeg, and the previous render's WebM (the kind in flight) and
    GIF (never started) are deleted, while its MP3, not asked for this
    time, is left as it was - and the line names what is missing."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid
    old_webm, old_gif, old_mp3 = project_dir / "deck.webm", project_dir / "deck.gif", project_dir / "deck_audio.mp3"
    old_webm.write_bytes(b"the previous webm")
    old_gif.write_bytes(b"the previous gif")
    old_mp3.write_bytes(b"the previous mp3")
    _previous_render(pid, {"webm": old_webm.name, "gif": old_gif.name, "mp3": old_mp3.name})

    _slow_formats(monkeypatch)
    started = _spy_ffmpeg(monkeypatch)
    job_id = _generate(admin, pid, export_webm=True, export_gif=True)
    job, elapsed = _cancel_at(admin, job_id, lambda j: "Writing WebM" in j["message"], "the WebM", f"formats {Path(ffmpeg).parent.parent.name}")

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "formats", "video": "deck.mp4", "outputs": {"srt": "deck.srt"}}
    assert job["message"] == (
        "Render cancelled once the video was written: the new video is kept, with its subtitles; "
        "the WebM and the GIF were not written. Render again for the full set."
    )
    assert elapsed < STOP_WITHIN, f"the cancel took {elapsed:.2f} s to end the job"
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
    (webm,) = [proc for proc in started if "libvpx-vp9" in proc.args]
    assert webm.returncode != 0, "the WebM encode was stopped, not left to finish"
    assert not any("fps=5" in arg for proc in started for arg in proc.args), "the GIF never started"
    assert _left_behind(pid, tmp_path) == []
    record = store.get_project(pid)
    assert record["output_video"] == "deck.mp4" and record["outputs"] == {"srt": "deck.srt"} and record["rendered_at"]
    video = project_dir / "deck.mp4"
    assert video.is_file() and video.stat().st_size > 1000, "the new video is there"
    assert (project_dir / "deck.srt").is_file()
    assert not old_webm.exists(), "the previous render's WebM - the kind in flight - is deleted"
    assert not old_gif.exists(), "and its GIF - never started - too"
    assert old_mp3.read_bytes() == b"the previous mp3", "a kind not asked for keeps its file"
    assert admin.get(f"/api/projects/{pid}/outputs/webm").status_code == 404, "and no longer served"
    assert admin.get(f"/api/projects/{pid}/outputs/gif").status_code == 404
    assert admin.get(f"/api/projects/{pid}/video").status_code == 200


def _previous_render(pid: str, outputs: dict) -> None:
    """The record of a render made earlier, naming ``outputs`` (the files are
    the test's to write)."""
    record = store.get_project(pid)
    record.update({"output_video": "deck.mp4", "outputs": outputs, "rendered_at": "2020-01-01T00:00:00+00:00"})
    store.save_project(record)


def _cancel_once_published(monkeypatch) -> dict:
    """A cancel that lands AFTER the video is published and BEFORE the
    subtitles: the chapter embed runs between the two, so it is held until
    the test has posted the cancel. Returns the events the test waits on."""
    gate = {"published": threading.Event(), "go": threading.Event(), "cancelled_at": 0.0}
    real_embed = video_creator.embed_chapters

    def embed(video_path, chapters):
        gate["published"].set()
        assert gate["go"].wait(10), "the test never released the embed"
        return real_embed(video_path, chapters)

    monkeypatch.setattr(video_creator, "embed_chapters", embed)
    return gate


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["slide", "whisper"])
@ON_EVERY_FFMPEG
def test_a_cancel_before_the_subtitles_keeps_the_new_video_and_deletes_the_previous_sidecars_it_asked_for(
    admin, cast, engine, monkeypatch, tmp_path, ffmpeg, mode,
):
    """The subtitles stage: the cancel lands once the video is written and
    before the subtitles are made (neither the SRT nor a Whisper pass runs).
    The new video is recorded with no sidecar, the previous render's
    subtitles (SRT, or Whisper's SRT and VTT) and GIF - asked for, not
    produced - are deleted, its WebM, not asked for, is kept, and the line
    names what was not written."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(processing, "_whisper_subtitles", lambda *a, **k: pytest.fail("the Whisper pass ran after the cancel"))
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid
    subs = ["deck.srt"] if mode == "slide" else ["deck.whisper.srt", "deck.whisper.vtt"]
    previous = {kind: name for kind, name in zip(("srt", "vtt"), subs)}
    previous.update({"gif": "deck.gif", "webm": "deck.webm"})
    for name in previous.values():
        (project_dir / name).write_bytes(f"the previous {name}".encode())
    _previous_render(pid, previous)

    gate = _cancel_once_published(monkeypatch)
    job_id = _generate(admin, pid, subtitles=mode, export_gif=True)
    assert gate["published"].wait(60), "the video was published"
    t0 = time.monotonic()
    r = admin.post(f"/api/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    gate["go"].set()
    job = _wait_end(admin, job_id)
    print(f"cancel-to-stop [subtitles {mode} {Path(ffmpeg).parent.parent.name}]: {time.monotonic() - t0:.2f} s")

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "subtitles", "video": "deck.mp4", "outputs": {}}
    assert job["message"] == (
        "Render cancelled once the video was written: the new video is kept; "
        "the subtitles and the GIF were not written. Render again for the full set."
    )
    record = store.get_project(pid)
    assert record["output_video"] == "deck.mp4" and record["outputs"] == {}
    assert (project_dir / "deck.mp4").stat().st_size > 1000, "the new video is there"
    for name in subs + ["deck.gif"]:
        assert not (project_dir / name).exists(), f"the previous render's {name} - asked for, not produced - is deleted"
    assert (project_dir / "deck.webm").read_bytes() == b"the previous deck.webm", "a kind not asked for keeps its file"
    assert _left_behind(pid, tmp_path) == []
    for kind in ("srt", "vtt", "gif"):
        assert admin.get(f"/api/projects/{pid}/outputs/{kind}").status_code == 404


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_format_that_fails_deletes_the_previous_renders_file_of_that_kind(admin, cast, engine, monkeypatch, tmp_path, ffmpeg):
    """Not a cancel: the WebM's encoder is missing, so the WebM fails, the
    MP3 is still made and the render completes ``done`` (never a cancel).
    The previous render's WebM - asked for, not produced - is deleted under
    the same rule, so no stale sidecar sits beside the new video."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid
    old_webm = project_dir / "deck.webm"
    old_webm.write_bytes(b"the previous webm")
    _previous_render(pid, {"srt": "deck.srt", "webm": old_webm.name})
    real_run = video_creator._run_until_done

    def run(cmd, log, timeout, cancelled, **kwargs):
        if "libvpx-vp9" in cmd:
            cmd = [c if c != "libvpx-vp9" else "nosuchencoder" for c in cmd]
        return real_run(cmd, log, timeout, cancelled, **kwargs)

    monkeypatch.setattr(video_creator, "_run_until_done", run)
    job = _wait_end(admin, _generate(admin, pid, export_webm=True, export_audio_only=True))

    assert job["status"] == "done", job
    assert job["result"] == {"video": "deck.mp4", "outputs": {"srt": "deck.srt", "mp3": "deck_audio.mp3"}}, "completed, not cancelled"
    assert store.get_project(pid)["outputs"] == {"srt": "deck.srt", "mp3": "deck_audio.mp3"}
    assert not old_webm.exists(), "the previous render's WebM - asked for, failed - is deleted"
    assert (project_dir / "deck_audio.mp3").stat().st_size > 100 and (project_dir / "deck.srt").is_file()
    assert _left_behind(pid, tmp_path) == []
    assert admin.get(f"/api/projects/{pid}/outputs/webm").status_code == 404


def test_stale_sidecars_are_the_asked_for_kinds_the_previous_record_named_and_this_render_did_not_produce():
    stale = processing.stale_sidecars
    previous = {"srt": "deck.srt", "webm": "deck.webm", "gif": "deck.gif", "mp3": "deck_audio.mp3", "preview": "deck_preview.mp4"}
    # The kind in flight and the kinds never started go; a kind not asked for stays; a produced kind stays.
    assert stale(previous, {"srt": "deck.srt"}, ["srt", "webm", "gif"]) == ["deck.webm", "deck.gif"]
    # The subtitles stage: nothing produced, everything asked for goes.
    assert stale(previous, {}, ["srt", "gif"]) == ["deck.srt", "deck.gif"]
    # Nothing asked for, or everything produced: nothing goes.
    assert stale(previous, {}, []) == []
    assert stale(previous, {"srt": "deck.srt", "webm": "deck.webm"}, ["srt", "webm"]) == []
    # A file the new record names under another kind is kept; a kind the previous record did not name is nothing.
    assert stale({"srt": "deck.whisper.srt"}, {"vtt": "deck.whisper.srt"}, ["srt", "vtt"]) == []
    assert stale({}, {}, ["srt", "webm", "gif", "mp3"]) == []
    # Each name once, in the asked-for order.
    assert stale({"srt": "same.srt", "vtt": "same.srt"}, {}, ["srt", "vtt"]) == ["same.srt"]
    # A changed subtitle mode (N7): the previous mode's files go, both of Whisper's together.
    whisper = {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt"}
    assert stale(whisper, {"srt": "deck.srt"}, ["srt"]) == ["deck.whisper.srt", "deck.whisper.vtt"], "whisper -> slide"
    assert stale({"srt": "deck.srt"}, whisper, ["srt", "vtt"]) == ["deck.srt"], "slide -> whisper"
    assert stale(whisper, whisper, ["srt", "vtt"]) == [], "whisper -> whisper: the same files"
    assert stale(whisper, {"webm": "deck.webm"}, ["webm"]) == [], "no subtitles asked for: the previous ones stay"
    # A produced kind under another name goes for the formats too (the same rule, no special case).
    assert stale({"webm": "old.webm"}, {"webm": "deck.webm"}, ["webm"]) == ["old.webm"]


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_per_slide_render_after_a_whisper_render_deletes_both_whisper_files(admin, cast, engine, monkeypatch, tmp_path, ffmpeg):
    """A changed subtitle mode (N7), through the route and to its end: the
    per-slide render produces ``deck.srt``, so ``srt`` is produced - under
    another name - and the previous Whisper render's SRT and VTT both go."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid
    for name in ("deck.whisper.srt", "deck.whisper.vtt", "deck.webm"):
        (project_dir / name).write_bytes(f"the previous {name}".encode())
    _previous_render(pid, {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt", "webm": "deck.webm"})

    job = _wait_end(admin, _generate(admin, pid, subtitles="slide"))
    assert job["status"] == "done" and job["result"] == {"video": "deck.mp4", "outputs": {"srt": "deck.srt"}}, job
    assert store.get_project(pid)["outputs"] == {"srt": "deck.srt"}
    assert (project_dir / "deck.srt").is_file()
    assert not (project_dir / "deck.whisper.srt").exists() and not (project_dir / "deck.whisper.vtt").exists()
    assert (project_dir / "deck.webm").read_bytes() == b"the previous deck.webm", "a kind not asked for keeps its file"
    assert admin.get(f"/api/projects/{pid}/outputs/vtt").status_code == 404
    assert _left_behind(pid, tmp_path) == []


def test_remove_stale_sidecars_deletes_inside_the_project_only(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "deck.webm").write_bytes(b"old")
    (tmp_path / "outside.txt").write_bytes(b"not mine")
    previous = {"webm": "deck.webm", "gif": "../outside.txt", "mp3": "deck_audio.mp3"}
    removed = processing.remove_stale_sidecars(project, previous, {}, ["webm", "gif", "mp3"])
    assert removed == ["deck.webm"], "the GIF's path escapes the project and is refused; the MP3 is not on disk"
    assert not (project / "deck.webm").exists()
    assert (tmp_path / "outside.txt").read_bytes() == b"not mine"


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", ONE_FFMPEG, ids=["one"])
def test_a_cancel_during_the_narration_stops_before_the_next_slide(admin, cast, engine, monkeypatch, ffmpeg):
    """Six slides without clips, two pool workers, a second per sentence:
    cancelled once the first clip is in, the render stops between slides -
    the slides in flight finish, the ones not started are never synthesised -
    and ends cancelled at the narration with the record untouched and no
    video written. The clips made are kept for the next render."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    pm = _deck_on_disk(ffmpeg, pid, slides=6, with_audio=False)
    _noise_mp3(ffmpeg, engine["tts"].clip, 1.0)
    engine["tts"].on_call = lambda n: time.sleep(1.0)
    before = store.get_project(pid)

    job_id = _generate(admin, pid)
    job, elapsed = _cancel_at(admin, job_id, lambda j: "Audio 1/6" in j["message"], "the first clip", "narration")

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "narration"}
    assert job["message"] == processing.RENDER_CANCELLED_BEFORE_VIDEO
    calls = engine["tts"].calls
    assert len(calls) < 6, "a slide was never synthesised"
    assert len({c["thread"] for c in calls}) == 2, "the narration ran on the pool's two workers"
    assert elapsed < 1.5 + 0.5, f"the cancel waited only for the sentences in flight: {elapsed:.2f} s"
    assert store.get_project(pid) == before, "the record is untouched"
    assert not (store.PROJECTS_DIR / pid / "deck.mp4").exists()
    pm.load()
    kept = [s.index for s in pm.state.slides if s.audio_path]
    assert 0 < len(kept) < 6, f"the clips already made are kept for the next render: {kept}"


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", ONE_FFMPEG, ids=["one"])
def test_a_cancelled_narration_does_not_record_the_new_speed_so_the_next_render_redoes_every_slide(
    admin, cast, engine, monkeypatch, ffmpeg,
):
    """A clip records its voice but not its speed; the next render tells a
    speed change only by the project's saved ``generation_speed``. Render at
    1.0, change the speed to 1.5, render again and cancel mid-narration:
    the saved speed must stay 1.0, so the third render synthesises EVERY
    slide at 1.5 - never a video with the unreached slides at the old
    speed. The plant: the stamp back before the narration check."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    pm = _deck_on_disk(ffmpeg, pid, slides=6, with_audio=False)
    _noise_mp3(ffmpeg, engine["tts"].clip, 1.0)
    tts = engine["tts"]
    real_generate = tts.generate_audio

    def generate(text, voice_id, output_path, **kwargs):
        tts.speeds.append(kwargs.get("speed"))
        return real_generate(text, voice_id, output_path, **kwargs)

    tts.speeds = []
    tts.generate_audio = generate

    first = _wait_end(admin, _generate(admin, pid, speed=1.0))
    assert first["status"] == "done" and first["result"]["video"] == "deck.mp4", first
    assert tts.speeds == [1.0] * 6
    pm.load()
    assert pm.state.generation_speed == 1.0

    tts.speeds = []
    tts.on_call = lambda n: time.sleep(1.0)
    job, _ = _cancel_at(admin, _generate(admin, pid, speed=1.5), lambda j: "Audio 1/6" in j["message"], "the first clip", "narration speed")
    assert job["result"] == {"cancelled": True, "stage": "narration"}
    assert 0 < len(tts.speeds) < 6 and set(tts.speeds) == {1.5}, "some slides were re-synthesised at 1.5, not all"
    pm.load()
    assert pm.state.generation_speed == 1.0, "the cancelled run did not record the new speed"

    tts.speeds = []
    tts.on_call = None
    third = _wait_end(admin, _generate(admin, pid, speed=1.5))
    assert third["status"] == "done" and third["result"]["video"] == "deck.mp4", third
    assert tts.speeds == [1.5] * 6, "every slide is synthesised at the new speed"
    pm.load()
    assert pm.state.generation_speed == 1.5, "recorded once the narration stage completed"


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", ONE_FFMPEG, ids=["one"])
def test_the_narration_worker_sees_the_cancel_during_a_retry_wait(admin, cast, engine, monkeypatch, ffmpeg):
    """The thread binding, deterministically. One slide, so one pool worker;
    its first synthesis fails, and the worker waits 3 s before the retry -
    a wait only the WORKER can cut short, by the job's check. Cancelled
    during it, the job ends within a second with the one call made. The
    plant: the route handing ``jobs.cancel_requested_here`` instead of the
    bound check - the worker never sees the cancel, retries after 3 s, and
    this test goes red on both counts (two calls, three seconds)."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid, slides=1, with_audio=False)
    _noise_mp3(ffmpeg, engine["tts"].clip, 1.0)
    failed_once = threading.Event()

    def first_fails(n: int):
        if n == 1:
            failed_once.set()
            return False
        return None

    engine["tts"].on_call = first_fails
    before = store.get_project(pid)

    job_id = _generate(admin, pid)
    assert failed_once.wait(10), "the first synthesis ran"
    job, elapsed = _cancel_at(admin, job_id, lambda j: True, "the retry wait", "narration retry wait")

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "narration"}
    assert len(engine["tts"].calls) == 1, "no retry after the cancel"
    assert elapsed < 1.0, f"the worker's wait saw the cancel: {elapsed:.2f} s"
    assert engine["tts"].calls[0]["thread"] != threading.current_thread().name
    assert store.get_project(pid) == before


def test_the_cancel_check_is_bound_to_the_job_not_to_the_thread(tmp_path):
    """Why the route binds the check (``jobs.cancel_check_here``) rather than
    handing ``jobs.cancel_requested_here``: with the cancel already asked,
    the pool worker's own check is what keeps it from calling the voice
    service, and that check answers right only when it is bound to the job
    - ``cancel_requested_here`` on a worker thread finds no job at all."""
    clip = tmp_path / "clip.bin"
    clip.write_bytes(b"clip")
    results = {}

    def work_with(check_for):
        def work(progress):
            jobs.cancel(jobs.current_job_id())
            pm = ProjectManager(tmp_path / f"proj_{check_for}")
            pm.create_project(pptx_path=tmp_path / "deck.pptx", slide_notes=["One."], voice_id=VOICE)
            tts = _FakeTTS(clip)
            processor = processing.VideoProcessor(
                voice_id=VOICE,
                cancel_check=jobs.cancel_check_here() if check_for == "bound" else jobs.cancel_requested_here,
            )
            processor._generate_audio_parallel(tts, pm, [0], 0, 1, "deck", None)
            return {"calls": len(tts.calls), "cancelled": processor.cancel_requested}
        return work

    for check_for in ("bound", "thread"):
        job_id = jobs.submit("test", work_with(check_for))
        deadline = time.time() + 10
        while (job := jobs.get(job_id))["status"] not in ("done", "error"):
            assert time.time() < deadline
            time.sleep(0.01)
        assert job["status"] == "done", job
        results[check_for] = job["result"]

    assert results["bound"] == {"calls": 0, "cancelled": True}, "the worker saw the cancel and never called the service"
    assert results["thread"]["calls"] == 1, "a check about the calling thread is blind on the worker"


def test_a_cancel_from_the_projects_owner_who_did_not_start_it_is_refused_and_the_render_finishes(cast, monkeypatch):
    """An editor who owns the project may watch the render an administrator
    started on it, but not stop it: 403 with the cancel rule's words, the
    flag stays down, and the render finishes with its video."""
    monkeypatch.setattr(file_item_module, "FileItem", _FakeFileItem)
    started, release = threading.Event(), threading.Event()

    class _Held(_FakeVideoProcessor):
        def process_files(self, files, output_dir, progress=None, preview_seconds=0):
            started.set()
            release.wait(10)
            return super().process_files(files, output_dir, progress, preview_seconds)

    monkeypatch.setattr(processing, "VideoProcessor", _Held)
    pid = _import_deck(cast["owner_account"])
    job_id = _generate(cast["admin"], pid)
    assert started.wait(5)
    try:
        r = cast["owner"].post(f"/api/jobs/{job_id}/cancel")
        assert r.status_code == 403 and r.json()["detail"] == CANCEL_FORBIDDEN
        assert jobs.get(job_id)["cancel_requested"] is False, "nothing was asked of the job"
        assert cast["owner"].get(f"/api/jobs/{job_id}").status_code == 200, "the owner may still watch it"
    finally:
        release.set()
    job = _wait_end(cast["admin"], job_id)
    assert job["status"] == "done" and job["result"] == {"video": "deck.mp4", "outputs": {"srt": "deck.srt"}}, job
    assert store.get_project(pid)["output_video"] == "deck.mp4"


def test_a_render_that_fails_is_still_an_error_not_a_cancel(admin, cast, monkeypatch):
    """A processor that produces nothing without having been cancelled is a
    failed job with the render's error, never a cancelled one."""
    monkeypatch.setattr(file_item_module, "FileItem", _FakeFileItem)

    class _Fails(_FakeVideoProcessor):
        def process_files(self, files, output_dir, progress=None, preview_seconds=0):
            return 0

    monkeypatch.setattr(processing, "VideoProcessor", _Fails)
    pid = _import_deck(cast["admin_account"])
    job = _wait_end(admin, _generate(admin, pid))
    assert job["status"] == "error", job
    assert job["error"] == "The video could not be rendered; the server log has the reason."
    assert job["result"] is None
    assert "output_video" not in store.get_project(pid)


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_an_encode_that_fails_is_an_error_and_leaves_the_previous_render(admin, cast, engine, monkeypatch, tmp_path, ffmpeg):
    """The real engine failing (its sound graph names a filter ffmpeg does
    not have) through the real route: the job ends ``error`` with the
    render's message, not cancelled, nothing is left behind and the
    previous render is untouched."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    pid = _import_deck(cast["admin_account"])
    _deck_on_disk(ffmpeg, pid)
    project_dir = store.PROJECTS_DIR / pid
    video = project_dir / "deck.mp4"
    video.write_bytes(b"the previous render")
    record = store.get_project(pid)
    record.update({"output_video": video.name, "outputs": {}, "rendered_at": "2020-01-01T00:00:00+00:00"})
    store.save_project(record)
    before = store.get_project(pid)
    real_graph = video_creator.deck_audio_graph
    monkeypatch.setattr(video_creator, "deck_audio_graph",
                        lambda *args, **kwargs: real_graph(*args, **kwargs).replace("aresample", "nosuchfilter", 1))
    started = _spy_ffmpeg(monkeypatch)

    job = _wait_end(admin, _generate(admin, pid))
    assert job["status"] == "error", job
    assert job["error"] == "The video could not be rendered; the server log has the reason."
    assert all(proc.poll() is not None for proc in started), "no ffmpeg left running"
    assert _left_behind(pid, tmp_path) == []
    assert store.get_project(pid) == before
    assert video.read_bytes() == b"the previous render"


# ── the export stage, on the processor ────────────────────────────────────────

class _FakeExporter:
    """Stands in for ``core.pptx_exporter.PPTXExporter``: writes a still per
    slide, and raises ``flag`` part-way when told to (a cancel arriving
    while PowerPoint exports)."""

    flag: dict = {}
    ran: list = []

    def __init__(self, path, images_dir):
        self.images_dir = Path(images_dir)
        self.backend = None

    def export_slides_as_images(self):
        _FakeExporter.ran.append(self)
        self.backend = "pillow"
        exported = []
        for i in range(3):
            if i == 1 and _FakeExporter.flag.get("raise_during"):
                _FakeExporter.flag["up"] = True
            exported.append(types.SimpleNamespace(index=i, image_path=_still(self.images_dir / f"slide_{i}.png"),
                                                  video_path=None, has_animation=False))
        return exported


def _bare_deck(tmp_path) -> _DeckItem:
    """A three-slide deck with notes and neither stills nor clips."""
    source = tmp_path / "deck.pptx"
    pm = ProjectManager(get_project_dir(source, tmp_path))
    pm.create_project(pptx_path=source, slide_notes=[f"Slide {i + 1}." for i in range(3)], voice_id=VOICE)
    item = _DeckItem(source, tmp_path)
    item.load()
    return item


@pytest.fixture
def exporter(monkeypatch):
    _FakeExporter.flag = {}
    _FakeExporter.ran = []
    monkeypatch.setattr(processing, "PPTXExporter", _FakeExporter)
    return _FakeExporter


def test_a_cancel_before_the_export_exports_nothing(tmp_path, exporter, monkeypatch):
    tts = _FakeTTS(tmp_path / "clip.bin")
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)
    item = _bare_deck(tmp_path)
    proc = processing.VideoProcessor(voice_id=VOICE, cancel_check=lambda: True)
    assert proc.process_files([item], tmp_path) == 0
    assert proc.cancelled is True and proc.cancel_stage == "export"
    assert exporter.ran == [] and tts.calls == [], "neither the export nor the narration started"
    assert proc.images_backend is None
    assert not any(s.image_path for s in item.project_manager.state.slides)


def test_a_cancel_during_the_export_lets_powerpoint_finish_and_stops_after_it(tmp_path, exporter, monkeypatch):
    """PowerPoint is not interrupted mid-export: the cancel that arrives during
    it is seen once the images are there - all of them, kept for the next
    render and recorded as this run's export - and nothing after it runs."""
    tts = _FakeTTS(tmp_path / "clip.bin")
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)
    item = _bare_deck(tmp_path)
    exporter.flag["raise_during"] = True
    proc = processing.VideoProcessor(voice_id=VOICE, cancel_check=lambda: bool(exporter.flag.get("up")))
    assert proc.process_files([item], tmp_path) == 0
    assert proc.cancelled is True and proc.cancel_stage == "export"
    assert len(exporter.ran) == 1 and proc.images_backend == "pillow"
    pm = item.project_manager
    assert all(s.image_path and Path(s.image_path).is_file() for s in pm.state.slides), "every still is there"
    assert tts.calls == [], "the narration never started"
    assert not (tmp_path / "deck.mp4").exists()


# ── the sidecar stages, on the processor ──────────────────────────────────────

def _processor(**kwargs) -> processing.VideoProcessor:
    return processing.VideoProcessor(voice_id=VOICE, **kwargs)


def test_a_cancel_seen_before_the_subtitles_writes_none_and_names_the_stage(monkeypatch, tmp_path):
    whisper, formats = [], []
    monkeypatch.setattr(processing, "_whisper_subtitles", lambda *a, **k: whisper.append(1) or {"srt": "x.whisper.srt", "vtt": "x.whisper.vtt"})
    monkeypatch.setattr(processing, "generate_extra_formats", lambda *a, **k: formats.append(1) or {})
    proc = _processor(subtitles="whisper", export_webm=True, cancel_check=lambda: True)
    out = proc._sidecar_outputs(None, tmp_path / "deck.mp4", None, "deck")
    assert out == {} and whisper == [] and formats == []
    assert proc.cancelled is True and proc.cancel_stage == "subtitles"


def test_the_whisper_pass_is_not_interrupted_and_its_files_are_kept(monkeypatch, tmp_path):
    """A cancel that arrives during the Whisper pass (one model call) is
    seen after it: the subtitles it wrote are kept and the formats never
    start - the stage is the formats'."""
    flag = {"up": False}

    def whisper(*a, **k):
        flag["up"] = True
        return {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt"}

    formats = []
    monkeypatch.setattr(processing, "_whisper_subtitles", whisper)
    monkeypatch.setattr(processing, "generate_extra_formats", lambda *a, **k: formats.append(1) or {})
    proc = _processor(subtitles="whisper", export_gif=True, cancel_check=lambda: flag["up"])
    out = proc._sidecar_outputs(None, tmp_path / "deck.mp4", None, "deck")
    assert out == {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt"} and formats == []
    assert proc.cancelled is True and proc.cancel_stage == "formats"


def test_a_cancel_once_everything_asked_for_is_written_is_too_late(monkeypatch, tmp_path):
    """The last check is before the last format: a cancel after it completes
    the run as if nobody had asked."""
    flag = {"up": False}

    def formats(*a, **k):
        flag["up"] = True
        return {"webm": "deck.webm"}

    monkeypatch.setattr(processing, "_whisper_subtitles", lambda *a, **k: {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt"})
    monkeypatch.setattr(processing, "generate_extra_formats", formats)
    proc = _processor(subtitles="whisper", export_webm=True, cancel_check=lambda: flag["up"])
    out = proc._sidecar_outputs(None, tmp_path / "deck.mp4", None, "deck")
    assert out == {"srt": "deck.whisper.srt", "vtt": "deck.whisper.vtt", "webm": "deck.webm"}
    assert proc.cancelled is False and proc.cancel_stage is None


def test_a_run_with_no_sidecars_asked_never_asks(monkeypatch, tmp_path):
    asked = []
    proc = _processor(subtitles="none", cancel_check=lambda: asked.append(1) or True)
    assert proc._sidecar_outputs(None, tmp_path / "deck.mp4", None, "deck") == {}
    assert asked == [] and proc.cancelled is False


# ── the extra formats, on the binary ──────────────────────────────────────────

def _small_video(ffmpeg: str, path: Path, seconds: float = 4.0) -> Path:
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=10:duration={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_extra_formats_are_written_aside_and_published_whole(monkeypatch, tmp_path, ffmpeg):
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video = _small_video(ffmpeg, tmp_path / "deck.mp4")
    told = []
    out = processing.generate_extra_formats(video, webm=True, gif=True, audio_only=True, progress=told.append)
    assert out == {"webm": "deck.webm", "gif": "deck.gif", "mp3": "deck_audio.mp3"}
    assert told == ["Writing WebM...", "Writing GIF...", "Writing MP3..."]
    for name in out.values():
        assert (tmp_path / name).stat().st_size > 100
    assert list(tmp_path.glob("*.part.*")) == [], "no part file is left"
    assert list((tmp_path / "temp").iterdir()) == [], "the scratch is removed"


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_cancel_while_a_format_is_written_kills_ffmpeg_and_removes_its_part_file(monkeypatch, tmp_path, ffmpeg):
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video = _small_video(ffmpeg, tmp_path / "deck.mp4")
    _slow_formats(monkeypatch)
    started = _spy_ffmpeg(monkeypatch)
    part = processing.extra_format_part(tmp_path / "deck.webm")
    seen = {"part": False}

    def cancel_once_writing() -> bool:
        # ffmpeg opens the part file as it starts (VP9 then buffers frames,
        # so its size stays at zero for a while): the file's presence is the
        # sign the WebM is being written.
        if part.is_file():
            seen["part"] = True
        return seen["part"]

    t0 = time.monotonic()
    out = processing.generate_extra_formats(video, webm=True, gif=True, cancel_check=cancel_once_writing)
    elapsed = time.monotonic() - t0
    assert out == {} and seen["part"], "cancelled while the WebM was being written"
    assert not part.exists() and not (tmp_path / "deck.webm").exists()
    assert not (tmp_path / "deck.gif").exists(), "the GIF never started"
    assert len(started) == 1 and started[0].poll() is not None and started[0].returncode != 0, "the WebM's ffmpeg was stopped"
    assert elapsed < STOP_WITHIN, f"a 4 s WebM at real time, stopped at the first poll after it opened its file: {elapsed:.2f} s"
    assert list((tmp_path / "temp").iterdir()) == []


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_format_that_fails_leaves_no_part_file_and_the_rest_are_made(monkeypatch, tmp_path, ffmpeg):
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video = _small_video(ffmpeg, tmp_path / "deck.mp4")
    real_run = video_creator._run_until_done

    def run(cmd, log, timeout, cancelled, **kwargs):
        if "libvpx-vp9" in cmd:
            cmd = [c if c != "libvpx-vp9" else "nosuchencoder" for c in cmd]
        return real_run(cmd, log, timeout, cancelled, **kwargs)

    monkeypatch.setattr(video_creator, "_run_until_done", run)
    out = processing.generate_extra_formats(video, webm=True, audio_only=True)
    assert out == {"mp3": "deck_audio.mp3"}
    assert list(tmp_path.glob("*.part.*")) == [] and not (tmp_path / "deck.webm").exists()


# ── the closing line ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("stage", ["export", "narration", "encode"])
def test_before_the_video_the_line_says_the_project_is_as_it_was(stage):
    assert processing.cancelled_render_line(stage, False, (), ("srt", "webm")) == processing.RENDER_CANCELLED_BEFORE_VIDEO
    assert processing.cancelled_render_line(stage, True, (), ()) == processing.PREVIEW_CANCELLED


def test_after_the_video_the_line_names_what_was_kept_and_what_was_not():
    line = processing.cancelled_render_line
    assert line("subtitles", False, {}, ["srt", "webm", "gif", "mp3"]) == (
        "Render cancelled once the video was written: the new video is kept; "
        "the subtitles, the WebM, the GIF and the MP3 were not written. Render again for the full set."
    )
    assert line("formats", False, {"srt": "a", "vtt": "b"}, ["srt", "vtt", "gif"]) == (
        "Render cancelled once the video was written: the new video is kept, with its subtitles; "
        "the GIF was not written. Render again for the full set."
    )
    assert line("formats", False, {"srt": "a", "webm": "c"}, ["srt", "webm", "mp3"]) == (
        "Render cancelled once the video was written: the new video is kept, with its subtitles and WebM; "
        "the MP3 was not written. Render again for the full set."
    )
    assert line("formats", False, {"webm": "c"}, ["webm"]).endswith("is kept with everything asked for.")
