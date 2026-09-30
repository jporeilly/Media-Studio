"""Markers become chapters in the render (porting vertical 6, phase E5b:
spec §13.2, decision 3, trap 42).

The chapter list itself is ``services.edit.chapters_for``'s and is pinned in
``tests/test_edit.py``. What these tests hold in place is the WRITE and its
place in the job:

- ``core.video_creator._ffmeta_escape`` escapes the five characters the
  ffmetadata format names - ``=``, ``;``, ``#``, the backslash and a
  newline - and nothing else, the backslash first;
- ``embed_chapters``, lifted out of the deck's ``_embed_chapters``, writes
  the metadata file, remuxes with the resolved ffmpeg (``-map_metadata 1
  -codec copy``, trap 3) into ``<stem>_chaptered.mp4``, renames it over the
  original and cleans up; with nothing to write, no ffmpeg or a failed remux
  it answers False, logs, and leaves the file exactly as it was. The deck
  path hands it the same spans it always computed
  (``tests/test_generation_options.py`` keeps the deck's own case);
- in the re-voice job it runs LAST - after the mux, and after the music
  pass when there is one - on the FINAL file the record names, with the
  chapters computed then from the record being rendered; no drawn markers,
  no remux; a failed remux never fails the job; a remux that lands stamps
  the record again (the bytes changed, so the page's cache token must);
- on the REAL binaries - the machine's ffmpeg AND the bundled 7.1 where it
  is installed, as F1's mux test runs - ``ffprobe -show_chapters`` lists the
  titles and the times exactly, a marker in removed picture is absent, and
  a name carrying a backslash comes back as typed (the one character an
  unescaped title loses on both builds; measured 2026-09-24).

ffmpeg is faked for the unit tests; the integration tests run the real
binaries when there are any and skip otherwise.
"""

import json
import logging
import subprocess
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import utils.config as config_module
from api import store as auth_store
from core import video_creator, video_importer
from services import edit, music, processing, revoice
from services import projects as store
from utils.config import config

# The real binaries the F1 checks run on, and the engine stand-in the job
# tests use: shared rather than copied.
from test_generation_options import REAL_FFMPEGS, _real_media, needs_ffmpeg
from test_revoice import _FakeVideoProcessor

KEEP = [[0.0, 6.0], [7.5, 12.0]]  # 6.0-7.5 removed: the output is 10.5 s
MARK_A = {"id": "a", "at": 1.0, "name": "Intro"}
MARK_B = {"id": "b", "at": 6.5, "name": "In the hole"}
MARK_C = {"id": "c", "at": 9.0, "name": r"Wrap\up = done; #3"}


# ── the escape ────────────────────────────────────────────────────────────────

def test_ffmeta_escape_escapes_the_five_characters_the_format_names_and_nothing_else():
    escape = video_creator._ffmeta_escape
    assert escape("plain title") == "plain title"
    assert escape("a=b") == "a\\=b"
    assert escape("a;b") == "a\\;b"
    assert escape("#1") == "\\#1"
    assert escape("C:\\Intro") == "C:\\\\Intro"
    assert escape("two\nlines") == "two\\\nlines"
    assert escape(MARK_C["name"]) == "Wrap\\\\up \\= done\\; \\#3"
    # The backslash first, so an escape is never escaped again.
    assert escape("\\=") == "\\\\\\="
    # Everything else rides through: quotes, commas, colons, unicode, a CR.
    untouched = "Q&A: 100% (done), 'quoted' \"too\" – très bien\r"
    assert escape(untouched) == untouched
    assert escape("") == ""


# ── embed_chapters, faked ─────────────────────────────────────────────────────

def _fake_ffmpeg(monkeypatch, *, ok=True, raises=False):
    """A ``subprocess.run`` that records the command and the metadata file
    (read before it is cleaned up) and writes the temp output on success."""
    calls: list[dict] = []

    def fake_run(cmd, **kwargs):
        meta = Path(cmd[4])
        calls.append({"cmd": list(cmd), "meta": meta.read_text(encoding="utf-8"), "kwargs": kwargs})
        if raises:
            raise OSError("no can do")
        if ok:
            Path(cmd[-1]).write_bytes(b"CHAPTERED")
        return types.SimpleNamespace(returncode=0 if ok else 1, stderr=b"not really ffmpeg")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "ffmpeg-test")
    return calls


