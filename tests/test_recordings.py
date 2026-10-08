"""Screen recordings (T3): the recorder's chunks become a video project.

- the chunk rules: out of order is fine, a duplicate replaces, a hole is
  refused with its numbers (never papered over), strays beyond the count
  are left out, and ffmpeg reads the chunks IN PLACE through a list of their
  names - no assembled copy is ever written;
- the disk: a chunk is refused (507) when it would leave under 1 GB free and
  (413) past the recording's cap, and a streamed body is refused (413) the
  moment it passes the chunk limit, never read whole first;
- LIVE: a recording its recorder is still making - heard from by a chunk or
  a heartbeat within 20 s - cannot be saved or discarded from the Projects
  page (409); its recorder can; one whose recorder went quiet can;
- SAVING: reported only while its job lives; a save a restart cut short is
  reset at startup and comes back savable;
- the length: the conversion's wait and the held last frame come from the
  chunks the server holds, never from a duration of 0; a recording with a
  hole is saved as the part before it only when the loss is accepted;
- making a recording is this computer's only (start, chunk, heartbeat:
  ``screen_user``); a recording is its owner's (or an admin's): another
  editor is refused every route that touches it and is not shown it, and the
  save job is shown to its owner (or an admin);
- the startup's reset of interrupted saves is best-effort: a recording it
  cannot rewrite is logged and left, and the app still starts;
- the transcode's argv is the app's encode - libx264 ``medium``, H.264 High,
  4:2:0, constant frame rate at the recorder's rate, the region cropped
  first at its exact origin with even sides, the last frame held to the
  sound's end, the unity up-mix at 48 kHz, AAC 192 kbit/s, ``+faststart`` -
  read from ``core.video_creator``'s own constants;
- the names: a window title becomes a safe file stem, the default name
  carries the date and time;
- on every real ffmpeg on the machine (the one the app resolves and, where
  the product is installed, the 7.1 it ships): a VP9/Opus WebM sent as
  three byte-split chunks (what MediaRecorder's timeslices are) comes back as
  a VIDEO PROJECT whose MP4 reads back as H.264 High yuv420p at 30 fps, AAC
  48 kHz stereo, index first, named as asked, with the chunks gone; a
  picture that ends before its sound is held to the sound's end; an odd
  region origin is cropped exactly; the part before a lost chunk is saved.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import struct
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, recordings
from services import projects as store
from utils.config import config

from test_chapters import _ffprobe_beside
from test_generation_options import REAL_FFMPEGS, needs_ffmpeg

PASSWORD = "Owner-pass-12345"
#: The desktop shell's window loads the backend at 127.0.0.1: THIS computer.
LOOPBACK = ("127.0.0.1", 50000)
#: A browser on another machine of the team server's network.
REMOTE = ("10.20.30.40", 50123)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The store, the recordings folder, the auth database and the config
    under tmp_path; a signed-in admin and a signed-in editor ("other"), both
    on this computer."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", tmp_path / "recordings")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    from api.app import app

    with TestClient(app, client=LOOPBACK) as admin:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert admin.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        auth_store.create_user("other", PASSWORD, "Otto Other", role="editor", must_change_password=False)
        other = TestClient(app, client=LOOPBACK)
        assert other.post("/api/auth/login", json={"username": "other", "password": PASSWORD}).status_code == 200
        yield {"admin": admin, "other": other, "tmp": tmp_path, "app": app}


def _signed_in_from(app, client: tuple[str, int], username: str = "admin", password: str = "admin") -> TestClient:
    c = TestClient(app, client=client)
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


def _start(client: TestClient, **over) -> str:
    body = {"name": "Notepad", "frame_rate": 30, "width": 640, "height": 480, "source": {"kind": "window", "window_title": "Notepad"}}
    body.update(over)
    r = client.post("/api/recordings", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _put(client: TestClient, rid: str, seq: int, data: bytes):
    return client.put(f"/api/recordings/{rid}/chunks/{seq}", content=data, headers={"Content-Type": "video/webm"})


def _finish(client: TestClient, rid: str, **body):
    return client.post(f"/api/recordings/{rid}/finish", json=body)


def _go_quiet(monkeypatch, seconds: float = recordings.LIVE_WINDOW_SECONDS + 1):
    """The recorder has not been heard from for ``seconds``: the clock moves on."""
    later = time.time() + seconds
    monkeypatch.setattr(recordings, "_now", lambda: later)


def _wait_job(job_id: str, seconds: float = 180.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = jobs.get(job_id)
        if job and job["status"] in ("done", "error"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {jobs.get(job_id)}")


def _fake_transcode(monkeypatch) -> dict:
    """Stand in for ffmpeg: record what the save would run - the argv, the
    wait, the folder it runs in, what is in that folder, the chunk list - and
    answer "cancelled", which keeps the recording."""
    import core.video_creator as vc

    seen: dict = {}

    def fake(cmd, log, timeout, cancelled, on_poll=None, cwd=None):
        seen.update(cmd=cmd, timeout=timeout, cwd=cwd, files=sorted(p.name for p in Path(cwd).iterdir()),
                    listing=(Path(cwd) / recordings.CHUNK_LIST).read_text(encoding="utf-8"))
        return "cancelled", None

    monkeypatch.setattr(vc, "_run_until_done", fake)
    monkeypatch.setattr(recordings, "FFMPEG_PATH", "FF")
    return seen


@contextmanager
def _a_save_running(rid: str | None = None, user_id: str | None = None):
    """A save job that holds until the block ends; with ``rid``, that
    recording is marked as the one it saves."""
    gate = threading.Event()
    job_id = jobs.start_single(recordings.JOB_KIND, lambda progress: gate.wait(30), user_id=user_id)
    try:
        if rid:
            meta = recordings.read_meta(rid)
            meta.update(status="finishing", job_id=job_id)
            recordings._write_meta(rid, meta)
        yield job_id
    finally:
        gate.set()
        _wait_job(job_id)


# -- names -------------------------------------------------------------------------

def test_a_window_title_becomes_a_safe_file_stem():
    assert recordings.safe_stem("C:\\Windows\\system32\\cmd.exe") == "CWindowssystem32cmd.exe"
    assert recordings.safe_stem("Budget 2026 - Excel") == "Budget-2026---Excel"
    assert recordings.safe_stem("Quarterly report - Notes / Browser") == "Quarterly-report---Notes-Browser"
    assert recordings.safe_stem("...") == "screen-recording"
    assert recordings.safe_stem("") == "screen-recording"
    assert len(recordings.safe_stem("x" * 200)) == 60


def test_the_default_name_carries_the_local_date_and_time():
    from datetime import datetime

    assert recordings.default_name(datetime(2026, 10, 6, 11, 42, 7)) == "Screen recording 2026-10-06 11-42"


# -- the chunk rules -----------------------------------------------------------------

def test_missing_chunks_names_every_hole_below_the_count_and_contiguous_counts_the_unbroken_start():
    assert recordings.missing_chunks([0, 1, 2], 3) == []
    assert recordings.missing_chunks([0, 2, 5], 4) == [1, 3]
    assert recordings.missing_chunks([], 2) == [0, 1]
    assert recordings.contiguous([0, 1, 3]) == 2
    assert recordings.contiguous([1, 2]) == 0
    assert recordings.contiguous([]) == 0


def _listed(rid: str) -> list[Path]:
    """The chunk files the list names, resolved in the recording's folder."""
    folder = recordings.RECORDINGS_DIR / rid
    return [folder / name for name in (folder / recordings.CHUNK_LIST).read_text(encoding="utf-8").splitlines()]


