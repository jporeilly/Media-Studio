"""Re-voice keeps the narration in step with the picture.

An imported video is reconstructed as ONE section holding every transcript
sentence, so the alignment that matters is per sentence: each one is pinned to
the moment it was spoken and the pause that followed it is preserved. Pinning
only the first sentence and running the rest on made the narration drift
further ahead of the picture with every pause it swallowed.

No audio is decoded and no TTS or ffmpeg runs: pydub is a stub whose segments
carry only a length, the generator writes a byte, and the clip length per
sentence is whatever the test asks for.
"""

import sys
import types
from pathlib import Path

import pytest

from core.project_manager import ProjectManager
from services import processing
from utils.config import config


class _Seg:
    """A pydub.AudioSegment stand-in: only lengths matter here."""

    def __init__(self, ms=0):
        self.ms = ms

    @classmethod
    def silent(cls, duration=0):
        return cls(int(duration))

    @classmethod
    def from_file(cls, path):
        return cls(_Seg.next_ms)

    def __add__(self, other):
        return _Seg(self.ms + other.ms)

    def __len__(self):
        return self.ms

    def export(self, path, **kwargs):
        Path(path).write_bytes(b"MASTER")


_Seg.next_ms = 1500


class _FakeTTS:
    """Writes a file per sentence and records the speed it was asked for."""

    def __init__(self):
        self.calls = []

    def generate_audio(self, text, voice_id, output_path, speed=1.0, **kwargs):
        self.calls.append({"text": text, "speed": speed})
        Path(output_path).write_bytes(b"mp3")
        return True


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Stub pydub, keep the engine off the config, and scratch under tmp."""
    fake = types.ModuleType("pydub")
    fake.AudioSegment = _Seg
    monkeypatch.setitem(sys.modules, "pydub", fake)
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)

    def _scratch(prefix=""):
        d = tmp_path / f"scratch_{prefix or 'x'}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(processing, "_job_scratch", _scratch)
    _Seg.next_ms = 1500


def _manager(tmp_path, segments):
    """A ProjectManager whose single slide carries the transcript, exactly as
    services/revoice.py reconstructs it for an imported video."""
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    notes = " ".join(s["text"] for s in segments)
    pm = ProjectManager(tmp_path / "proj")
    pm.create_project(pptx_path=source, slide_notes=[notes], voice_id="v")
    slide0 = pm.state.slides[0]
    slide0.original_segments = [dict(s) for s in segments]
    slide0.original_start_time = float(segments[0]["start"])
    slide0.original_end_time = float(segments[-1]["end"])
    pm.state.source_video_path = str(source)
    return pm


def _revoice(tmp_path, monkeypatch, segments, *, clip_ms=1500, free=False, speed=1.0):
    """Run _revoice_video with the engine stubbed; return the aligned chunks."""
    _Seg.next_ms = clip_ms
    pm = _manager(tmp_path, segments)
    if free:
        pm.state.revoice_sync_mode = "free"

    tts = _FakeTTS()
    proc = processing.VideoProcessor(voice_id="en-US-AriaNeural", speed=speed)
    monkeypatch.setattr(proc, "_create_tts_generator", lambda: tts)
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: 15.0)

    captured = {}
    real_assemble = processing.assemble_master

    def _assemble(chunks, is_free):
        captured["chunks"] = list(chunks)
        captured["is_free"] = is_free
        return real_assemble(chunks, is_free)

    monkeypatch.setattr(processing, "assemble_master", _assemble)
    monkeypatch.setattr(processing, "replace_video_audio", lambda **kw: True, raising=False)

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: True)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    ok = proc._revoice_video(pm, source, tmp_path / "out.mp4")
    assert ok, "re-voice returned False"
    return captured, tts


# Three sentences with real pauses between them: 2 s spoken, 3 s of silence,
# 2 s spoken, 3 s of silence, 2 s spoken.
GAPPY = [
    {"start": 0.0, "end": 2.0, "text": "First sentence."},
    {"start": 5.0, "end": 7.0, "text": "Second sentence."},
    {"start": 10.0, "end": 12.0, "text": "Third sentence."},
]


def test_every_sentence_is_pinned_to_the_moment_it_was_spoken(tmp_path, monkeypatch):
    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY)

    starts = [start for start, _end, _clip in captured["chunks"]]
    assert starts == [0.0, 5.0, 10.0], (
        "each sentence must carry its own pin; None means it runs on from the "
        "previous one and the original pause is lost"
    )


def test_the_pauses_between_sentences_survive_in_the_master(tmp_path, monkeypatch):
    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY, clip_ms=1500)

    master = processing.assemble_master(captured["chunks"], False)
    # 10 s of picture before the last sentence starts, plus that sentence.
    assert len(master) == pytest.approx(11500, abs=50)
    # Without the pins it would be the three clips back to back.
    assert len(master) > 3 * 1500


def test_a_sentence_borrows_the_pause_after_it_before_anything_is_sped_up(tmp_path, monkeypatch):
    """Every sentence here synthesises to 4 s from a 2 s original slot. The
    first two are due 5 s apart, so they simply use the pause that followed
    them and are left alone. The last has no pause to borrow — its slot ends
    with the speech, and the video may end with it — so that one is squeezed
    rather than risk being cut off mid-word by the video's end."""
    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed",
                        lambda self, *a, **k: 1.0)
    cmds = []

    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda cmd, **k: cmds.append(cmd) or types.SimpleNamespace(returncode=0))

    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY, clip_ms=4000)

    assert len(cmds) == 1, "only the final sentence should have been tempo-adjusted"
    assert any("seg0003" in str(part) for part in cmds[0]), cmds[0]
    assert [s for s, _e, _c in captured["chunks"]] == [0.0, 5.0, 10.0]


