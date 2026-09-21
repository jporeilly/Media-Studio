"""The music lane's render (porting vertical 6, phase E4a): the second ffmpeg
pass in ``core.video_creator`` (``music_filtergraph``, ``music_timeout``,
``mix_music``) and its place in the re-voice job (``services.revoice``).

What these tests hold in place:

- the graph is the one the spec measured (§12.4): every clip sliced,
  re-timed, made stereo, levelled by a LINEAR gain, faded with linear
  ramps (a fade of 0 left out) and placed by ``adelay`` in whole
  milliseconds; the clips summed with ``normalize=0`` and the voice mixed
  under ``duration=first`` with ``normalize=0`` again (traps 26-28); a file
  used by several clips is one input;
- the up-mix to stereo is UNITY - ``pan``, never
  ``aformat=channel_layouts=stereo``, whose power-preserving rematrix took
  a mono narration down 3.01 dB - for the voice and for every clip, pinned
  in the graph and measured on the real binary;
- the pass copies the video (trap 29), runs the resolved ffmpeg (trap 3),
  is polled for a cancel and a deadline exactly as the picture cut is, and
  a cancelled, failed or stalled mix leaves nothing behind;
- in the job it runs AFTER the mux, in place, only when there are clips; a
  clip whose file has gone is refused BEFORE any work (trap 25); the
  record is stamped for the voice-only file BEFORE the pass and gains
  ``music_rendered`` and a fresh stamp after it, so a cancel or a failure
  leaves a record that describes the file on disk; the standalone narration
  track is never opened.

ffmpeg is faked for the unit tests; the integration tests run the real
binary when there is one (its ``-filters``, one real mix, and the voice's
level through the up-mix from a mono and a stereo source).
"""

import subprocess
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

import utils.config as config_module
from api import store as auth_store
from core import video_creator
from services import edit, jobs, music, processing, revoice
from services import projects as store
from utils.config import config

# The polled-ffmpeg stand-in the picture cut's tests use, and the engine
# stand-in the re-voice tests use: shared rather than copied.
from test_generation_options import _FakeFfmpeg
from test_revoice import _FakeVideoProcessor

# The spec's measured case (§12.4, measured 2026-09-21): the 336 s corpus
# output under two five-minute MP3s - clips 0-290 s at 12.5 s and 30-300 s
# at 200 s, gain 0.15, fades 1 and 2 s.
A = "C:/lib/Amarent - The Man from Hyde Park.mp3"
B = "C:/lib/Second Bed.mp3"
BED = {"path": A, "at": 12.5, "in": 0.0, "out": 290.0, "gain": 0.15, "fade_in": 1.0, "fade_out": 2.0}
TAIL = {"path": B, "at": 200.0, "in": 30.0, "out": 300.0, "gain": 0.15, "fade_in": 1.0, "fade_out": 2.0}


# ── the graph ─────────────────────────────────────────────────────────────────

def test_one_clip_is_the_bed_itself_and_the_voice_is_mixed_under_duration_first():
    assert video_creator.music_filtergraph([BED], [A]) == (
        "[1:a]atrim=start=0.000:end=290.000,asetpts=PTS-STARTPTS,pan=stereo|FL=FL+FC|FR=FR+FC,"
        "volume=0.150,afade=t=in:st=0.000:d=1.000,afade=t=out:st=288.000:d=2.000,adelay=12500|12500[m1];"
        "[0:a]pan=stereo|FL=FL+FC|FR=FR+FC[v];"
        "[v][m1]amix=inputs=2:duration=first:normalize=0[a]"
    )


def test_the_measured_graph_two_clips_over_two_files():
    """The spec's own case, as measured on the bundled 7.1: 8.25 s over a
    336 s output with the video copied, its length preserved."""
    assert video_creator.music_filtergraph([BED, TAIL], [A, B]) == (
        "[1:a]atrim=start=0.000:end=290.000,asetpts=PTS-STARTPTS,pan=stereo|FL=FL+FC|FR=FR+FC,"
        "volume=0.150,afade=t=in:st=0.000:d=1.000,afade=t=out:st=288.000:d=2.000,adelay=12500|12500[m1];"
        "[2:a]atrim=start=30.000:end=300.000,asetpts=PTS-STARTPTS,pan=stereo|FL=FL+FC|FR=FR+FC,"
        "volume=0.150,afade=t=in:st=0.000:d=1.000,afade=t=out:st=268.000:d=2.000,adelay=200000|200000[m2];"
        "[m1][m2]amix=inputs=2:normalize=0:dropout_transition=0[bed];"
        "[0:a]pan=stereo|FL=FL+FC|FR=FR+FC[v];"
        "[v][bed]amix=inputs=2:duration=first:normalize=0[a]"
    )