def test_embed_chapters_writes_the_metadata_file_remuxes_in_place_and_cleans_up(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch)
    video = tmp_path / "clip_revoiced.mp4"
    video.write_bytes(b"MUXED")

    assert video_creator.embed_chapters(video, [(1000, 7500, "Intro"), (7500, 10500, MARK_C["name"])]) is True

    (call,) = calls
    temp = tmp_path / "clip_revoiced_chaptered.mp4"
    assert call["cmd"] == [
        "ffmpeg-test", "-i", str(video), "-i", str(tmp_path / "clip_revoiced.chapters.txt"),
        "-map_metadata", "1", "-map_chapters", "1", "-codec", "copy", "-y", str(temp),
    ], "the resolved ffmpeg, the deck path's own remux, the chapters mapped explicitly"
    assert call["kwargs"]["timeout"] == 60 and call["kwargs"]["capture_output"] is True
    assert call["meta"] == (
        ";FFMETADATA1\n"
        "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=7500\ntitle=Intro\n"
        "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=7500\nEND=10500\ntitle=Wrap\\\\up \\= done\\; \\#3\n"
    ), "the titles escaped as the format wants"
    assert video.read_bytes() == b"CHAPTERED", "renamed over the original"
    assert not temp.exists() and not (tmp_path / "clip_revoiced.chapters.txt").exists()


def _meta_for(record: dict, tmp_path, monkeypatch) -> list[str]:
    """The ffmetadata text the markers of ``record`` become: ``chapters_for``
    then ``embed_chapters`` with ffmpeg faked - [] when nothing was spawned."""
    calls = _fake_ffmpeg(monkeypatch)
    video = tmp_path / "clip_revoiced.mp4"
    video.write_bytes(b"MUXED")
    video_creator.embed_chapters(video, edit.chapters_for(record, 12.0))
    return [call["meta"] for call in calls]


def _block(start: int, end: int, title: str) -> str:
    """One chapter's block in the metadata file, its title already escaped."""
    return f"\n[CHAPTER]\nTIMEBASE=1/1000\nSTART={start}\nEND={end}\ntitle={title}\n"


def test_the_metadata_opens_with_an_untitled_chapter_when_the_first_marker_is_after_0(tmp_path, monkeypatch):
    """E6, the three cases as the ffmetadata file carries them: a first
    marker after 0 - an untitled chapter from 0 to it comes first, as a
    ``title=`` line with nothing after it (a block with NO title line
    breaks ffmpeg's reader; the real-binary test below holds that); a first
    marker at 0 - no leading chapter; no markers - no file, no ffmpeg."""
    after_0 = {"edit": {"version": 2, "markers": [MARK_C, MARK_A]}}
    wrap = "Wrap\\\\up \\= done\\; \\#3"  # MARK_C's name as the format escapes it
    assert _meta_for(after_0, tmp_path, monkeypatch) == [
        ";FFMETADATA1\n" + _block(0, 1000, "") + _block(1000, 9000, "Intro") + _block(9000, 12000, wrap)
    ]
    at_0 = {"edit": {"version": 2, "markers": [{**MARK_A, "at": 0.0}, MARK_C]}}
    assert _meta_for(at_0, tmp_path, monkeypatch) == [
        ";FFMETADATA1\n" + _block(0, 9000, "Intro") + _block(9000, 12000, wrap)
    ]
    assert _meta_for({"edit": {"version": 2, "markers": []}}, tmp_path, monkeypatch) == []
    assert _meta_for({}, tmp_path, monkeypatch) == []


def test_embed_chapters_with_nothing_to_write_answers_false_and_spawns_nothing(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch)
    video = tmp_path / "clip_revoiced.mp4"
    video.write_bytes(b"MUXED")
    assert video_creator.embed_chapters(video, []) is False
    assert calls == [] and video.read_bytes() == b"MUXED"


def test_embed_chapters_without_an_ffmpeg_answers_false_and_leaves_the_file(tmp_path, monkeypatch):
    calls = _fake_ffmpeg(monkeypatch)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", "")
    video = tmp_path / "clip_revoiced.mp4"
    video.write_bytes(b"MUXED")
    assert video_creator.embed_chapters(video, [(0, 1000, "x")]) is False
    assert calls == [] and video.read_bytes() == b"MUXED"
    assert not (tmp_path / "clip_revoiced.chapters.txt").exists()