def test_chunks_arrive_in_any_order_a_duplicate_replaces_and_ffmpeg_reads_them_in_place(rig):
    c = rig["admin"]
    rid = _start(c)
    assert _put(c, rid, 2, b"CC").json() == {"received": 1, "bytes": 2}
    assert _put(c, rid, 0, b"AAAA").json() == {"received": 2, "bytes": 6}
    assert _put(c, rid, 1, b"XX").json()["received"] == 3
    assert _put(c, rid, 1, b"BBB").json() == {"received": 3, "bytes": 9}  # the resend replaces
    listing = recordings.assemble(rid, 3)
    assert listing.name == recordings.CHUNK_LIST
    assert listing.read_text(encoding="utf-8") == "chunk-000000.webm\nchunk-000001.webm\nchunk-000002.webm\n"
    assert b"".join(p.read_bytes() for p in _listed(rid)) == b"AAAA" + b"BBB" + b"CC"
    # No assembled copy: the folder holds the chunks, the meta and the list - nothing the size of the recording.
    assert sorted(p.name for p in (rig["tmp"] / "recordings" / rid).iterdir()) == [
        "chunk-000000.webm", "chunk-000001.webm", "chunk-000002.webm", recordings.CHUNK_LIST, "meta.json"]


def test_a_hole_is_refused_with_its_numbers_and_a_stray_is_left_out(rig):
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    _put(c, rid, 2, b"C")
    _put(c, rid, 7, b"stray")
    with pytest.raises(recordings.RecordingError, match=r"Chunks 1 of 3 never arrived"):
        recordings.assemble(rid, 3)
    assert not (rig["tmp"] / "recordings" / rid / recordings.CHUNK_LIST).exists()  # a refused list is not written
    _put(c, rid, 1, b"B")
    recordings.assemble(rid, 3)
    assert b"".join(p.read_bytes() for p in _listed(rid)) == b"ABC"  # 7 is beyond the count: not listed
    # Without a count every chunk present is taken, in order.
    recordings.assemble(rid, None)
    assert b"".join(p.read_bytes() for p in _listed(rid)) == b"ABCstray"


def test_the_chunk_route_refuses_a_bad_number_an_empty_body_and_an_oversize_one(rig, monkeypatch):
    c = rig["admin"]
    rid = _start(c)
    assert _put(c, rid, recordings.MAX_CHUNKS, b"x").status_code == 400
    assert _put(c, rid, 0, b"").status_code == 400
    monkeypatch.setattr(recordings, "MAX_CHUNK_BYTES", 4)
    # Too large is refused from the Content-Length header, before the body is read (413).
    assert _put(c, rid, 0, b"12345").status_code == 413
    assert _put(c, rid, 0, b"1234").status_code == 200
    # The service itself refuses the same.
    with pytest.raises(ValueError, match="may not exceed"):
        recordings.put_chunk(rid, 1, b"12345")
    assert _put(c, "0" * 12, 0, b"x").status_code == 404


def test_a_streamed_chunk_with_no_length_is_refused_past_the_limit_and_never_stored(rig, monkeypatch):
    """A chunked-transfer body has no Content-Length, so the header check cannot refuse it; the route
    refuses it (413) and stores nothing. What this test CANNOT show: Starlette's TestClient sends the
    whole body before the app reads any of it, so it says nothing about how much was read - that the
    route stops reading at the limit is the next test's, on the helper itself (and the review measured
    real uvicorn refusing after 3 of 6 pieces)."""
    monkeypatch.setattr(recordings, "MAX_CHUNK_BYTES", 8)
    c = rig["admin"]
    rid = _start(c)
    r = c.put(f"/api/recordings/{rid}/chunks/0", content=iter([b"1234", b"5678", b"9abc", b"defg"]),
              headers={"Content-Type": "video/webm"})
    assert r.status_code == 413, r.text
    assert "Content-Length" not in r.request.headers
    assert recordings.chunks_present(rid) == []
    ok = c.put(f"/api/recordings/{rid}/chunks/0", content=iter([b"1234", b"5678"]), headers={"Content-Type": "video/webm"})
    assert ok.status_code == 200 and ok.json() == {"received": 1, "bytes": 8}