def test_three_clips_over_two_files_reuse_the_input_and_omit_zero_fades():
    """A file laid twice (the owner's own Track 3) is decoded from ONE input;
    ``adelay`` is whole milliseconds; times are 3 dp; a fade of 0 is left
    out; the gain is linear and printed to 3 dp."""
    first = {"path": A, "at": 0.0004, "in": 0.1, "out": 10.0, "gain": 1, "fade_in": 0, "fade_out": 0}
    second = {"path": B, "at": 1.2346, "in": 5.0, "out": 15.5, "gain": 0.5, "fade_in": 0.0, "fade_out": 0.25}
    again = {"path": A, "at": 100.0, "in": 0.0, "out": 4.5, "gain": 0.15, "fade_in": 1.0, "fade_out": 0}
    graph = video_creator.music_filtergraph([first, second, again], [A, B])
    assert graph == (
        "[1:a]atrim=start=0.100:end=10.000,asetpts=PTS-STARTPTS,pan=stereo|FL=FL+FC|FR=FR+FC,volume=1.000,adelay=0|0[m1];"
        "[2:a]atrim=start=5.000:end=15.500,asetpts=PTS-STARTPTS,pan=stereo|FL=FL+FC|FR=FR+FC,volume=0.500,"
        "afade=t=out:st=10.250:d=0.250,adelay=1235|1235[m2];"
        "[1:a]atrim=start=0.000:end=4.500,asetpts=PTS-STARTPTS,pan=stereo|FL=FL+FC|FR=FR+FC,volume=0.150,"
        "afade=t=in:st=0.000:d=1.000,adelay=100000|100000[m3];"
        "[m1][m2][m3]amix=inputs=3:normalize=0:dropout_transition=0[bed];"
        "[0:a]pan=stereo|FL=FL+FC|FR=FR+FC[v];"
        "[v][bed]amix=inputs=2:duration=first:normalize=0[a]"
    )
    assert graph.count("normalize=0") == 2, "both mixes: amix otherwise divides by the input count"
    assert graph.count("duration=first") == 1 and graph.count("dropout_transition=0") == 1
    assert "[3:a]" not in graph, "two files, two inputs"
    assert "dB" not in graph and "volume=0.150" in graph, "a linear factor, never the old rough-dB formula"


def test_the_up_mix_is_unity_for_the_voice_and_for_every_clip():
    """``aformat=channel_layouts=stereo`` rematrixes mono to M/sqrt(2) - the
    voice arrived 3.01 dB down - so the graph names neither it nor any other
    filter that changes a level it was not asked to change. ``pan`` copies
    the channels it names and drops the ones the input has not got, so one
    string serves a mono and a stereo input with nothing probed."""
    graph = video_creator.music_filtergraph([BED, TAIL], [A, B])
    assert "aformat" not in graph, "the -3.01 dB up-mix is gone from the graph entirely"
    assert graph.count(video_creator.UPMIX_STEREO) == 3, "both clips and the voice"
    assert video_creator.UPMIX_STEREO == "pan=stereo|FL=FL+FC|FR=FR+FC"
    # The order inside a clip's chain: the slice, re-timed, up-mixed, then
    # levelled, faded and placed - the level and the fades act on stereo.
    for chain in graph.split(";")[:2]:
        steps = [step.split("=")[0].split("]")[-1] for step in chain.split(",")]
        assert steps == ["atrim", "asetpts", "pan", "volume", "afade", "afade", "adelay"], chain
    assert graph.split(";")[-2] == f"[0:a]{video_creator.UPMIX_STEREO}[v]", "the voice, up-mixed and nothing else"


def test_a_graph_for_no_clips_is_refused_rather_than_written():
    """There is no graph for no music: one written anyway names an ``[m1]``
    that does not exist. ``mix_music`` refuses an empty list before it gets
    here; the pure function is public and says so itself."""
    with pytest.raises(ValueError) as exc:
        video_creator.music_filtergraph([], [])
    assert "at least one clip" in str(exc.value)
    with pytest.raises(ValueError):
        video_creator.music_filtergraph((), [A])


def test_the_timeout_is_sixty_seconds_plus_twice_the_output():
    """Sized by the OUTPUT's length, never by how far the clips reach: an
    hour of video under one 15 s sting gets 7260 s, not 90."""
    assert video_creator.music_timeout(336.0) == pytest.approx(732.0)
    assert video_creator.music_timeout(0) == pytest.approx(60.0)
    assert video_creator.music_timeout(3600) == pytest.approx(7260.0)
    assert video_creator.music_timeout(15) == pytest.approx(90.0)
    assert video_creator.music_timeout(7200) == pytest.approx(14460.0)


# ── the pass, against a fake ffmpeg ───────────────────────────────────────────