def test_a_failed_remux_answers_false_leaves_the_file_and_leaves_nothing_behind(tmp_path, monkeypatch):
    _fake_ffmpeg(monkeypatch, ok=False)
    video = tmp_path / "clip_revoiced.mp4"
    video.write_bytes(b"MUXED")
    assert video_creator.embed_chapters(video, [(0, 1000, "x")]) is False
    assert video.read_bytes() == b"MUXED"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip_revoiced.mp4"], "no metadata file, no partial output"

    _fake_ffmpeg(monkeypatch, raises=True)
    assert video_creator.embed_chapters(video, [(0, 1000, "x")]) is False, "never raises"
    assert video.read_bytes() == b"MUXED"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip_revoiced.mp4"]


def test_the_deck_path_hands_its_spans_to_the_shared_remux(tmp_path, monkeypatch):
    """The deck's behaviour is unchanged: it computes its spans - shifted
    past the intro card, the pause after each slide - and calls the lifted
    function with them (``tests/test_generation_options.py`` keeps the
    metadata-file case)."""
    from core.video_creator import SlideClipInfo, VideoCreator

    handed: list[tuple] = []
    monkeypatch.setattr(video_creator, "embed_chapters", lambda path, chapters: handed.append((path, chapters)) or True)
    video = tmp_path / "deck.mp4"
    video.write_bytes(b"mp4")
    creator = VideoCreator(intro_text="Welcome", intro_duration=3.0, transition_pause=0.5, voice_start_delay=0.25)
    clips = [SlideClipInfo(slide_index=0, duration=2.0), SlideClipInfo(slide_index=1, duration=1.5)]
    creator._embed_chapters(video, clips, ["Opening", ""])
    assert handed == [(video, [(3000, 5000, "Opening"), (5500, 7000, "Slide 2")])]


# ── the job: last, on the final file ──────────────────────────────────────────

