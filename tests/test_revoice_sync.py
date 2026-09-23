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

    def __getitem__(self, item):
        # Only the trim slice (``audio[trim:]``) uses this; length is all the
        # callers here read back.
        return _Seg(self.ms)

    def export(self, path, **kwargs):
        Path(path).write_bytes(b"MASTER")


_Seg.next_ms = 1500


class _FakeTTS:
    """Writes a file per sentence and records the voice and speed it was asked for."""

    def __init__(self):
        self.calls = []

    def generate_audio(self, text, voice_id, output_path, speed=1.0, **kwargs):
        self.calls.append({"text": text, "voice_id": voice_id, "speed": speed})
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


def _revoice(tmp_path, monkeypatch, segments, *, clip_ms=1500, free=False, speed=1.0,
             video_seconds=None, mux_ok=True):
    """Run _revoice_video with the engine stubbed; return the aligned chunks.

    ``video_seconds`` is what the duration probe reports for the source video;
    None stands for a picture nothing could measure, which is the fallback the
    last sentence's bound has to cope with. ``mux_ok`` is what the final audio
    swap reports, for the paths that only matter when it fails.
    """
    monkeypatch.setattr(_Seg, "next_ms", clip_ms)
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

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: mux_ok)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", lambda path: video_seconds)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    ok = proc._revoice_video(pm, source, tmp_path / "out.mp4")
    assert ok is mux_ok, f"re-voice returned {ok}"
    captured["assemble"] = real_assemble
    captured["ok"] = ok
    captured["processor"] = proc
    return captured, tts


def _windows(monkeypatch) -> list:
    """Spy on ``_per_sentence_speed``: it is handed the window each sentence was
    measured against, which is the number the offsets and the mutes change."""
    seen = []

    def _speed(self, text, orig_duration, baseline, user_speed):
        seen.append((text, round(orig_duration, 3)))
        return user_speed

    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed", _speed)
    return seen


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

    master = captured["assemble"](captured["chunks"], False)
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
    master = captured["assemble"](captured["chunks"], True)
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


def test_the_last_sentence_may_use_the_video_after_it(tmp_path, monkeypatch):
    """The narration is padded to the video and trimmed there, so the tail after
    the closing sentence is room it may use. Bounding it at the speaker's own
    stop would squeeze it for nothing."""
    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed",
                        lambda self, *a, **k: 1.0)
    cmds = []
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda cmd, **k: cmds.append(cmd) or types.SimpleNamespace(returncode=0))

    # Last sentence spoken 10.0-12.0 but the video runs to 20.0: a 4 s synthesis
    # fits the room after it and must not be tempo-adjusted.
    _revoice(tmp_path, monkeypatch, GAPPY, clip_ms=4000, video_seconds=20.0)

    assert cmds == [], "the closing sentence may use the video's tail"


def test_without_a_duration_probe_the_last_sentence_keeps_its_own_slot(tmp_path, monkeypatch):
    """A picture nothing can measure: fall back to the section's end so the
    closing sentence cannot overrun the video and be cut off mid-word."""
    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed",
                        lambda self, *a, **k: 1.0)
    cmds = []
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda cmd, **k: cmds.append(cmd) or types.SimpleNamespace(returncode=0))

    _revoice(tmp_path, monkeypatch, GAPPY, clip_ms=4000, video_seconds=None)

    assert len(cmds) == 1 and any("seg0003" in str(part) for part in cmds[0]), cmds


def test_a_small_overrun_is_left_alone_rather_than_squeezed(tmp_path, monkeypatch):
    """Whisper often ends one sentence exactly where the next begins. A slightly
    long synthesis there is absorbed at the next real pause; tempo-adjusting
    every such sentence would make the speaking rate wobble."""
    tight = [
        {"start": 0.0, "end": 2.0, "text": "First sentence."},
        {"start": 2.0, "end": 4.0, "text": "Second sentence."},
        {"start": 8.0, "end": 10.0, "text": "Third sentence."},
    ]
    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed",
                        lambda self, *a, **k: 1.0)
    cmds = []
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda cmd, **k: cmds.append(cmd) or types.SimpleNamespace(returncode=0))

    # 2.2 s of speech in a 2.0 s slot: 10% over, inside the tolerance.
    captured, _ = _revoice(tmp_path, monkeypatch, tight, clip_ms=2200, video_seconds=12.0)

    assert cmds == [], "a 10% overrun should not be tempo-adjusted"
    # The third sentence still starts on time: the overrun ate into the pause.
    master = captured["assemble"](captured["chunks"], False)
    assert len(master) == pytest.approx(10200, abs=50)