def _fake_mix(monkeypatch, path="ffmpeg-test", **script):
    """Install the polled fake ffmpeg; returns the processes started."""
    _FakeFfmpeg.script = script
    _FakeFfmpeg.instances = []
    monkeypatch.setattr(subprocess, "Popen", _FakeFfmpeg)
    monkeypatch.setattr(config_module, "FFMPEG_PATH", path)
    monkeypatch.setattr(video_creator, "CUT_POLL_SECONDS", 0)
    return _FakeFfmpeg.instances


def _files(tmp_path) -> tuple[Path, list[dict]]:
    """A muxed output and two clips over two real files beside it."""
    out = tmp_path / "clip_revoiced.mp4"
    out.write_bytes(b"REVOICED")
    bed = tmp_path / "lib" / "bed.mp3"
    sting = tmp_path / "lib" / "sting.wav"
    bed.parent.mkdir()
    bed.write_bytes(b"mp3")
    sting.write_bytes(b"wav")
    clips = [
        {"path": str(bed), "at": 0.0, "in": 0.0, "out": 3.0, "gain": 0.15, "fade_in": 0.5, "fade_out": 0.5},
        {"path": str(sting), "at": 1.0, "in": 0.0, "out": 1.0, "gain": 0.3, "fade_in": 0.0, "fade_out": 0.0},
        {"path": str(bed), "at": 3.5, "in": 1.0, "out": 2.0, "gain": 0.15, "fade_in": 0.0, "fade_out": 0.0},
    ]
    return out, clips


def _leaves_nothing(out: Path) -> bool:
    part = out.with_suffix(".music.part.mp4")
    return not part.exists() and not part.with_suffix(".log").exists()