RATE = 1000
SIGNAL = np.zeros(2500, dtype=np.int16)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A logged-in TestClient with the store, the auth DB, the config, the
    scratch and the music library isolated, and the engine faked."""
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path / "music")
    monkeypatch.setattr(music, "decode_audio", lambda path, display_name=None: music.Decoded(30.0, RATE, 2, SIGNAL))
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.captured = None
    _FakeVideoProcessor.source = None

    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _video(seconds: float = 12.0) -> str:
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [
        {"start": 0.0, "end": 2.0, "text": "Hello there."},
        {"start": 8.0, "end": 10.0, "text": "This is a test."},
    ])
    with wave.open(str(store.PROJECTS_DIR / pid / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(1000)
        wf.writeframes(np.zeros(int(round(seconds * 1000)), dtype="<i2").tobytes())
    return pid


def _recorders(monkeypatch, *, embed_ok=True):
    """Stand-ins for the cut, the mix and the chapter remux that share one
    log, so a test can see the ORDER as well as the arguments - and, for
    the remux, the bytes and the record as they stood when it ran."""
    log: list[dict] = []

    def fake_cut(source, keep, dst, video_bitrate="", cancel_check=None):
        log.append({"step": "cut", "keep": keep})
        Path(dst).write_bytes(b"CUT-PICTURE")
        return True

    def fake_mix(video_in, clips, video_out, cancel_check=None, output_seconds=None):
        log.append({"step": "mix", "video_out": Path(video_out)})
        Path(video_out).write_bytes(b"FAKEREVOICE+MUSIC")
        return True

    def fake_embed(video_path, chapters):
        log.append({"step": "chapters", "video": Path(video_path), "chapters": list(chapters),
                    "input_bytes": Path(video_path).read_bytes(),
                    "record": store.get_project(Path(video_path).parent.name)})
        if embed_ok:
            Path(video_path).write_bytes(Path(video_path).read_bytes() + b"+CHAPTERS")
        return embed_ok

    monkeypatch.setattr(video_creator, "cut_picture", fake_cut)
    monkeypatch.setattr(video_creator, "mix_music", fake_mix)
    monkeypatch.setattr(video_creator, "embed_chapters", fake_embed)
    return log


def _wait_job(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish in time")


def _revoice(client, pid):
    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "v"})
    assert r.status_code == 200, r.text
    return _wait_job(client, r.json()["job_id"])


def test_the_chapters_are_written_last_on_the_final_file_from_the_markers_projected_through_the_cut(client, monkeypatch):
    """A cut and markers: the picture is cut, the engine muxes, and THEN the
    drawn markers - projected through the list as stored - go onto the muxed
    file the record names, the record stamped again for the bytes."""
    log = _recorders(monkeypatch)
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"video": KEEP, "markers": [MARK_C, MARK_A, MARK_B]}).status_code == 200
    progress: list[tuple[float, str]] = []

    result = revoice.revoice_project(pid, "v", progress=lambda f, m="": progress.append((f, m)))
    assert result == {"video": "clip_revoiced.mp4", "language": None, "failed_sentences": 0}

    assert [entry["step"] for entry in log] == ["cut", "chapters"]
    out = store.PROJECTS_DIR / pid / "clip_revoiced.mp4"
    chapters = log[1]
    assert chapters["video"] == out, "the file the record names, never the cut intermediate"
    assert chapters["input_bytes"] == b"FAKEREVOICE", "after the mux"
    assert chapters["chapters"] == [(0, 1000, ""), (1000, 7500, "Intro"), (7500, 10500, MARK_C["name"])], (
        "the hidden marker is no chapter; unescaped names - the writer escapes; the first marker is not at 0, so an "
        "untitled chapter opens the file (E6)"
    )
    assert chapters["chapters"] == edit.chapters_for(store.get_project(pid), 12.0), "the one place the list is computed"
    assert progress.index((0.9, "revoicing")) < progress.index((0.98, "Writing 3 chapters…")), "the chapters WRITTEN, the untitled one included"
    assert [f for f, _ in progress] == sorted(f for f, _ in progress), "the job never steps backwards"

    during = chapters["record"]
    assert during["revoiced_video"] == "clip_revoiced.mp4", "stamped for the muxed file before the remux ran"
    saved = store.get_project(pid)
    assert out.read_bytes() == b"FAKEREVOICE+CHAPTERS"
    assert saved["revoiced_at"] > during["revoiced_at"], "the bytes changed again, so the cache token moves"
    assert saved["edit_rendered_at"] == saved["revoiced_at"]
    assert client.get(f"/api/projects/{pid}/revoiced-video").content == b"FAKEREVOICE+CHAPTERS"


def test_with_music_the_chapters_go_on_after_the_mix_onto_the_mixed_file(client, monkeypatch):
    music.add_file("bed.mp3", b"BED", "u")
    log = _recorders(monkeypatch)
    pid = _video()
    bed = {"id": "bed", "file": "bed.mp3", "at": 0.0, "in": 0.0, "out": 3.0, "gain": 0.15, "fade_in": 0.5, "fade_out": 0.5}
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [bed], "markers": [MARK_A]}).status_code == 200

    assert _revoice(client, pid)["status"] == "done"
    assert [entry["step"] for entry in log] == ["mix", "chapters"]
    out = store.PROJECTS_DIR / pid / "clip_revoiced.mp4"
    assert log[1]["video"] == out == log[0]["video_out"]
    assert log[1]["input_bytes"] == b"FAKEREVOICE+MUSIC", "the mixed file is what gets the chapters"
    assert log[1]["chapters"] == [(0, 1000, ""), (1000, 12000, "Intro")], "a whole picture: the last chapter runs to the source's end"
    saved = store.get_project(pid)
    assert out.read_bytes() == b"FAKEREVOICE+MUSIC+CHAPTERS"
    assert saved["music_rendered"] == 1 and saved["edit_rendered_at"] == saved["revoiced_at"]


def test_no_drawn_markers_no_remux(client, monkeypatch):
    """The path every project has always taken: no markers, no remux - and a
    marker the cut hides is no chapter, so a project whose only marker sits
    in removed picture takes that path too."""
    log = _recorders(monkeypatch)
    pid = _video()
    assert _revoice(client, pid)["status"] == "done"
    assert log == []
    assert (store.PROJECTS_DIR / pid / "clip_revoiced.mp4").read_bytes() == b"FAKEREVOICE"
    assert "edit_rendered_at" not in store.get_project(pid)

    assert client.put(f"/api/projects/{pid}/edit", json={"video": KEEP, "markers": [MARK_B]}).status_code == 200
    assert _revoice(client, pid)["status"] == "done"
    assert [entry["step"] for entry in log] == ["cut"], "the picture was cut; nothing was chaptered"
    assert (store.PROJECTS_DIR / pid / "clip_revoiced.mp4").read_bytes() == b"FAKEREVOICE"


def test_a_marker_only_edit_renders_the_untouched_source_and_stamps_it_edited_once_the_chapters_are_in(client, monkeypatch):
    log = _recorders(monkeypatch)
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"markers": [MARK_A, MARK_C]}).status_code == 200
    assert _revoice(client, pid)["status"] == "done"
    assert [entry["step"] for entry in log] == ["chapters"], "no cut, no mix"
    assert _FakeVideoProcessor.source == store.PROJECTS_DIR / pid / "clip.mp4", "the untouched source"
    assert log[0]["chapters"] == [(0, 1000, ""), (1000, 9000, "Intro"), (9000, 12000, MARK_C["name"])]
    assert "edit_rendered_at" not in log[0]["record"], "nothing cut or projected: unstamped until the chapters land"
    saved = store.get_project(pid)
    assert saved["edit_rendered_at"] == saved["revoiced_at"], "the output differs from an unedited render"


def test_markers_whose_wav_has_gone_render_no_chapters_and_say_so(client, monkeypatch, caplog):
    """The Reviewer's NIT 7: reachable only when ``audio.wav`` is removed
    after the markers were stored (the PUT that stores them needs it). With
    no length the last chapter has no end, so the job - no cut, no mix -
    writes no chapters and says so once, naming the project and the count."""
    log = _recorders(monkeypatch)
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"markers": [MARK_A, MARK_C]}).status_code == 200
    (store.PROJECTS_DIR / pid / "audio.wav").unlink()

    with caplog.at_level(logging.WARNING):
        result = revoice.revoice_project(pid, "v")
    assert result["video"] == "clip_revoiced.mp4"
    assert log == [], "nothing could be measured, so no remux"
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING and "marker" in r.getMessage()]
    assert said == [f"Project {pid} has 2 markers but its extracted audio is missing, so none became chapters"]
    assert "edit_rendered_at" not in store.get_project(pid), "nothing rendered differs from an unedited render"

    # With the audio back, the same markers are chapters again and nothing is said.
    caplog.clear()
    with wave.open(str(store.PROJECTS_DIR / pid / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(1000)
        wf.writeframes(np.zeros(12000, dtype="<i2").tobytes())
    with caplog.at_level(logging.WARNING):
        assert revoice.revoice_project(pid, "v")["video"] == "clip_revoiced.mp4"
    assert [entry["step"] for entry in log] == ["chapters"]
    assert not [r for r in caplog.records if r.levelno == logging.WARNING and "marker" in r.getMessage()]


def test_a_failed_remux_never_fails_the_job_and_leaves_the_muxed_file_and_its_stamps(client, monkeypatch):
    log = _recorders(monkeypatch, embed_ok=False)
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"video": KEEP, "markers": [MARK_A]}).status_code == 200

    job = _revoice(client, pid)
    assert job["status"] == "done", job
    assert [entry["step"] for entry in log] == ["cut", "chapters"]
    out = store.PROJECTS_DIR / pid / "clip_revoiced.mp4"
    assert out.read_bytes() == b"FAKEREVOICE", "un-chaptered, as the deck path leaves a render whose chapters failed"
    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["revoiced_at"] == log[1]["record"]["revoiced_at"], "no new bytes, no new stamp"
    assert saved["edit_rendered_at"] == saved["revoiced_at"], "the picture WAS cut, and the file says so"


# ── the real binaries ─────────────────────────────────────────────────────────

def _ffprobe_beside(ffmpeg: str) -> Path | None:
    """The prober that ships beside this ffmpeg (``ffprobe.exe`` next to
    ``ffmpeg.exe``, whatever the case of the suffix), or None."""
    binary = Path(ffmpeg)
    for name in ("ffprobe.exe", "ffprobe.EXE", "ffprobe"):
        candidate = binary.with_name(name)
        if candidate.is_file():
            return candidate
    return None


def _probed_chapters(ffprobe: Path, video: Path) -> list[tuple[float, float, str]]:
    """``ffprobe -show_chapters``: what any player built on ffmpeg's reader sees."""
    out = subprocess.run(
        [str(ffprobe), "-v", "error", "-show_chapters", "-print_format", "json", str(video)],
        capture_output=True, timeout=60, check=True,
    ).stdout.decode("utf-8", errors="replace")
    return [
        (float(c["start_time"]), float(c["end_time"]), c.get("tags", {}).get("title", ""))
        for c in json.loads(out).get("chapters", [])
    ]


