"""A re-voice honours a cancel while it synthesises (E7 #3).

The long part of a re-voice is the synthesis: one voice-service round trip per
sentence. ``VideoProcessor._revoice_video`` asks its ``cancel_check`` before
the speaking-rate measurement, before every sentence and once more before the
mux, and on a cancel returns False with ``cancelled`` set having written
nothing outside its scratch directory, which it removes - the output file and
its narration track are exactly as they were.

The engine runs for real here with everything below it stubbed as
``tests/test_revoice_sync.py`` stubs it: pydub is a stand-in whose segments
carry only a length, the TTS writes a byte per sentence, and the mux is a
recorder. The job-level half - the record, the files and the closing line -
is ``tests/test_revoice.py`` (the cut, the engine's cancel, the translation)
and ``tests/test_music_render.py`` (the mix); the last test here runs a real
job through the real engine end to end.
"""

import sys
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.project_manager import ProjectManager
from services import jobs, processing, revoice
from services import projects as store
from utils.config import config

from test_revoice_sync import _FakeTTS, _Seg

SENTENCES = [
    {"start": 0.0, "end": 2.0, "text": "One."},
    {"start": 2.0, "end": 4.0, "text": "Two."},
    {"start": 4.0, "end": 6.0, "text": "Three."},
    {"start": 6.0, "end": 8.0, "text": "Four."},
]


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """The engine with everything below it stubbed: returns what the tests
    look at - the TTS, the mux calls, the calibration calls and the scratch
    directories the run made."""
    fake = types.ModuleType("pydub")
    fake.AudioSegment = _Seg
    monkeypatch.setitem(sys.modules, "pydub", fake)
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(_Seg, "next_ms", 1500)

    scratches: list[Path] = []

    def _scratch(prefix=""):
        d = tmp_path / f"scratch_{prefix}{len(scratches)}"
        d.mkdir(parents=True)
        scratches.append(d)
        return d

    monkeypatch.setattr(processing, "_job_scratch", _scratch)
    tts = _FakeTTS()
    calibrations: list[int] = []
    muxes: list[dict] = []
    monkeypatch.setattr(processing.VideoProcessor, "_create_tts_generator", lambda self: tts)
    monkeypatch.setattr(
        processing.VideoProcessor, "_calibrate_tts_baseline",
        lambda self, *a, **k: calibrations.append(1) or 15.0,
    )

    import core.video_creator as vc

    def _mux(**kw):
        muxes.append(kw)
        Path(kw["output_path"]).write_bytes(b"NEW-REVOICE")
        return True

    monkeypatch.setattr(vc, "replace_video_audio", _mux)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", lambda path: 8.0)
    return {"tts": tts, "muxes": muxes, "calibrations": calibrations, "scratches": scratches}


def _manager(tmp_path) -> tuple[ProjectManager, Path]:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    pm = ProjectManager(tmp_path / "proj")
    pm.create_project(pptx_path=source, slide_notes=[" ".join(s["text"] for s in SENTENCES)], voice_id="v")
    slide0 = pm.state.slides[0]
    slide0.original_segments = [dict(s) for s in SENTENCES]
    slide0.original_start_time, slide0.original_end_time = 0.0, 8.0
    pm.state.source_video_path = str(source)
    return pm, source


def _previous_output(tmp_path) -> Path:
    out = tmp_path / "clip_revoiced.mp4"
    out.write_bytes(b"PREVIOUS")
    processing.narration_path_for(out).write_bytes(b"PREVIOUS-NARRATION")
    return out


def _cancel_after(tts: _FakeTTS, sentences: int):
    """A cancel flag that goes up once ``sentences`` have been synthesised."""
    return lambda: len(tts.calls) >= sentences


def test_a_cancel_between_sentences_stops_the_synthesis_and_writes_nothing(engine, tmp_path):
    pm, source = _manager(tmp_path)
    out = _previous_output(tmp_path)
    proc = processing.VideoProcessor(voice_id="en-US-AriaNeural")

    ok = proc._revoice_video(pm, source, out, cancel_check=_cancel_after(engine["tts"], 2))

    assert ok is False and proc.cancelled is True
    assert [c["text"] for c in engine["tts"].calls] == ["One.", "Two."], "no sentence after the flag was seen"
    assert engine["muxes"] == [], "never muxed"
    assert out.read_bytes() == b"PREVIOUS", "the previous output is untouched"
    assert processing.narration_path_for(out).read_bytes() == b"PREVIOUS-NARRATION"
    assert engine["scratches"] and not any(d.exists() for d in engine["scratches"]), "the scratch directory is removed"