def test_the_mix_uses_the_resolved_ffmpeg_the_graph_and_copies_the_video(tmp_path, monkeypatch):
    started = _fake_mix(monkeypatch, path="C:/tools/ffmpeg.exe")
    out, clips = _files(tmp_path)

    assert video_creator.mix_music(out, clips, out, output_seconds=4.0) is True
    (proc,) = started
    cmd = proc.cmd
    assert cmd[0] == "C:/tools/ffmpeg.exe"
    assert cmd[1:6] == ["-y", "-hide_banner", "-nostats", "-loglevel", "error"]
    inputs = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]
    assert inputs == [str(out), clips[0]["path"], clips[1]["path"]], "the video first, then each DISTINCT file once"
    assert cmd[cmd.index("-filter_complex") + 1] == video_creator.music_filtergraph(clips, inputs[1:])
    maps = [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-map"]
    assert maps == ["0:v", "[a]"]
    assert cmd[cmd.index("-c:v") + 1] == "copy", "the picture is not touched"
    assert cmd[cmd.index("-c:a") + 1] == "aac" and cmd[cmd.index("-b:a") + 1] == "192k"
    assert cmd[cmd.index("-movflags") + 1] == "+faststart"
    part = out.with_suffix(".music.part.mp4")
    assert cmd[-1] == str(part) and part.name == "clip_revoiced.music.part.mp4", "written aside, then published"
    assert "-ss" not in cmd and "-shortest" not in cmd and "-r" not in cmd and "ffprobe" not in cmd[0]
    assert proc.kwargs["stdin"] is subprocess.DEVNULL and hasattr(proc.kwargs["stderr"], "write"), (
        "stderr goes to a file, never a pipe nobody reads"
    )
    assert out.read_bytes() == b"x", "the fake's output replaced the voice-only file in place"
    assert _leaves_nothing(out)


def test_a_cancel_while_ffmpeg_runs_kills_it_and_leaves_the_voice_only_file(tmp_path, monkeypatch):
    started = _fake_mix(monkeypatch, polls=10**9)
    out, clips = _files(tmp_path)
    asked = {"n": 0}

    def cancel_check():
        asked["n"] += 1
        return asked["n"] >= 3

    assert video_creator.mix_music(out, clips, out, cancel_check=cancel_check, output_seconds=4.0) is False
    (proc,) = started
    assert proc.killed is True and proc.returncode == -9
    assert out.read_bytes() == b"REVOICED" and _leaves_nothing(out)


def test_a_stalled_mix_is_killed_at_its_deadline_from_the_outputs_length(tmp_path, monkeypatch):
    """The deadline is ``music_timeout(OUTPUT_seconds)``; without a length
    the same rule runs over the clips' furthest end.

    The two rules are held far apart on purpose: an hour of output under
    clips reaching 4.5 s is 7260 s one way and 69 s the other, so the poll
    count on a clock ticking 50 s a poll can only match one of them. (With
    the numbers close together - 70 s against 69 - a deadline taken from the
    clips passed this test, and would kill a legitimate hour-long render
    ninety seconds in.)"""
    sized: list[float] = []
    real_timeout = video_creator.music_timeout
    monkeypatch.setattr(video_creator, "music_timeout",
                        lambda seconds: (sized.append(seconds), real_timeout(seconds))[1])
    started = _fake_mix(monkeypatch, polls=10**9)
    ticks = iter(range(0, 1_000_000, 50))
    monkeypatch.setattr(video_creator, "_clock", lambda: float(next(ticks)))
    out, clips = _files(tmp_path)
    assert max(clip["at"] + clip["out"] - clip["in"] for clip in clips) == 4.5, "the clips reach 4.5 s"

    assert video_creator.mix_music(out, clips, out, output_seconds=3600.0) is False
    (proc,) = started
    assert proc.killed is True
    assert sized == [3600.0], "the output's length, not the clips' reach"
    assert 140 <= proc.polls <= 150, "a 7260 s deadline on a clock ticking 50 s a poll, not 69 s (2 polls)"
    assert out.read_bytes() == b"REVOICED" and _leaves_nothing(out)

    sized.clear()
    started = _fake_mix(monkeypatch, polls=10**9)
    ticks = iter(range(0, 1_000_000, 50))
    far = [{**clips[0], "at": 100.0, "in": 0.0, "out": 20.0}]  # reaches 120 s: 60 + 2 x 120 = 300
    assert video_creator.mix_music(out, far, out) is False
    (proc,) = started
    assert proc.killed is True and 5 <= proc.polls <= 7, "a 300 s deadline from the clips' reach"
    assert sized == [120.0], "no output length: the furthest end the clips reach"


def test_the_flag_is_read_before_ffmpeg_starts_and_before_publishing(tmp_path, monkeypatch):
    out, clips = _files(tmp_path)
    started = _fake_mix(monkeypatch)
    assert video_creator.mix_music(out, clips, out, cancel_check=lambda: True) is False
    assert started == [] and out.read_bytes() == b"REVOICED" and _leaves_nothing(out)

    started = _fake_mix(monkeypatch)
    answers = iter([False, True])
    assert video_creator.mix_music(out, clips, out, cancel_check=lambda: next(answers, True)) is False
    (proc,) = started
    assert proc.killed is False and proc.returncode == 0
    assert out.read_bytes() == b"REVOICED" and _leaves_nothing(out), "the finished part is discarded, not published"


def test_a_failed_mix_answers_false_and_leaves_the_voice_only_file(tmp_path, monkeypatch):
    out, clips = _files(tmp_path)

    _fake_mix(monkeypatch, returncode=1, stderr="Invalid argument")
    assert video_creator.mix_music(out, clips, out, output_seconds=4.0) is False
    assert out.read_bytes() == b"REVOICED" and _leaves_nothing(out), "the part and the log are removed"

    _fake_mix(monkeypatch, writes=False)
    assert video_creator.mix_music(out, clips, out, output_seconds=4.0) is False, "exit 0 but no video"
    assert _leaves_nothing(out)

    _fake_mix(monkeypatch, **{"raise": OSError("ffmpeg crashed")})
    assert video_creator.mix_music(out, clips, out, output_seconds=4.0) is False, "ffmpeg could not even start"
    assert _leaves_nothing(out)

    started = _fake_mix(monkeypatch, path=None)
    assert video_creator.mix_music(out, clips, out, output_seconds=4.0) is False, "no ffmpeg at all"
    assert started == []
    started = _fake_mix(monkeypatch)
    assert video_creator.mix_music(out, [], out, output_seconds=4.0) is False, "no clips"
    assert started == []
    started = _fake_mix(monkeypatch)
    gone = [{**clips[0], "path": str(tmp_path / "lib" / "gone.mp3")}]
    assert video_creator.mix_music(out, gone, out, output_seconds=4.0) is False, "a file that is not there: refused, never silence"
    assert started == [] and out.read_bytes() == b"REVOICED"


def test_the_output_may_differ_from_the_input(tmp_path, monkeypatch):
    started = _fake_mix(monkeypatch)
    out, clips = _files(tmp_path)
    dst = tmp_path / "with-music" / "final.mp4"
    assert video_creator.mix_music(out, clips[:1], dst, output_seconds=4.0) is True
    (proc,) = started
    assert proc.cmd[-1] == str(dst.with_suffix(".music.part.mp4"))
    assert dst.read_bytes() == b"x" and out.read_bytes() == b"REVOICED"
    assert "[m1]amix" in proc.cmd[proc.cmd.index("-filter_complex") + 1], "one clip: no bed mix"


# ── the job: the pass after the mux ───────────────────────────────────────────

RATE = 1000
SIGNAL = np.zeros(2500, dtype=np.int16)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A logged-in TestClient with the store, the auth DB, the config, the
    scratch and the music library isolated, and the decode faked."""
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


@pytest.fixture
def library(client):
    """Two library files (30 s each, by the faked decode)."""
    music.add_file("bed.mp3", b"BED", "u")
    music.add_file("sting.wav", b"STING", "u")
    return {"bed.mp3": music.get_path("bed.mp3"), "sting.wav": music.get_path("sting.wav")}


CLIP_BED = {"id": "bed", "file": "bed.mp3", "at": 0.0, "in": 0.0, "out": 3.0, "gain": 0.15, "fade_in": 0.5, "fade_out": 0.5}
CLIP_STING = {"id": "sting", "file": "sting.wav", "at": 1.0, "in": 2.0, "out": 3.0, "gain": 0.3, "fade_in": 0.0, "fade_out": 0.0}


def _video(seconds: float = 4.0) -> str:
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [
        {"start": 0.0, "end": 2.0, "text": "Hello there."},
        {"start": 2.0, "end": 4.0, "text": "This is a test."},
    ])
    with wave.open(str(store.PROJECTS_DIR / pid / "audio.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(1000)
        wf.writeframes(np.zeros(int(round(seconds * 1000)), dtype="<i2").tobytes())
    return pid


def _recorders(monkeypatch, *, mix_ok=True, on_mix=None):
    """Stand-ins for ``cut_picture`` and ``mix_music`` that share one log, so
    a test can see the ORDER as well as the arguments."""
    log: list[dict] = []

    def fake_cut(source, keep, dst, video_bitrate="", cancel_check=None):
        log.append({"step": "cut", "source": Path(source), "keep": keep, "dst": Path(dst)})
        Path(dst).write_bytes(b"CUT-PICTURE")
        return True

    def fake_mix(video_in, clips, video_out, cancel_check=None, output_seconds=None):
        # The record as it stands WHILE the pass runs: the mux's stamps are
        # written before this, so a cancel or a failure here leaves a record
        # that describes the voice-only file on disk.
        log.append({"step": "mix", "video_in": Path(video_in), "clips": clips, "video_out": Path(video_out),
                    "cancel_check": cancel_check, "output_seconds": output_seconds,
                    "input_bytes": Path(video_in).read_bytes(),
                    "record": store.get_project(Path(video_in).parent.name)})
        if on_mix:
            on_mix()
        if mix_ok:
            Path(video_out).write_bytes(b"FAKEREVOICE+MUSIC")
        return mix_ok

    monkeypatch.setattr(video_creator, "cut_picture", fake_cut)
    monkeypatch.setattr(video_creator, "mix_music", fake_mix)
    return log


def _previous_render(pid: str, *, clips: int) -> str:
    """Stamp the record as a successful earlier render of ``clips`` clips and
    return its ``revoiced_at`` - what the next job must not leave behind
    over bytes it has already replaced."""
    stamp = "2020-01-01T00:00:00+00:00"
    record = store.get_project(pid)
    record.update({"revoiced_video": "clip_revoiced.mp4", "revoiced_at": stamp,
                   "edit_rendered_at": stamp, "music_rendered": clips})
    store.save_project(record)
    return stamp


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


class _KeepsNarration(_FakeVideoProcessor):
    """Leaves the standalone narration track behind, as the real engine does."""

    def _revoice_video(self, pm, source_video, output_path, progress=None, file_label=""):
        ok = super()._revoice_video(pm, source_video, output_path, progress=progress, file_label=file_label)
        processing.narration_path_for(output_path).write_bytes(b"NARRATION")
        return ok


def test_a_missing_music_file_is_refused_before_any_work(client, library, monkeypatch):
    """Trap 25, the E1 rule: never silence in a file's place. The clip the
    library reports missing fails the job before the engine, the cut or the
    mix run; so does a file that has left the disk since the index was read."""
    log = _recorders(monkeypatch)
    pid = _video()
    record = store.get_project(pid)
    record["edit"] = {"version": 2, "video": {"keep": [[0.0, 3.0]]},
                      "music": [dict(CLIP_BED), {**CLIP_STING, "file": "gone.mp3"}]}
    store.save_project(record)

    job = _revoice(client, pid)
    assert job["status"] == "error", job
    assert job["error"] == "music file 'gone.mp3' is missing — remove the clip or upload the file again"
    assert log == [] and _FakeVideoProcessor.captured is None, "nothing ran"
    assert "revoiced_video" not in store.get_project(pid)

    # In the index but not on disk: the same refusal, before any work.
    record["edit"]["music"] = [dict(CLIP_STING)]
    store.save_project(record)
    library["sting.wav"].unlink()
    job = _revoice(client, pid)
    assert job["status"] == "error" and "music file 'sting.wav' is missing" in job["error"]
    assert log == [] and _FakeVideoProcessor.captured is None


def test_the_music_is_mixed_after_the_mux_in_place_with_the_clips_paths(client, library, monkeypatch):
    """A music-only edit: no cut, the engine muxes onto the untouched source
    exactly as ever, and THEN the clips are laid under the muxed output in
    place - the picture's length handed to the pass as its bound, the job's
    cancel flag as its check - and the record says so."""
    monkeypatch.setattr(processing, "VideoProcessor", _KeepsNarration)
    log = _recorders(monkeypatch)
    pid = _video(4.0)
    stored, changed = edit.set_edit(pid, music=[CLIP_STING, CLIP_BED])
    assert changed and stored["music"][0]["id"] == "bed"
    progress: list[tuple[float, str]] = []

    result = revoice.revoice_project(pid, "v", progress=lambda f, m="": progress.append((f, m)))
    assert result == {"video": "clip_revoiced.mp4", "language": None, "failed_sentences": 0}

    out = store.PROJECTS_DIR / pid / "clip_revoiced.mp4"
    (mix,) = log
    assert mix["step"] == "mix"
    assert mix["video_in"] == out == mix["video_out"], "the muxed output, mixed in place"
    assert mix["input_bytes"] == b"FAKEREVOICE", "after the mux: the engine's file is what is mixed"
    assert mix["clips"] == [
        {"path": str(library["bed.mp3"]), "at": 0.0, "in": 0.0, "out": 3.0, "gain": 0.15, "fade_in": 0.5, "fade_out": 0.5},
        {"path": str(library["sting.wav"]), "at": 1.0, "in": 2.0, "out": 3.0, "gain": 0.3, "fade_in": 0.0, "fade_out": 0.0},
    ], "the library's paths, in the stored (sorted) order, no derived keys"
    assert mix["cancel_check"] is jobs.cancel_requested_here
    assert mix["output_seconds"] == 4.0, "the picture's length: the source's, uncut"
    assert _FakeVideoProcessor.source == store.PROJECTS_DIR / pid / "clip.mp4", "no picture step"
    assert progress.index((0.9, "revoicing")) < progress.index((0.95, "Mixing 2 music clips…"))
    assert [f for f, _ in progress] == sorted(f for f, _ in progress), "the job never steps backwards"

    # The voice-only file was stamped before the pass ran; the mix stamps again.
    during = mix["record"]
    assert during["revoiced_video"] == "clip_revoiced.mp4" and "music_rendered" not in during
    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4" and out.read_bytes() == b"FAKEREVOICE+MUSIC"
    assert saved["music_rendered"] == 2
    assert saved["revoiced_at"] > during["revoiced_at"], "the bytes changed again, so the cache token moves"
    assert saved["edit_rendered_at"] == saved["revoiced_at"], "the output differs from an unedited render"
    assert processing.narration_path_for(out).read_bytes() == b"NARRATION", "the voice-only track is never opened"
    assert saved["narration_audio"] == "clip_revoiced_narration.mp3"
    assert client.get(f"/api/projects/{pid}/revoiced-video").content == b"FAKEREVOICE+MUSIC"


def test_a_cut_and_music_together_run_the_cut_first_and_hand_the_mix_the_cut_pictures_length(client, library, monkeypatch):
    log = _recorders(monkeypatch)
    pid = _video(4.0)
    keep = [[0.0, 1.0], [1.5, 4.0]]  # 3.5 s
    assert client.put(f"/api/projects/{pid}/edit", json={"video": keep, "music": [CLIP_BED]}).status_code == 200

    assert _revoice(client, pid)["status"] == "done"
    assert [entry["step"] for entry in log] == ["cut", "mix"]
    cut, mix = log
    assert cut["keep"] == keep and _FakeVideoProcessor.source == cut["dst"]
    assert mix["video_in"] == store.PROJECTS_DIR / pid / "clip_revoiced.mp4", "the muxed output, never the cut intermediate"
    assert mix["output_seconds"] == 3.5
    assert len(mix["clips"]) == 1 and mix["clips"][0]["path"] == str(library["bed.mp3"])
    saved = store.get_project(pid)
    assert saved["music_rendered"] == 1 and saved["edit_rendered_at"] == saved["revoiced_at"]


def test_a_run_without_clips_pops_music_rendered_and_clears_the_stamp(client, library, monkeypatch):
    log = _recorders(monkeypatch)
    pid = _video(4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_BED]}).status_code == 200
    assert _revoice(client, pid)["status"] == "done"
    assert store.get_project(pid)["music_rendered"] == 1 and store.get_project(pid).get("edit_rendered_at")

    assert client.put(f"/api/projects/{pid}/edit", json={"music": []}).status_code == 200
    assert _revoice(client, pid)["status"] == "done"
    assert [entry["step"] for entry in log] == ["mix"], "no clips, no pass"
    saved = store.get_project(pid)
    assert "music_rendered" not in saved and "edit_rendered_at" not in saved
    assert (store.PROJECTS_DIR / pid / "clip_revoiced.mp4").read_bytes() == b"FAKEREVOICE"


def test_a_cancel_during_the_mix_leaves_a_record_that_describes_the_voice_only_file(client, library, monkeypatch):
    """``_revoice_video`` has already rewritten ``<stem>_revoiced.mp4`` by
    the time the pass runs, so the file on disk IS a new render - just
    without music. The mux's stamps are written before the pass for exactly
    that reason: a cancel leaves a fresh ``revoiced_at`` (the page's cache
    token: the bytes did change) and no ``music_rendered``, instead of the
    previous render's clip count over bytes that no longer carry it."""
    def _cancel_this_job():
        jobs.cancel(jobs.current_job_id())

    _recorders(monkeypatch, mix_ok=False, on_mix=_cancel_this_job)
    pid = _video(4.0)
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_BED]}).status_code == 200
    stale = _previous_render(pid, clips=3)

    job = _revoice(client, pid)
    assert job["status"] == "done" and job["message"] == "Cancelled", job
    assert job["result"] == {"cancelled": True}
    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["revoiced_at"] != stale, "the bytes changed, so the page's cache token had to"
    assert "music_rendered" not in saved, "the file on disk carries none - not the 3 the last render mixed"
    assert "edit_rendered_at" not in saved, "nothing was cut either"
    assert (store.PROJECTS_DIR / pid / "clip_revoiced.mp4").read_bytes() == b"FAKEREVOICE", "voice only, left on disk"