def test_a_re_voice_where_every_sentence_fails_reports_failure(tmp_path, monkeypatch):
    """No audio at all is a failed job, not a silent video."""
    pm = _manager(tmp_path, GAPPY)
    proc = processing.VideoProcessor(voice_id="v", speed=1.0)

    class _DeadTTS:
        def generate_audio(self, **kwargs):
            raise RuntimeError("voice unavailable")

    monkeypatch.setattr(proc, "_create_tts_generator", lambda: _DeadTTS())
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: 15.0)

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: True)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", lambda path: 20.0)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    assert proc._revoice_video(pm, source, tmp_path / "out.mp4") is False


def test_the_narration_is_kept_beside_the_video_for_editing(tmp_path, monkeypatch):
    """The assembled narration lived only in the job's scratch directory and was
    deleted with it, so the one file an editor most wants - the new voice alone,
    starting at the same zero as the picture - was thrown away on every run. It
    is kept beside the re-voiced video now.

    The same START, not the same length: this is the master before the mux, and
    the pad out to the video's duration happens during the audio swap."""
    _revoice(tmp_path, monkeypatch, GAPPY)

    track = processing.narration_path_for(tmp_path / "out.mp4")
    assert track.is_file(), f"no narration track at {track}"
    assert track.name == "out_narration.mp3"


def test_no_narration_is_left_behind_when_the_audio_swap_fails(tmp_path, monkeypatch):
    """A track claiming to belong to a video that was never produced would be
    worse than none: it would be offered for download beside nothing."""
    _revoice(tmp_path, monkeypatch, GAPPY, mux_ok=False)

    assert not processing.narration_path_for(tmp_path / "out.mp4").exists()


def test_a_narration_copy_that_fails_does_not_fail_the_re_voice(tmp_path, monkeypatch):
    """The video is already made by the time the track is copied; losing the
    convenience is not worth losing the render."""
    import shutil as _shutil

    def _boom(src, dst, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(_shutil, "copy2", _boom)
    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY)

    assert captured["ok"] is True


def test_a_failed_copy_does_not_leave_the_previous_run_s_track(tmp_path, monkeypatch):
    """The track's name is the same every run, so a second re-voice whose copy
    failed would otherwise leave the FIRST run's file sitting there and the
    project would go on offering it - in the old voice, and the old language -
    as the narration for the new video. A copy that dies part way through would
    leave a truncated file the record swears is good. Either way the project
    must end up with no track rather than a lying one."""
    import shutil as _shutil

    _revoice(tmp_path, monkeypatch, GAPPY)
    track = processing.narration_path_for(tmp_path / "out.mp4")
    assert track.is_file(), "the first run must leave a track for this to mean anything"

    real_copy = _shutil.copy2

    def _copy_then_fail(src, dst, **kw):
        real_copy(src, dst, **kw)  # a partial write: the file exists, then it dies
        raise OSError("disk full")

    monkeypatch.setattr(_shutil, "copy2", _copy_then_fail)
    _revoice(tmp_path, monkeypatch, GAPPY)

    assert not track.exists(), "the previous run's track must not survive a failed copy"
    assert not track.with_name(track.name + ".part").exists(), "no half-written file left behind"


# -- per-sentence adjustments (services/narration.py) -------------------------
#
# The transcript may carry four optional keys per sentence, written by the
# narration editor: offset, muted, voice and speed. They ride into the engine
# inside ``original_segments`` and are read here. A project made before they
# existed carries none of them, which is what every test above goes on proving.


def _adjusted(**by_index) -> list:
    """GAPPY with adjustments applied to the named indexes."""
    out = [dict(s) for s in GAPPY]
    for index, changes in by_index.items():
        out[int(index)].update(changes)
    return out


def test_a_muted_sentence_is_never_synthesised(tmp_path, monkeypatch):
    captured, tts = _revoice(tmp_path, monkeypatch, _adjusted(**{"1": {"muted": True}}))

    assert [c["text"] for c in tts.calls] == ["First sentence.", "Third sentence."]
    assert [start for start, _e, _c in captured["chunks"]] == [0.0, 10.0]


def test_the_sentence_before_a_muted_one_inherits_its_room(tmp_path, monkeypatch):
    """Muting is filtered BEFORE the window maths, which is the whole reason it
    is done where it is: the room the muted sentence had becomes the previous
    one's, instead of a hole nothing can use."""
    seen = _windows(monkeypatch)
    _revoice(tmp_path, monkeypatch, _adjusted(**{"1": {"muted": True}}))

    # First runs until the THIRD sentence is due (10 s), not until the second (5 s).
    assert seen == [("First sentence.", 10.0), ("Third sentence.", 2.0)]


def test_the_last_unmuted_sentence_picks_up_the_video_tail(tmp_path, monkeypatch):
    """The "runs to the end of the video" rule belongs to whichever sentence is
    actually last, not to the last one in the transcript."""
    seen = _windows(monkeypatch)
    _revoice(tmp_path, monkeypatch, _adjusted(**{"2": {"muted": True}}), video_seconds=20.0)

    assert seen == [("First sentence.", 5.0), ("Second sentence.", 15.0)]