def _chpl_atom(video: Path) -> list[tuple[float, str]]:
    """The MP4's Nero chapter list (``chpl``: a version byte, three flag
    bytes, four reserved, a count, then per chapter an 8-byte start in
    100 ns units, a length byte and the UTF-8 title), parsed by hand - the
    FILE's own record of where each chapter starts, which VLC's own
    demuxer reads first. ffmpeg's reader takes the chapter TRACK instead
    and reports the first chapter from 0 whatever its start (measured on
    8.0.1 and the bundled 7.1, MP4 and MOV alike, edit lists on or off;
    Matroska keeps the start) - so the times are proved on the atom and
    the titles and the ends on both."""
    raw = video.read_bytes()
    at = raw.find(b"chpl")
    assert at > 0, "no chpl atom in the MP4"
    body = raw[at + 4:]
    version = body[0]
    count = body[8] if version == 1 else body[4]
    pos = 9 if version == 1 else 5
    found = []
    for _ in range(count):
        start = int.from_bytes(body[pos:pos + 8], "big") / 10_000_000
        length = body[pos + 8]
        found.append((start, body[pos + 9:pos + 9 + length].decode("utf-8")))
        pos += 9 + length
    return found


@needs_ffmpeg
@pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")
def test_a_real_remux_lists_the_titles_and_times_and_leaves_a_hidden_marker_out(tmp_path, monkeypatch, ffmpeg):
    """End to end on the binary itself: a 10.5 s picture standing in for the
    cut output, the three markers projected through KEEP, one remux - and
    the file's own chapter list holds exactly three chapters at their
    starts, the untitled one from 0 (E6) and the two named ones with the
    titles as typed, the backslash included; the prober beside that ffmpeg
    (and ffmpeg's own header, as F1 reads it) lists the same three with the
    same starts, titles and ends. Before E6 the file had no chapter before
    the first marker, and ffmpeg's reader put that first named chapter at
    0 whatever its start (the deck's chapters after an intro card still read
    so) - the disagreement the leading chapter ends. The metadata file and
    the partial output are gone, and the picture still decodes."""
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    video, _ = _real_media(ffmpeg, tmp_path, seconds=10.5, audio_seconds=1.0)
    record = {"edit": {"version": 2, "video": {"keep": KEEP}, "markers": [MARK_C, MARK_A, MARK_B]}}
    chapters = edit.chapters_for(record, 12.0)
    assert chapters == [(0, 1000, ""), (1000, 7500, "Intro"), (7500, 10500, MARK_C["name"])]

    assert video_creator.embed_chapters(video, chapters) is True
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.mp4", "narration.mp3"], "nothing left behind"

    # The file's own chapter list: the starts and the titles exactly as typed.
    assert _chpl_atom(video) == [(0.0, ""), (1.0, "Intro"), (7.5, MARK_C["name"])]
    # ffmpeg's reader AGREES with the atom now: the first named chapter at
    # its true start, the untitled one before it, nothing listed twice. (A
    # block written with no title line at all read back, on 8.0.1 and 7.1
    # alike, as "Intro" from 0 and the last chapter twice - measured
    # 2026-09-29, which is why the leading title is an empty `title=`.)
    expected = [(0.0, 1.0, ""), (1.0, 7.5, "Intro"), (7.5, 10.5, MARK_C["name"])]
    ffprobe = _ffprobe_beside(ffmpeg)
    if ffprobe is not None:
        assert _probed_chapters(ffprobe, video) == expected
    header = [(c["start"], c["end"], c["title"]) for c in video_importer.get_video_chapters(video)]
    # F1's header parser numbers an untitled chapter, as the ffprobe version did.
    assert header == [(0.0, 1.0, "Chapter 1"), *expected[1:]], "ffmpeg's own header agrees with its prober"
    assert not any("In the hole" in title for _, _, title in header)
    # The picture is still there and decodes.
    frame = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=60)
    assert frame.returncode == 0 and len(frame.stdout) == 320 * 240 * 3

    # Chaptered again with a different list: the remux REPLACES, never adds
    # and never keeps. A file that already has chapters is exactly what a
    # re-voice hands this (the mux copies the source's along with the
    # picture), and without ``-map_chapters 1`` ffmpeg kept input 0's list
    # and ignored the markers' - on both binaries, the first run of this
    # very assertion (2026-09-24).
    assert video_creator.embed_chapters(video, [(2000, 3000, "Only")]) is True
    assert _chpl_atom(video) == [(2.0, "Only")]
    # ffmpeg's reader again: one chapter, its title; its times it reads off
    # the chapter track (a lone chapter's end comes back as that track's
    # media length), so the file's atom above is the proof of the times.
    assert [c["title"] for c in video_importer.get_video_chapters(video)] == ["Only"]
    if ffprobe is not None:
        assert [title for _, _, title in _probed_chapters(ffprobe, video)] == ["Only"]