def test_a_mix_that_fails_fails_the_job_with_the_reason(client, library, monkeypatch):
    """The job fails, and the record still describes what is on disk."""
    _recorders(monkeypatch, mix_ok=False)
    pid = _video(4.0)
    keep = [[0.0, 1.0], [1.5, 4.0]]
    assert client.put(f"/api/projects/{pid}/edit", json={"video": keep, "music": [CLIP_BED]}).status_code == 200
    stale = _previous_render(pid, clips=3)

    job = _revoice(client, pid)
    assert job["status"] == "error"
    assert job["error"] == "The music could not be mixed; the server log has the reason."
    saved = store.get_project(pid)
    assert saved["revoiced_video"] == "clip_revoiced.mp4"
    assert saved["revoiced_at"] != stale and "music_rendered" not in saved, "the mix never happened"
    assert saved["edit_rendered_at"] == saved["revoiced_at"], "but the picture WAS cut, and the file says so"


def test_a_project_without_music_never_touches_the_library_or_the_pass(client, monkeypatch):
    """The path every project has always taken: no clips, no library read,
    no pass, no stamp - byte for byte E3's."""
    log = _recorders(monkeypatch)
    monkeypatch.setattr(music, "library", lambda: (_ for _ in ()).throw(AssertionError("the library was read")))
    pid = _video(4.0)
    assert _revoice(client, pid)["status"] == "done"
    assert log == []
    saved = store.get_project(pid)
    assert "music_rendered" not in saved and "edit_rendered_at" not in saved