def test_the_body_is_read_only_up_to_the_limit():
    """The route's reader stops pulling the body the moment it passes the limit: of six 4-byte pieces
    against an 8-byte limit, the third is the last one read."""
    from fastapi import HTTPException

    from api.routers.recordings import _capped_body

    class Streamed:
        headers: dict = {}

        def __init__(self):
            self.pulled = 0

        async def stream(self):
            for _ in range(6):
                self.pulled += 1
                yield b"abcd"

    request = Streamed()
    with pytest.raises(HTTPException) as refused:
        asyncio.run(_capped_body(request, 8))
    assert refused.value.status_code == 413
    assert request.pulled == 3
    within = Streamed()
    assert asyncio.run(_capped_body(within, 24)) == b"abcd" * 6 and within.pulled == 6


# -- the disk (M4) --------------------------------------------------------------------

def test_a_chunk_that_would_leave_under_1_gb_free_is_refused_and_what_arrived_is_kept(rig, monkeypatch):
    c = rig["admin"]
    rid = _start(c)
    assert _put(c, rid, 0, b"AAAA").status_code == 200
    monkeypatch.setattr(recordings, "free_bytes", lambda path=None: recordings.MIN_CHUNK_FREE_BYTES + 3)
    r = _put(c, rid, 1, b"BBBB")  # 4 bytes would leave 1 GB less 1
    assert r.status_code == 507, r.text
    assert "1 GB" in r.json()["detail"] and "kept" in r.json()["detail"]
    assert [p.name for p in recordings.chunks_present(rid)] == ["chunk-000000.webm"]
    assert _put(c, rid, 1, b"BBB").status_code == 200  # exactly at the floor is fine
    assert recordings.MIN_CHUNK_FREE_BYTES == 1024 ** 3


def test_a_recording_is_capped_and_a_resend_is_not_counted_twice(rig, monkeypatch):
    monkeypatch.setattr(recordings, "MAX_RECORDING_BYTES", 10)
    c = rig["admin"]
    rid = _start(c)
    assert _put(c, rid, 0, b"AAAA").status_code == 200
    assert _put(c, rid, 1, b"BBBB").status_code == 200
    r = _put(c, rid, 2, b"CCC")
    assert r.status_code == 413 and "most one recording may take" in r.json()["detail"], r.text
    assert _put(c, rid, 1, b"bbbbbb").status_code == 200  # replaces chunk 1: 4 + 6 = 10, at the cap
    assert len(recordings.chunks_present(rid)) == 2


def test_the_cap_holds_a_two_hour_4k_recording():
    """The recorder asks for 8 Mbit/s of video and 192 kbit/s of sound whatever the picture's size:
    two hours of that is about 7.4 GB, well inside the cap, which is stated in limits.md."""
    two_hours = 2 * 60 * 60 * (8_000_000 + 192_000) / 8
    assert two_hours * 1.5 < recordings.MAX_RECORDING_BYTES == 16 * 1024 ** 3
    limits = (Path(__file__).resolve().parent.parent / "docs" / "reference" / "limits.md").read_text(encoding="utf-8")
    assert "16 GB" in limits and "1 GB" in limits


# -- ownership ------------------------------------------------------------------------

def test_someone_elses_recording_is_refused_and_the_list_is_filtered(rig, monkeypatch):
    """Another signed-in editor - on this computer, so the screen guard lets them in - is refused every
    route that touches the admin's recording (chunk, heartbeat, release, finish, discard), changes
    nothing, and is not shown it; an administrator sees everyone's."""
    admin, other = rig["admin"], rig["other"]
    mine = _start(other, name="Mine")
    _put(other, mine, 0, b"A")
    theirs = _start(admin, name="Admins")
    _put(admin, theirs, 0, b"A")
    _go_quiet(monkeypatch)  # not live: a refusal below is the owner rule, never the live rule
    assert _put(other, theirs, 1, b"B").status_code == 403
    assert other.post(f"/api/recordings/{theirs}/heartbeat").status_code == 403
    assert other.post(f"/api/recordings/{theirs}/release").status_code == 403
    assert _finish(other, theirs, chunks=1).status_code == 403
    assert other.delete(f"/api/recordings/{theirs}").status_code == 403
    # Nothing was changed: no chunk added, no save started, still there and still the admin's.
    assert [p.name for p in recordings.chunks_present(theirs)] == ["chunk-000000.webm"]
    assert jobs.active_of_kind(recordings.JOB_KIND) is None
    assert recordings.read_meta(theirs)["status"] == "recording"
    # The list: the editor sees their own only; an administrator sees both.
    assert [r["id"] for r in other.get("/api/recordings").json()["recordings"]] == [mine]
    assert {r["id"] for r in admin.get("/api/recordings").json()["recordings"]} == {mine, theirs}


# -- live (M1) ------------------------------------------------------------------------

def test_a_live_recording_is_not_saved_or_discarded_from_the_page(rig):
    c = rig["admin"]
    rid = _start(c)
    assert _put(c, rid, 0, b"A").status_code == 200
    listed = c.get("/api/recordings").json()["recordings"]
    assert [(r["id"], r["status"]) for r in listed] == [(rid, "recording")]
    r = _finish(c, rid, chunks=1)
    assert r.status_code == 409 and "still being made" in r.json()["detail"], r.text
    r = c.delete(f"/api/recordings/{rid}")
    assert r.status_code == 409 and "still being made" in r.json()["detail"], r.text
    assert jobs.active_of_kind(recordings.JOB_KIND) is None
    # The recorder carries on: its next chunk is taken.
    assert _put(c, rid, 1, b"B").status_code == 200