def test_an_offset_moves_where_a_sentence_is_pinned(tmp_path, monkeypatch):
    captured, _ = _revoice(tmp_path, monkeypatch, _adjusted(**{"1": {"offset": 1.5}}))

    assert [start for start, _e, _c in captured["chunks"]] == [0.0, 6.5, 10.0]


def test_a_negative_offset_past_zero_is_clamped_rather_than_left_to_luck(tmp_path, monkeypatch):
    """``assemble_master`` would floor it by accident (a non-positive gap is
    ignored). Clamped here so the window maths, the log and the UI agree."""
    captured, _ = _revoice(tmp_path, monkeypatch, _adjusted(**{"0": {"offset": -5.0}}))

    assert [start for start, _e, _c in captured["chunks"]] == [0.0, 5.0, 10.0]


def test_the_next_sentence_offset_changes_the_window_before_it(tmp_path, monkeypatch):
    """Without this the squeeze is measured against a moment nothing will be
    spoken at: moving a sentence later gives the one before it more room, and
    moving it earlier takes room away."""
    seen = _windows(monkeypatch)
    _revoice(tmp_path, monkeypatch, _adjusted(**{"1": {"offset": 1.5}}))

    assert seen == [
        ("First sentence.", 6.5),   # until the second is now due
        ("Second sentence.", 3.5),  # it starts later, so it has less room
        ("Third sentence.", 2.0),
    ]


def test_an_explicit_speed_is_used_verbatim_and_skips_the_per_sentence_rule(tmp_path, monkeypatch):
    """No automatic rate fitting: the user's number wins."""
    seen = _windows(monkeypatch)
    _, tts = _revoice(tmp_path, monkeypatch, _adjusted(**{"1": {"speed": 1.4}}), speed=1.0)

    assert [c["speed"] for c in tts.calls] == [1.0, 1.4, 1.0]
    assert [text for text, _d in seen] == ["First sentence.", "Third sentence."], (
        "the adjusted sentence never reaches _per_sentence_speed"
    )


def test_an_explicit_speed_also_skips_the_post_synthesis_squeeze(tmp_path, monkeypatch):
    """The user asked for that length; tempo-adjusting it afterwards would undo
    the one thing they asked for. The overrun is absorbed at the next pause."""
    monkeypatch.setattr(processing.VideoProcessor, "_per_sentence_speed", lambda self, *a, **k: 1.0)
    cmds = []
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda cmd, **k: cmds.append(cmd) or types.SimpleNamespace(returncode=0))

    # 4 s of speech for the closing sentence's 2 s slot: squeezed without an
    # explicit speed (test_a_sentence_borrows_the_pause_after_it... proves that).
    _revoice(tmp_path, monkeypatch, _adjusted(**{"2": {"speed": 0.8}}), clip_ms=4000)

    assert cmds == [], "a sentence with its own speed keeps the length it was asked for"


def test_a_per_sentence_voice_is_used_when_it_belongs_to_this_provider(tmp_path, monkeypatch):
    """Exactly the fallback the deck path relies on: an override from the other
    provider is dropped rather than failing the synthesis."""
    segments = _adjusted(**{
        "1": {"voice": "en-GB-RyanNeural", "provider": "edge_tts"},
        "2": {"voice": "af_heart", "provider": "kokoro"},  # stale: this run is Edge
    })
    _, tts = _revoice(tmp_path, monkeypatch, segments)

    assert [c["voice_id"] for c in tts.calls] == [
        "en-US-AriaNeural", "en-GB-RyanNeural", "en-US-AriaNeural",
    ]


def test_a_sentence_that_could_not_be_synthesised_is_counted_for_the_job(tmp_path, monkeypatch):
    """A failed sentence leaves a silent hole. It used to be visible only in the
    server log, so a whole line could vanish from the narration with nothing to
    show for it; the job reports the tally now."""
    _Seg.next_ms = 1500
    pm = _manager(tmp_path, GAPPY)
    proc = processing.VideoProcessor(voice_id="v", speed=1.0)

    tts = _FakeTTS()
    real = tts.generate_audio

    def _fail_second(text, voice_id, output_path, speed=1.0, **kwargs):
        if text == "Second sentence.":
            raise RuntimeError("voice unavailable")
        return real(text, voice_id, output_path, speed=speed, **kwargs)

    tts.generate_audio = _fail_second
    monkeypatch.setattr(proc, "_create_tts_generator", lambda: tts)
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: 15.0)

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: True)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", lambda path: 20.0)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    assert proc._revoice_video(pm, source, tmp_path / "out.mp4") is True
    assert proc.failed_sentences == 1