# ── the real binary ───────────────────────────────────────────────────────────

def _real_ffmpeg() -> str | None:
    path = config_module.FFMPEG_PATH
    return path if path and Path(path).is_file() else None


def test_every_filter_the_graph_names_is_in_this_ffmpeg():
    """Trap 17: a filter is checked against the binary's ``-filters`` before
    it is relied on. The bundled 7.1 essentials was checked by hand
    (2026-09-21); this checks whichever binary the suite resolves."""
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no ffmpeg on this machine")
    listed = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=60).stdout
    names = {line.split()[1] for line in listed.splitlines() if len(line.split()) > 2 and "->" in line}
    assert {"adelay", "afade", "amix", "asetpts", "atrim", "pan", "volume"} <= names


def _channel_levels(ffmpeg: str, video: Path, wav: Path, window: tuple[float, float]) -> list[float]:
    """The RMS of each channel of ``video``'s audio over ``window``, decoded
    without a down-mix - the only way to see an up-mix change a level (a
    mono file read back as one channel hides it)."""
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn",
                    "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True, timeout=60)
    with wave.open(str(wav), "rb") as wf:
        channels, rate = wf.getnchannels(), wf.getframerate()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float64)
    frames = samples.reshape(-1, channels)[int(window[0] * rate):int(window[1] * rate)]
    return [float(np.sqrt(np.mean(frames[:, c] ** 2))) for c in range(channels)]