def test_a_cancel_before_the_synthesis_synthesises_nothing(engine, tmp_path):
    pm, source = _manager(tmp_path)
    out = _previous_output(tmp_path)
    proc = processing.VideoProcessor(voice_id="en-US-AriaNeural")

    assert proc._revoice_video(pm, source, out, cancel_check=lambda: True) is False
    assert proc.cancelled is True
    assert engine["tts"].calls == [] and engine["calibrations"] == [], "not even the speaking-rate measurement"
    assert engine["muxes"] == [] and out.read_bytes() == b"PREVIOUS"
    assert not any(d.exists() for d in engine["scratches"])


def test_a_cancel_after_the_last_sentence_stops_before_the_mux(engine, tmp_path):
    pm, source = _manager(tmp_path)
    out = _previous_output(tmp_path)
    proc = processing.VideoProcessor(voice_id="en-US-AriaNeural")

    assert proc._revoice_video(pm, source, out, cancel_check=_cancel_after(engine["tts"], len(SENTENCES))) is False
    assert proc.cancelled is True and len(engine["tts"].calls) == len(SENTENCES)
    assert engine["muxes"] == [], "the last check is before the mux"
    assert out.read_bytes() == b"PREVIOUS"
    assert not any(d.exists() for d in engine["scratches"])


def test_with_no_cancel_the_run_is_what_it_always_was(engine, tmp_path):
    """The check costs nothing when nobody asks: every sentence, one mux, the
    narration track kept - with a flag that never goes up and with none."""
    for cancel_check in (lambda: False, None):
        pm, source = _manager(tmp_path)
        out = _previous_output(tmp_path)
        engine["tts"].calls.clear()
        engine["muxes"].clear()
        proc = processing.VideoProcessor(voice_id="en-US-AriaNeural")

        assert proc._revoice_video(pm, source, out, cancel_check=cancel_check) is True
        assert proc.cancelled is False
        assert len(engine["tts"].calls) == len(SENTENCES) and len(engine["muxes"]) == 1
        assert out.read_bytes() == b"NEW-REVOICE"
        assert processing.narration_path_for(out).read_bytes() == b"MASTER", "the new narration track"


# ── one real job through the real engine ──────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")

    from api import store as auth_store

    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")

    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def test_a_job_cancelled_mid_synthesis_leaves_the_record_and_the_previous_re_voice_as_they_were(
    engine, client, tmp_path,
):
    """The whole chain: the route, the job, the real ``revoice_project`` and
    the real ``_revoice_video``, the TTS raising the job's own flag after
    the second sentence. The job ends ``done`` and cancelled at the
    synthesis stage with the line the card shows; the record is exactly as
    it was, the previous re-voice's two files are the ones it names, and no
    scratch directory is left."""
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [dict(s) for s in SENTENCES])
    out = store.PROJECTS_DIR / pid / "clip_revoiced.mp4"
    out.write_bytes(b"PREVIOUS")
    processing.narration_path_for(out).write_bytes(b"PREVIOUS-NARRATION")
    record = store.get_project(pid)
    record.update({"revoiced_video": out.name, "revoiced_at": "2020-01-01T00:00:00+00:00",
                   "narration_audio": processing.narration_path_for(out).name})
    store.save_project(record)
    before = store.get_project(pid)
    tts = engine["tts"]
    real_generate = tts.generate_audio

    def generate_then_cancel(*args, **kwargs):
        result = real_generate(*args, **kwargs)
        if len(tts.calls) == 2:
            jobs.cancel(jobs.current_job_id())
        return result

    tts.generate_audio = generate_then_cancel

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "en-US-AriaNeural"})
    assert r.status_code == 200, r.text
    deadline = time.time() + 10
    while (job := client.get(f"/api/jobs/{r.json()['job_id']}").json())["status"] not in ("done", "error"):
        assert time.time() < deadline, "the job did not finish"
        time.sleep(0.02)

    assert job["status"] == "done", job
    assert job["result"] == {"cancelled": True, "stage": "synthesis"}
    assert job["message"] == revoice.CANCELLED_BEFORE_RENDER
    assert [c["text"] for c in tts.calls] == ["One.", "Two."]
    assert engine["muxes"] == []
    assert store.get_project(pid) == before, "the record is unchanged"
    assert out.read_bytes() == b"PREVIOUS" and processing.narration_path_for(out).read_bytes() == b"PREVIOUS-NARRATION"
    assert not any(d.exists() for d in engine["scratches"]), "no scratch directory is left"
    assert jobs.active_for(pid) is None