def test_a_generator_that_writes_nothing_counts_as_a_failure_too(tmp_path, monkeypatch):
    """The same silent hole by the other route: no exception, no file."""

    class _Silent:
        def generate_audio(self, text, voice_id, output_path, speed=1.0, **kwargs):
            if text != "First sentence.":
                return None  # nothing written
            Path(output_path).write_bytes(b"mp3")
            return True

    _Seg.next_ms = 1500
    pm = _manager(tmp_path, GAPPY)
    proc = processing.VideoProcessor(voice_id="v", speed=1.0)
    monkeypatch.setattr(proc, "_create_tts_generator", lambda: _Silent())
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: 15.0)

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: True)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", lambda path: 20.0)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    assert proc._revoice_video(pm, source, tmp_path / "out.mp4") is True
    assert proc.failed_sentences == 2


def test_a_clean_run_counts_no_failures(tmp_path, monkeypatch):
    captured, _ = _revoice(tmp_path, monkeypatch, GAPPY)
    assert captured["processor"].failed_sentences == 0


def _revoice_told(tmp_path, monkeypatch, video_duration, probe=None):
    """``_revoice_video`` told ``video_duration``, with the picture measuring
    ``probe``; returns the mux's kwargs.

    The real ``pad_seconds`` rule runs: only ``_probe_duration`` is stubbed, so
    what these tests pin is how the measurement and the caller's number combine
    - not a re-implementation of it.
    """
    pm = _manager(tmp_path, GAPPY)
    proc = processing.VideoProcessor(voice_id="en-US-AriaNeural", speed=1.0)
    monkeypatch.setattr(proc, "_create_tts_generator", lambda: _FakeTTS())
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: 15.0)

    muxed = {}

    def _mux(**kwargs):
        muxed.update(kwargs)
        return True

    def _probe(path):
        return probe

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", _mux)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", _probe)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    assert proc._revoice_video(
        pm, source, tmp_path / "out.mp4", video_duration=video_duration,
    ) is True
    return muxed


def test_the_measured_picture_wins_over_the_length_the_job_was_given(tmp_path, monkeypatch):
    """G1, at the engine's own seam: the job can only offer a length measured
    on the extracted AUDIO, so a picture that outlives its sound must not be
    cut down to it. The measurement wins; the caller's number may only raise
    it."""
    muxed = _revoice_told(tmp_path, monkeypatch, 6.014, probe=20.0)
    assert muxed["video_duration"] == 20.0, "20 s of picture, not 6 s of sound"

    muxed = _revoice_told(tmp_path, monkeypatch, 20.0, probe=6.0)
    assert muxed["video_duration"] == 20.0, "a longer belt still raises a short measurement"


@pytest.mark.parametrize("told", [None, 0, -3.0, "later"])
def test_without_a_usable_length_the_picture_alone_decides(tmp_path, monkeypatch, told):
    """A missing or nonsense length is not passed on as one: what the picture
    measures bounds the last sentence and pads the narration."""
    muxed = _revoice_told(tmp_path, monkeypatch, told, probe=14.0)
    assert muxed["video_duration"] == 14.0


def test_an_unmeasurable_picture_falls_back_to_what_the_job_knows(tmp_path, monkeypatch):
    """The belt: a file ffmpeg cannot measure still gets the job's length, so
    the pad is not lost for a format the probe cannot read."""
    muxed = _revoice_told(tmp_path, monkeypatch, 12.5, probe=None)
    assert muxed["video_duration"] == 12.5


def test_when_nothing_can_tell_the_mux_is_told_nothing(tmp_path, monkeypatch):
    """Neither measured nor known reaches the mux as None - which emits no pad
    at all - rather than as a zero that could be mistaken for a length."""
    muxed = _revoice_told(tmp_path, monkeypatch, None, probe=None)
    assert muxed["video_duration"] is None


def test_the_speaking_rate_is_never_calibrated_on_a_muted_sentence(tmp_path, monkeypatch):
    """The baseline is single-voice and feeds only ``_per_sentence_speed``; a
    sentence that is never spoken standing in for a third of the measurement
    would skew it for nothing."""
    silence = types.ModuleType("pydub.silence")
    silence.detect_leading_silence = lambda audio, **kwargs: 0
    monkeypatch.setitem(sys.modules, "pydub.silence", silence)

    long_enough = [
        {"start": 0.0, "end": 4.0, "text": "A first sentence with more than thirty characters in it."},
        {"start": 5.0, "end": 9.0, "text": "A second sentence with over thirty characters too.", "muted": True},
    ]
    tts = _FakeTTS()
    proc = processing.VideoProcessor(voice_id="en-US-AriaNeural", speed=1.0)
    proc._calibrate_tts_baseline(long_enough, tts, tmp_path)

    assert [c["text"] for c in tts.calls] == [long_enough[0]["text"]]