@pytest.mark.parametrize("channels", [1, 2])
def test_a_real_mix_leaves_the_voices_level_exactly_where_it_was(tmp_path, channels):
    """MAJOR 2, measured on the real binary. A MONO narration - what the
    local kokoro path writes, at 24 kHz - came back 3.01 dB quieter from
    the music pass than from a render without music, because ffmpeg's
    mono-to-stereo rematrix is power-preserving (M/sqrt(2) per channel).
    The up-mix is ``pan`` now: every output channel carries the voice at
    exactly its own level, from a mono input and a stereo one alike, with
    the clip placed outside the window so only the up-mix is in it."""
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no ffmpeg on this machine")
    layout = "mono" if channels == 1 else "stereo"
    video = tmp_path / f"{layout}_revoiced.mp4"
    made = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=4",
         "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=24000:duration=4,aformat=channel_layouts={layout}",
         "-t", "4", "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "192k", "-shortest", str(video)],
        capture_output=True, text=True, timeout=120,
    )
    if made.returncode != 0:
        pytest.skip(f"this ffmpeg cannot make the fixture: {made.stderr[-200:]}")
    tone = tmp_path / "tone.wav"
    frames = (np.sin(2 * np.pi * 1200 * np.arange(8000) / 8000.0) * 20000).astype("<i2")
    with wave.open(str(tone), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(np.repeat(frames, 2).tobytes())

    quiet = (0.5, 2.5)  # the clip is at 3.0 s: no music at all in this window
    before = _channel_levels(ffmpeg, video, tmp_path / "before.wav", quiet)
    assert len(before) == channels and before[0] > 100, before

    clips = [{"path": str(tone), "at": 3.0, "in": 0.0, "out": 1.0, "gain": 0.5, "fade_in": 0.0, "fade_out": 0.0}]
    assert video_creator.mix_music(video, clips, video, output_seconds=4.0) is True

    after = _channel_levels(ffmpeg, video, tmp_path / "after.wav", quiet)
    assert len(after) == 2, "the pass always writes stereo"
    for index, level in enumerate(after):
        db = 20 * np.log10(level / before[min(index, channels - 1)])
        assert abs(db) < 0.1, f"channel {index} moved {db:+.2f} dB (aformat's rematrix is -3.01 dB for mono)"


def _decoded_audio(ffmpeg: str, video: Path, wav: Path) -> tuple[np.ndarray, int]:
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-c:a", "pcm_s16le",
                    "-ac", "1", str(wav)], check=True, capture_output=True, timeout=60)
    with wave.open(str(wav), "rb") as wf:
        rate = wf.getframerate()
        samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float64)
    return samples, rate