def test_the_recorder_itself_may_save_or_discard_its_live_recording(rig, monkeypatch):
    seen = _fake_transcode(monkeypatch)
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    r = _finish(c, rid, chunks=1, duration_ms=4000, from_recorder=True)
    assert r.status_code == 200, r.text
    assert _wait_job(r.json()["job_id"])["result"] == {"cancelled": True}
    assert seen["listing"] == "chunk-000000.webm\n"
    other = _start(c)
    _put(c, other, 0, b"A")
    assert c.delete(f"/api/recordings/{other}", params={"from_recorder": "true"}).status_code == 204


def test_a_client_on_another_machine_cannot_claim_to_be_the_recorder(rig):
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    remote = _signed_in_from(rig["app"], REMOTE)
    assert _finish(remote, rid, chunks=1, from_recorder=True).status_code == 409
    assert remote.delete(f"/api/recordings/{rid}", params={"from_recorder": "true"}).status_code == 409
    assert recordings.read_meta(rid) is not None


def test_a_heartbeat_keeps_a_paused_recording_live_and_a_quiet_one_lapses(rig, monkeypatch):
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    t0 = time.time()
    clock = {"now": t0 + 15}
    monkeypatch.setattr(recordings, "_now", lambda: clock["now"])
    assert c.post(f"/api/recordings/{rid}/heartbeat").status_code == 204  # paused: no chunk, still there
    clock["now"] = t0 + 30
    assert c.get("/api/recordings").json()["recordings"][0]["status"] == "recording"
    assert _finish(c, rid, chunks=1).status_code == 409
    clock["now"] = t0 + 15 + recordings.LIVE_WINDOW_SECONDS + 1  # the recorder crashed: nothing since
    assert c.get("/api/recordings").json()["recordings"][0]["status"] == "stopped"
    assert c.delete(f"/api/recordings/{rid}").status_code == 204  # now the page may
    assert c.post(f"/api/recordings/{rid}/heartbeat").status_code == 404


def test_a_recording_its_recorder_let_go_of_is_offered_at_once(rig):
    """The recorder failed, or the disk filled, after chunks landed: it releases the recording instead of
    deleting it (review MINOR 2), and the page may save or discard it straight away."""
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    assert c.post(f"/api/recordings/{rid}/release").status_code == 204
    assert c.get("/api/recordings").json()["recordings"][0]["status"] == "stopped"
    assert c.post(f"/api/recordings/{rid}/heartbeat").status_code == 204  # a late beat does not revive it
    assert c.get("/api/recordings").json()["recordings"][0]["status"] == "stopped"
    assert c.delete(f"/api/recordings/{rid}").status_code == 204
    assert c.post(f"/api/recordings/{rid}/release").status_code == 404


def test_heartbeats_and_chunks_are_this_computers_only(rig):
    """Recording ingest - start, chunk, heartbeat - is the desktop shell's on this computer (MINOR 10);
    test_captures checks the 403 and the audit row for each."""
    c = rig["admin"]
    rid = _start(c)
    remote = _signed_in_from(rig["app"], REMOTE)
    assert remote.post("/api/recordings", json={"name": "x"}).status_code == 403
    assert _put(remote, rid, 0, b"A").status_code == 403
    assert remote.post(f"/api/recordings/{rid}/heartbeat").status_code == 403
    assert recordings.chunks_present(rid) == []


# -- saving (M2) ----------------------------------------------------------------------

def test_a_save_in_progress_is_reported_and_takes_no_save_discard_or_chunk(rig, monkeypatch):
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    _go_quiet(monkeypatch)
    with _a_save_running(rid):
        assert c.get("/api/recordings").json()["recordings"][0]["status"] == "finishing"
        assert _finish(c, rid, chunks=1).status_code == 409
        assert c.delete(f"/api/recordings/{rid}").status_code == 409
        assert _put(c, rid, 1, b"B").status_code == 409
        assert recordings.reset_stale() == 0  # its job is alive: not stale
    assert recordings.read_meta(rid)["status"] == "finishing"  # (the stand-in job wrote nothing back)


def test_a_save_cut_short_by_a_restart_comes_back_savable(rig, monkeypatch):
    """The review's probe: a "finishing" record whose job no longer exists. It is reported as stopped,
    can be saved again, and the startup sets it back for good."""
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    meta = recordings.read_meta(rid)
    meta.update(status="finishing", job_id="feedfacecafe")  # no such job: the process that ran it is gone
    recordings._write_meta(rid, meta)
    _go_quiet(monkeypatch)
    assert [(r["id"], r["status"]) for r in c.get("/api/recordings").json()["recordings"]] == [(rid, "stopped")]
    # The startup resets it.
    with TestClient(rig["app"], client=LOOPBACK):
        pass
    assert (recordings.read_meta(rid)["status"], recordings.read_meta(rid)["job_id"]) == ("stopped", None)
    # And it can be saved: here the save fails for want of ffmpeg, and the recording stays offered.
    monkeypatch.setattr(recordings, "FFMPEG_PATH", None)
    r = _finish(c, rid, chunks=1)
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "error" and "ffmpeg is not available" in job["error"]
    assert recordings.read_meta(rid)["status"] == "stopped"
    assert [x["id"] for x in c.get("/api/recordings").json()["recordings"]] == [rid]