def test_a_sentence_longer_than_its_window_is_tempo_adjusted(tmp_path, monkeypatch, caplog):
    """8 s of speech where the next sentence is due in 5 s: sped up so the one
    after it still lands on time."""
    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed",
                        lambda self, *a, **k: 1.0)
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0)

    import subprocess as sp
    monkeypatch.setattr(sp, "run", _run)

    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY, clip_ms=8000)

    assert calls, "the overrunning sentence should have been tempo-adjusted"
    atempo = [c for cmd in calls for c in cmd if isinstance(c, str) and c.startswith("atempo=")]
    assert atempo and atempo[0].startswith("atempo=1.6"), atempo  # 8000 / 5000
    assert [s for s, _e, _c in captured["chunks"]] == [0.0, 5.0, 10.0]


def test_each_sentence_is_paced_against_its_own_window_not_the_whole_clip(tmp_path, monkeypatch):
    """The speed asked of the TTS is computed per sentence: the long pause after
    a short sentence is its slack, so it is never rushed to fit the clip."""
    seen = []

    def _speed(self, text, orig_duration, baseline, user_speed):
        seen.append((text, orig_duration))
        return user_speed

    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed", _speed)
    _revoice(tmp_path, monkeypatch, GAPPY)

    assert [text for text, _d in seen] == [
        "First sentence.", "Second sentence.", "Third sentence.",
    ], "each sentence is paced on its own, not as one joined block"
    # Window = until the next sentence is due; the last runs to the section end.
    assert [round(d, 2) for _t, d in seen] == [5.0, 5.0, 2.0]


def test_free_mode_still_runs_the_sentences_together(tmp_path, monkeypatch):
    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY, free=True, clip_ms=1500)

    assert captured["is_free"] is True
    master = processing.assemble_master(captured["chunks"], True)
    assert len(master) == 4500, "free pace concatenates: no silence is inserted"


def test_a_failed_sentence_leaves_its_slot_silent_without_shifting_the_rest(tmp_path, monkeypatch):
    """The sentence after a TTS failure is still pinned to its own moment."""
    tts = _FakeTTS()
    original = tts.generate_audio

    def _fail_second(text, voice_id, output_path, speed=1.0, **kwargs):
        if text == "Second sentence.":
            raise RuntimeError("voice unavailable")
        return original(text, voice_id, output_path, speed=speed, **kwargs)

    _Seg.next_ms = 1500
    pm = _manager(tmp_path, GAPPY)
    proc = processing.VideoProcessor(voice_id="v", speed=1.0)
    tts.generate_audio = _fail_second
    monkeypatch.setattr(proc, "_create_tts_generator", lambda: tts)
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: 15.0)

    captured = {}
    real_assemble = processing.assemble_master

    def _assemble(chunks, is_free):
        captured["chunks"] = list(chunks)
        return real_assemble(chunks, is_free)

    monkeypatch.setattr(processing, "assemble_master", _assemble)

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: True)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    assert proc._revoice_video(pm, source, tmp_path / "out.mp4") is True

    starts = [start for start, _end, _clip in captured["chunks"]]
    assert starts == [0.0, 10.0], "the third sentence keeps its own moment"