def test_a_real_mix_places_the_clip_where_the_edit_says_and_keeps_the_picture(tmp_path):
    """One real pass: a 4 s test-card video with a silent voice track, a
    loud 1 s tone laid at 2.0 s. Decoded back, the output is silent before
    the clip, loud during it and silent after; its length is the video's
    (``duration=first``), and its picture is still there (copied)."""
    ffmpeg = _real_ffmpeg()
    if not ffmpeg:
        pytest.skip("no ffmpeg on this machine")
    video = tmp_path / "clip_revoiced.mp4"
    made = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=4",
         "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
         "-t", "4", "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)],
        capture_output=True, text=True, timeout=120,
    )
    if made.returncode != 0:
        pytest.skip(f"this ffmpeg cannot make the fixture: {made.stderr[-200:]}")
    tone = tmp_path / "tone.wav"
    t = np.arange(8000) / 8000.0
    mono = (np.sin(2 * np.pi * 440 * t) * 20000).astype("<i2")
    with wave.open(str(tone), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(8000)
        wf.writeframes(np.repeat(mono, 2).tobytes())
    clips = [{"path": str(tone), "at": 2.0, "in": 0.0, "out": 1.0, "gain": 0.5, "fade_in": 0.0, "fade_out": 0.0}]
    before = video.stat().st_size

    assert video_creator.mix_music(video, clips, video, output_seconds=4.0) is True
    assert _leaves_nothing(video) and video.stat().st_size > 0

    samples, rate = _decoded_audio(ffmpeg, video, tmp_path / "back.wav")
    seconds = samples.size / rate
    assert 3.9 <= seconds <= 4.2, f"the voice's length, not the clip's: {seconds:.3f}s"

    def rms(a: float, b: float) -> float:
        span = samples[int(a * rate):int(b * rate)]
        return float(np.sqrt(np.mean(span * span))) if span.size else 0.0

    assert rms(0.2, 1.8) < 50, "silent before the clip"
    assert rms(2.1, 2.9) > 3000, "the tone, at half gain, where the clip was placed"
    assert rms(3.2, 3.9) < 50, "silent after it"
    # The picture is still there and decodes.
    frame = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video), "-frames:v", "1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=60)
    assert frame.returncode == 0 and len(frame.stdout) == 64 * 64 * 3
    assert before > 0