def test_one_save_at_a_time_across_recordings(rig, monkeypatch):
    """Saves are one job kind, one at a time (``jobs.start_single``, its check and its submit under one
    lock): another recording's save while one runs is refused, and nothing is marked as saving."""
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"A")
    _go_quiet(monkeypatch)
    with _a_save_running():
        r = _finish(c, rid, chunks=1)
        assert r.status_code == 409 and "already running" in r.json()["detail"], r.text
        assert recordings.read_meta(rid)["status"] == "recording"


def test_a_save_is_cancelled_through_the_jobs_route_and_keeps_the_chunks(rig, monkeypatch):
    """The Projects page's Cancel on "Saving the recording" is POST /api/jobs/{id}/cancel: allowed for
    the save's starter, it stops the conversion, and the recording stays, stopped, with its chunks."""
    import core.video_creator as vc

    monkeypatch.setattr(recordings, "FFMPEG_PATH", "FF")
    started = threading.Event()

    def until_cancelled(cmd, log, timeout, cancelled, on_poll=None, cwd=None):
        started.set()
        deadline = time.time() + 20
        while not cancelled() and time.time() < deadline:
            time.sleep(0.02)
        return ("cancelled" if cancelled() else "timeout"), None

    monkeypatch.setattr(vc, "_run_until_done", until_cancelled)
    c = rig["admin"]
    rid = _start(c)
    _put(c, rid, 0, b"x")
    _go_quiet(monkeypatch)
    job_id = _finish(c, rid, chunks=1).json()["job_id"]
    assert started.wait(10)
    assert c.post(f"/api/jobs/{job_id}/cancel").status_code == 200
    job = _wait_job(job_id)
    assert job["status"] == "done" and job["result"] == {"cancelled": True}, job
    assert [p.name for p in recordings.chunks_present(rid)] == ["chunk-000000.webm"]
    assert c.get("/api/recordings").json()["recordings"][0]["status"] == "stopped"
    # Somebody else may not cancel a save that is not theirs.
    job_id = _finish(c, rid, chunks=1).json()["job_id"]
    assert started.wait(10)
    assert rig["other"].post(f"/api/jobs/{job_id}/cancel").status_code == 403
    c.post(f"/api/jobs/{job_id}/cancel")
    _wait_job(job_id)


@pytest.mark.skipif(os.name != "nt", reason="a read-only file refuses a replace on Windows only")
def test_the_startup_carries_on_past_a_recording_it_cannot_reset(rig, monkeypatch):
    """Two saves cut short by a restart; one's meta.json is read-only, so it cannot be rewritten
    (WinError 5). The startup logs it and resets the other, and the app starts; the stuck one is still
    offered (no job is alive for it)."""
    c = rig["admin"]
    stuck, fine = _start(c, name="Stuck"), _start(c, name="Fine")
    for rid in (stuck, fine):
        _put(c, rid, 0, b"A")
        meta = recordings.read_meta(rid)
        meta.update(status="finishing", job_id="feedfacecafe")
        recordings._write_meta(rid, meta)
    _go_quiet(monkeypatch)
    locked = rig["tmp"] / "recordings" / stuck / "meta.json"
    os.chmod(locked, stat.S_IREAD)
    try:
        with TestClient(rig["app"], client=LOOPBACK) as again:
            assert again.get("/api/system/health").status_code == 200
        assert recordings.read_meta(stuck)["status"] == "finishing"  # could not be rewritten
        assert recordings.read_meta(fine)["status"] == "stopped"
        assert not list((rig["tmp"] / "recordings" / stuck).glob("meta.*.tmp"))  # no stray temp file
        statuses = {r["id"]: r["status"] for r in c.get("/api/recordings").json()["recordings"]}
        assert statuses == {stuck: "stopped", fine: "stopped"}
    finally:
        os.chmod(locked, stat.S_IREAD | stat.S_IWRITE)


def test_the_save_job_route_shows_only_the_callers_save(rig):
    admin, other = rig["admin"], rig["other"]
    admin_id = admin.get("/api/auth/me").json()["user"]["id"]
    other_id = other.get("/api/auth/me").json()["user"]["id"]
    assert admin.get("/api/recordings/job").json() == {"active_job": None}
    with _a_save_running(user_id=admin_id) as job_id:
        assert other.get("/api/recordings/job").json() == {"active_job": None}
        shown = admin.get("/api/recordings/job").json()["active_job"]
        assert shown["id"] == job_id and shown["kind"] == recordings.JOB_KIND and shown["user_id"] == admin_id
    with _a_save_running(user_id=other_id) as job_id:
        assert other.get("/api/recordings/job").json()["active_job"]["id"] == job_id
        assert admin.get("/api/recordings/job").json()["active_job"]["id"] == job_id  # an admin sees every save


# -- the length and the holes (M3) --------------------------------------------------------

def test_the_wait_comes_from_the_chunks_held_never_from_a_duration_of_0(rig, monkeypatch):
    seen = _fake_transcode(monkeypatch)
    c = rig["admin"]
    rid = _start(c, timeslice_ms=2000)
    for i in range(3):
        _put(c, rid, i, b"x")
    _go_quiet(monkeypatch)
    r = _finish(c, rid, chunks=3)  # what the Projects page sends: no duration
    assert r.status_code == 200, r.text
    _wait_job(r.json()["job_id"])
    assert seen["timeout"] == recordings.transcode_timeout(6.0) > recordings.TRANSCODE_BASE_SECONDS
    assert "stop_duration=6" in seen["cmd"][seen["cmd"].index("-vf") + 1]
    # It runs in the recording's folder, reading the list of its chunks; nothing assembled beside them.
    assert Path(seen["cwd"]) == rig["tmp"] / "recordings" / rid
    assert not [f for f in seen["files"] if f.endswith(".webm") and not f.startswith("chunk-")]
    assert recordings.read_meta(rid)["status"] == "stopped"
    # An hour of 5-second chunks is an hour; the recorder's clock counts when it is longer (a long pause
    # sends no chunks).
    assert recordings.recorded_seconds({"timeslice_ms": 5000}, 720, 0) == 3600
    assert recordings.transcode_timeout(3600) == 5700
    assert recordings.recorded_seconds({"timeslice_ms": 5000}, 2, 60_000) == 60
    assert recordings.recorded_seconds({}, 2, 0) == 10  # an old record without a timeslice: the default


def test_the_finish_audit_names_the_chunk_count_even_when_none_was_sent(rig, monkeypatch):
    _fake_transcode(monkeypatch)
    c = rig["admin"]
    rid = _start(c, name="Counted")
    for i in range(3):
        _put(c, rid, i, b"x")
    _go_quiet(monkeypatch)
    r = _finish(c, rid)  # no count: every chunk present
    assert r.status_code == 200, r.text
    _wait_job(r.json()["job_id"])
    row = auth_store.list_audit(action="recording.finish")[0]
    assert ", 3 chunks" in row["detail"] and "None" not in row["detail"], row["detail"]


def test_a_recording_with_a_hole_is_saved_as_the_part_before_it_only_when_asked(rig, monkeypatch):
    seen = _fake_transcode(monkeypatch)
    c = rig["admin"]
    rid = _start(c)
    for i in (0, 1, 3):
        _put(c, rid, i, b"x")
    _go_quiet(monkeypatch)
    listed = c.get("/api/recordings").json()["recordings"][0]
    assert (listed["chunks"], listed["contiguous"], listed["highest"]) == (3, 2, 4)
    r = _finish(c, rid)  # every chunk present: the hole is named
    assert r.status_code == 409 and "Chunks 2 of 4 never arrived" in r.json()["detail"], r.text
    r = _finish(c, rid, chunks=2)  # the part before the hole, without consent: refused, saying what is lost
    assert r.status_code == 409 and "give up 1 more" in r.json()["detail"], r.text
    assert jobs.active_of_kind(recordings.JOB_KIND) is None and "listing" not in seen
    r = _finish(c, rid, chunks=2, accept_loss=True)
    assert r.status_code == 200, r.text
    _wait_job(r.json()["job_id"])
    assert seen["listing"] == "chunk-000000.webm\nchunk-000001.webm\n"


# -- the region (MINOR 1) ---------------------------------------------------------------

def test_a_region_not_inside_its_screen_is_refused_at_start(rig):
    c = rig["admin"]
    monitor = {"x": -1600, "y": -120, "width": 1600, "height": 900}
    inside = {"kind": "region", "monitor": monitor, "region": {"x": -1500, "y": -100, "width": 400, "height": 300}}
    _start(c, source=inside)
    past_the_edge = {**inside, "region": {"x": -300, "y": -100, "width": 440, "height": 300}}
    r = c.post("/api/recordings", json={"name": "x", "source": past_the_edge})
    assert r.status_code == 400 and "not inside one screen" in r.json()["detail"], r.text
    wider = {**inside, "region": {"x": -1600, "y": -120, "width": 1700, "height": 300}}
    assert c.post("/api/recordings", json={"name": "x", "source": wider}).status_code == 400
    assert c.post("/api/recordings", json={"name": "x", "source": {"kind": "region"}}).status_code == 400
    assert recordings.region_problem({"kind": "window"}) is None


# -- the transcode's argv ------------------------------------------------------------

def _contains(cmd: list, part: list) -> bool:
    return any(cmd[i:i + len(part)] == part for i in range(len(cmd) - len(part) + 1))


def test_the_transcode_is_the_apps_encode_at_the_recorders_rate(tmp_path):
    from core import video_creator as vc

    cmd = recordings.transcode_command("FF", Path("chunks.txt"), Path("out.mp4"), frame_rate=30, crop=None,
                                       progress_file=tmp_path / "p.txt", pad_seconds=15)
    assert cmd[0] == "FF" and cmd[-1] == "out.mp4"
    assert _contains(cmd, ["-i", "concatf:chunks.txt"])
    assert _contains(cmd, ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30,tpad=stop_mode=clone:stop_duration=15"])
    assert _contains(cmd, ["-fps_mode", "cfr", "-r", "30", "-shortest"])
    assert _contains(cmd, ["-c:v", "libx264", "-preset", vc.REVOICE_X264_PRESET, *vc.h264_params(vc.REVOICE_H264_PROFILE)])
    assert vc.REVOICE_X264_PRESET == "medium" and vc.REVOICE_H264_PROFILE == "high"
    assert _contains(cmd, ["-af", f"{vc.UPMIX_STEREO},aresample={vc.AUDIO_SAMPLE_RATE}"]) and vc.AUDIO_SAMPLE_RATE == 48000
    assert _contains(cmd, ["-c:a", "aac", "-b:a", vc.REVOICE_AUDIO_BITRATE]) and vc.REVOICE_AUDIO_BITRATE == "192k"
    assert _contains(cmd, vc.FASTSTART)
    assert _contains(cmd, ["-progress", str(tmp_path / "p.txt")])
    assert "-ac" not in cmd  # never the power-preserving down-mix


def test_a_region_is_cropped_first_at_its_exact_origin_with_even_sides_and_the_rate_is_clamped(tmp_path):
    cmd = recordings.transcode_command("FF", Path("chunks.txt"), Path("out.mp4"), frame_rate=144,
                                       crop={"x": 101, "y": 57, "width": 401, "height": 301},
                                       progress_file=tmp_path / "p", pad_seconds=0)
    assert _contains(cmd, ["-vf", "crop=400:300:101:57:exact=1,fps=60,tpad=stop_mode=clone:stop_duration=1"])
    assert _contains(cmd, ["-r", "60"])
    assert recordings.even(401) == 400 and recordings.even(1) == 2 and recordings.even(640) == 640


def test_the_crop_is_the_region_less_the_captured_monitors_origin():
    meta = {"source": {"kind": "region", "region": {"x": -1500, "y": -20, "width": 401, "height": 301},
                       "monitor": {"x": -1600, "y": -120, "width": 1600, "height": 900}}}
    assert recordings.region_in_surface(meta) == {"x": 100, "y": 100, "width": 401, "height": 301}
    assert recordings.region_in_surface({"source": {"kind": "screen"}}) is None
    assert recordings.region_in_surface({"source": {"kind": "window", "window_title": "x"}}) is None


# -- the real thing --------------------------------------------------------------------

def _top_level_atoms(path: Path) -> list[str]:
    order, pos = [], 0
    with open(path, "rb") as f:
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            size, typ = struct.unpack(">I4s", hdr)
            order.append(typ.decode("latin1"))
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0]
            if size == 0:
                break
            pos += size
            f.seek(pos)
    return order


def _webm_fixture(ffmpeg: str, tmp_path: Path, seconds: float = 3.0, *, video: str | None = None,
                  audio_seconds: float | None = None, lossless: bool = False, name: str = "fixture.webm") -> Path:
    """A VP9/Opus WebM like MediaRecorder's, from lavfi: by default a 320x240
    test pattern at 30 fps with a 440 Hz tone, mono, both ``seconds`` long."""
    out = tmp_path / name
    video = video or f"testsrc=size=320x240:rate=30:duration={seconds}"
    rate = ["-lossless", "1"] if lossless else ["-b:v", "500k"]
    subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", video,
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_seconds or seconds}",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", *rate,
                    "-c:a", "libopus", "-b:a", "64k", "-map", "0:v:0", "-map", "1:a:0", str(out)],
                   capture_output=True, timeout=120, check=True)
    return out


def _split(data: bytes) -> list[bytes]:
    """Three byte-split chunks, as MediaRecorder's timeslices are."""
    a, b = len(data) * 2 // 5, len(data) * 7 // 10
    return [data[:a], data[a:b], data[b:]]


def _streams(ffmpeg: str, mp4: Path) -> dict:
    out = subprocess.run([_ffprobe_beside(ffmpeg), "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,profile,pix_fmt,width,height,r_frame_rate,sample_rate,channels,duration",
                          "-of", "json", str(mp4)], capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def _saved(c: TestClient, rig: dict, job: dict) -> tuple[dict, Path]:
    assert job["status"] == "done", job
    project = c.get(f"/api/projects/{job['result']['project_id']}").json()
    return project, rig["tmp"] / "projects" / project["id"] / project["source_filename"]


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_recording_becomes_a_video_project_on_the_real_binary(rig, monkeypatch, tmp_path, ffmpeg):
    monkeypatch.setattr(recordings, "FFMPEG_PATH", ffmpeg)
    import core.video_creator as vc

    real_run, at_transcode = vc._run_until_done, {}

    def watched(cmd, log, timeout, cancelled, on_poll=None, cwd=None):
        at_transcode["files"] = sorted(p.name for p in Path(cwd).iterdir())
        return real_run(cmd, log, timeout, cancelled, on_poll, cwd)

    monkeypatch.setattr(vc, "_run_until_done", watched)
    c = rig["admin"]
    parts = _split(_webm_fixture(ffmpeg, tmp_path).read_bytes())
    rid = _start(c, name="Budget 2026 - Excel", frame_rate=30, width=320, height=240,
                 source={"kind": "window", "window_title": "Budget 2026 - Excel"})
    assert _put(c, rid, 2, parts[2]).status_code == 200
    assert _put(c, rid, 0, parts[0]).status_code == 200
    assert _put(c, rid, 1, parts[1]).status_code == 200

    r = _finish(c, rid, duration_ms=3000, chunks=3, from_recorder=True)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    # A second save while this one runs is refused.
    assert _finish(c, rid, duration_ms=3000, chunks=3, from_recorder=True).status_code == 409
    job = _wait_job(job_id)
    assert job["status"] == "done", job
    assert job["result"]["name"] == "Budget 2026 - Excel" and abs(job["result"]["seconds"] - 3.0) < 0.2
    assert job["message"] == "Saved as Budget 2026 - Excel"
    # ffmpeg read the chunks in place: no assembled WebM beside them.
    assert at_transcode["files"] == ["chunk-000000.webm", "chunk-000001.webm", "chunk-000002.webm",
                                     recordings.CHUNK_LIST, "meta.json"]

    project, mp4 = _saved(c, rig, job)
    assert project["kind"] == "video" and project["name"] == "Budget 2026 - Excel"
    assert project["source_filename"] == "Budget-2026---Excel.mp4"
    assert project["owner_name"]
    assert mp4.is_file() and project["size_bytes"] == mp4.stat().st_size
    # The chunks are gone; the recording is no longer listed.
    assert not (rig["tmp"] / "recordings" / rid).exists()
    assert c.get("/api/recordings").json()["recordings"] == []

    streams = _streams(ffmpeg, mp4)
    v, a_ = streams["video"], streams["audio"]
    assert (v["codec_name"], v["profile"], v["pix_fmt"], v["width"], v["height"], v["r_frame_rate"]) == \
        ("h264", "High", "yuv420p", 320, 240, "30/1")
    assert (a_["codec_name"], a_["sample_rate"], a_["channels"]) == ("aac", "48000", 2)
    atoms = _top_level_atoms(mp4)
    assert atoms.index("moov") < atoms.index("mdat"), atoms


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_picture_that_ends_before_its_sound_is_held_to_the_sounds_end(rig, monkeypatch, tmp_path, ffmpeg):
    """A screen still at the end sends no frames: the WebM's picture ends 1 s before its sound. The MP4's
    picture is held to the sound's end (a re-voice muxes with -shortest and would cut there)."""
    monkeypatch.setattr(recordings, "FFMPEG_PATH", ffmpeg)
    c = rig["admin"]
    data = _webm_fixture(ffmpeg, tmp_path, seconds=2.0, audio_seconds=3.0).read_bytes()
    rid = _start(c, name="Still at the end", frame_rate=30, width=320, height=240)
    assert _put(c, rid, 0, data).status_code == 200
    r = _finish(c, rid, duration_ms=3000, chunks=1, from_recorder=True)
    assert r.status_code == 200, r.text
    _, mp4 = _saved(c, rig, _wait_job(r.json()["job_id"]))
    streams = _streams(ffmpeg, mp4)
    video, audio = float(streams["video"]["duration"]), float(streams["audio"]["duration"])
    assert audio > 2.9 and abs(video - audio) < 0.1, (video, audio)


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_an_odd_region_origin_is_cropped_exactly_on_the_real_binary(rig, monkeypatch, tmp_path, ffmpeg):
    """A white square at (101, 57) on black, lossless; the region is that square. Cropped at its exact
    origin the MP4 is white to its first row and column; rounded to the chroma grid (100, 56) - the
    review's measurement - they are black."""
    monkeypatch.setattr(recordings, "FFMPEG_PATH", ffmpeg)
    c = rig["admin"]
    pattern = "color=black:size=320x240:rate=30:duration=1,drawbox=x=101:y=57:w=100:h=100:color=white:t=fill"
    data = _webm_fixture(ffmpeg, tmp_path, seconds=1.0, video=pattern, lossless=True).read_bytes()
    source = {"kind": "region", "region": {"x": 101, "y": 57, "width": 100, "height": 100},
              "monitor": {"x": 0, "y": 0, "width": 320, "height": 240}}
    rid = _start(c, name="Odd origin", frame_rate=30, width=320, height=240, source=source)
    assert _put(c, rid, 0, data).status_code == 200
    r = _finish(c, rid, duration_ms=1000, chunks=1, from_recorder=True)
    assert r.status_code == 200, r.text
    _, mp4 = _saved(c, rig, _wait_job(r.json()["job_id"]))
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(mp4), "-frames:v", "1", "-vf", "format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True, timeout=60).stdout
    assert len(raw) == 100 * 100
    first_row, first_column = raw[:100], raw[0::100]
    assert min(first_row) > 180 and min(first_column) > 180, (min(first_row), min(first_column))


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_the_part_before_a_lost_chunk_is_saved_on_the_real_binary(rig, monkeypatch, tmp_path, ffmpeg):
    """Chunk 2 of 4 never arrived (a crash, a resend that failed): with the loss accepted, chunks 0-1 -
    a WebM cut mid-stream, as a lost chunk leaves it - become a project, and every piece goes."""
    monkeypatch.setattr(recordings, "FFMPEG_PATH", ffmpeg)
    c = rig["admin"]
    parts = _split(_webm_fixture(ffmpeg, tmp_path, seconds=3.0).read_bytes())
    rid = _start(c, name="Interrupted", frame_rate=30, width=320, height=240)
    for seq, part in ((0, parts[0]), (1, parts[1]), (3, parts[2])):
        assert _put(c, rid, seq, part).status_code == 200
    _go_quiet(monkeypatch)
    r = _finish(c, rid, chunks=2, accept_loss=True)
    assert r.status_code == 200, r.text
    _, mp4 = _saved(c, rig, _wait_job(r.json()["job_id"]))
    v = _streams(ffmpeg, mp4)["video"]
    assert (v["codec_name"], v["width"], v["height"]) == ("h264", 320, 240)
    assert 0.5 < float(v["duration"]) < 3.0
    assert not (rig["tmp"] / "recordings" / rid).exists()


@needs_ffmpeg
def test_a_cancelled_save_keeps_the_chunks_for_another_try(rig, monkeypatch, tmp_path):
    """The job's cancel reaches the transcode: ffmpeg is stopped, the MP4 is
    not written, the recording stays listed with its chunks, savable."""
    ffmpeg = REAL_FFMPEGS[0]
    monkeypatch.setattr(recordings, "FFMPEG_PATH", ffmpeg)
    c = rig["admin"]
    data = _webm_fixture(ffmpeg, tmp_path, seconds=6.0).read_bytes()
    rid = _start(c, name="Cancel me", frame_rate=30, width=320, height=240)
    assert _put(c, rid, 0, data).status_code == 200
    # Hold the transcode back so the cancel lands inside it: a poll that
    # cancels on its first call, through the job's own flag.
    import core.video_creator as vc

    real_run = vc._run_until_done

    def _run_then_cancel(cmd, log, timeout, cancelled, on_poll=None, cwd=None):
        job = jobs.active_of_kind(recordings.JOB_KIND)
        jobs.cancel(job["id"])
        return real_run(cmd, log, timeout, cancelled, on_poll, cwd)

    monkeypatch.setattr(vc, "_run_until_done", _run_then_cancel)
    r = _finish(c, rid, duration_ms=6000, chunks=1, from_recorder=True)
    assert r.status_code == 200, r.text
    job = _wait_job(r.json()["job_id"])
    assert job["status"] == "done" and job["result"] == {"cancelled": True}, job
    assert "kept" in job["message"]
    assert (rig["tmp"] / "recordings" / rid / "chunk-000000.webm").is_file()
    assert not list((rig["tmp"] / "recordings" / rid).glob("*.mp4"))
    assert [x["id"] for x in c.get("/api/recordings").json()["recordings"]] == [rid]
    assert recordings.read_meta(rid)["status"] == "stopped"
