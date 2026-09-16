"""The audition plan (narration timeline, phase 3a).

``GET /api/projects/{pid}/narration/plan`` answers what every sentence will be
spoken as, how fast and in whose voice - everything the timeline needs to play a
re-voice back in the browser without running one.

The owner re-voiced a 5m41s video six times in twelve minutes trying to fix
sentence timing, because the only way to hear a change was a full render. This
route is the half of the fix that stops that: the clips it points at are the
clips the render will use.

The division of labour is the point, and it is what these tests hold in place:

- **the server owns what each sentence says and how fast.** The render's rate
  rules - the per-sentence fitting over a sentence's window, the measured TTS
  baseline, the explicit-speed bypass - are answered from the very functions
  ``_revoice_video`` calls, never from a second implementation in TypeScript.
  ``test_the_plans_speeds_are_the_speeds_the_render_really_synthesises_at``
  runs a real re-voice beside a plan over the same transcript and compares
  them, so a change to either that does not move the other fails here.
- **the client owns only when each clip lands**, which needs the real decoded
  length of each clip and so cannot be answered here at all.
- **a preview_url carries the EFFECTIVE voice and speed**, which is what makes
  the audition byte-identical to the render rather than approximate: the clip
  the browser fetches is published to the cache entry the re-voice reuses.
"""

import threading
import time
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, narration, processing
from services import projects as store
from utils import helpers
from utils.config import config

STUDIO_EDGE_VOICE = "en-US-AriaNeural"   # utils.config.Config.edge_tts_voice's default
STUDIO_KOKORO_VOICE = "af_heart"         # ... and kokoro_voice's

# Three sentences with real pauses between them, as tests/test_revoice_sync.py
# uses: 2 s spoken, 3 s of silence, 2 s spoken, 3 s of silence, 2 s spoken.
GAPPY = [
    {"start": 0.0, "end": 2.0, "text": "First sentence."},
    {"start": 5.0, "end": 7.0, "text": "Second sentence."},
    {"start": 10.0, "end": 12.0, "text": "Third sentence."},
]

# A speaker rattling through far more words than the TTS baseline would fit in
# the same window, so the per-sentence rule actually raises the rate instead of
# every sentence coming back at the job's speed.
FAST = [
    {"start": 0.0, "end": 2.0, "text": "A very great many words indeed packed into a slot that is much too short for them."},
    {"start": 5.0, "end": 7.0, "text": "And a second sentence quite as crowded as the first one was, with no room to breathe."},
]


class FakeTTS:
    """Records what it was asked to say, and writes something at the path it is
    handed (both real providers write in place)."""

    AUDIO = b"ID3\x03fake-mp3-bytes"

    def __init__(self):
        self.calls: list[dict] = []

    def generate_audio(self, text, voice_id, output_path=None, speed=1.0, use_cache=True, **_ignored):
        self.calls.append({"text": text, "voice_id": voice_id, "speed": speed})
        from pathlib import Path

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.AUDIO)
        return path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(helpers, "CACHE_DIR", tmp_path / "cache")
    # The measured speaking rates are memoised across requests; no test may
    # inherit another's.
    monkeypatch.setattr(narration, "_BASELINE_CACHE", {})


@pytest.fixture(autouse=True)
def baseline(monkeypatch):
    """The measured speaking rate, stubbed at the engine's own default.

    The real measurement synthesises three sentences and decodes them with
    pydub; the tests that care about what happens when it cannot run stub it
    themselves. Everything else wants a known number, and 15.0 is the number a
    render with no usable samples uses too.
    """
    calls: list[dict] = []

    def _calibrate(segments, tts_gen, tmp_dir, *, provider, voice_id, progress=None, file_label=""):
        calls.append({"provider": provider, "voice_id": voice_id,
                      "texts": [s.get("text") for s in segments]})
        return processing.DEFAULT_BASELINE_RATE

    monkeypatch.setattr(processing, "calibrate_tts_baseline", _calibrate)
    return calls


@pytest.fixture
def tts(monkeypatch):
    fake = FakeTTS()
    monkeypatch.setattr("core.tts_provider.get_tts_provider", lambda provider_id=None: fake)
    return fake


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _video(segments=None, *, audio_seconds: float | None = None) -> str:
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [dict(s) for s in (GAPPY if segments is None else segments)])
    if audio_seconds is not None:
        # The file the transcribe step leaves beside the video, at the length the
        # timeline will be drawn against.
        path = store.PROJECTS_DIR / pid / "audio.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(1000)
            wf.writeframes(np.zeros(int(audio_seconds * 1000), dtype="<i2").tobytes())
    return pid


def _plan(client: TestClient, pid: str, **params):
    return client.get(f"/api/projects/{pid}/narration/plan", params=params or None)


# ── the payload ──────────────────────────────────────────────────────────────

def test_the_plan_says_what_every_sentence_is_and_where_it_is_aimed(client):
    pid = _video(audio_seconds=12.0)

    r = _plan(client, pid)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["duration"] == pytest.approx(12.0)
    assert payload["baseline_rate"] == pytest.approx(15.0)
    # The render's own squeeze constants, sent rather than written into the
    # client as literals - see test_the_squeeze_numbers_are_the_render_s.
    assert payload["squeeze_tolerance"] == processing.SQUEEZE_TOLERANCE
    assert payload["squeeze_max_factor"] == processing.SQUEEZE_MAX_FACTOR
    assert [s["index"] for s in payload["sentences"]] == [0, 1, 2]
    first = payload["sentences"][0]
    assert set(first) == {
        "index", "text", "start", "end", "pinned_start", "muted", "speakable", "past_end",
        "window", "squeezable", "speed", "voice", "preview_url",
    }
    assert first["text"] == "First sentence."
    assert (first["start"], first["end"]) == (0.0, 2.0)
    assert first["pinned_start"] == 0.0
    assert first["muted"] is False
    assert first["speakable"] is True and first["past_end"] is False
    assert first["window"] == pytest.approx(5.0)
    assert first["squeezable"] is True
    assert first["voice"] == STUDIO_EDGE_VOICE
    assert first["speed"] == 1.0


def test_the_blocks_keep_the_original_speech_window_and_the_pin_moves_with_the_offset(client):
    """``start``/``end`` are the moment the sentence was SPOKEN - that is what
    lines up with the burst under it in the waveform - while ``pinned_start`` is
    where the render will put it. Both, because a ghost outline at the original
    position is how "what I moved" is visible without a second panel."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -1.5}).status_code == 200

    sentences = _plan(client, pid).json()["sentences"]
    assert (sentences[1]["start"], sentences[1]["end"]) == (5.0, 7.0), "the block keeps its own width"
    assert sentences[1]["pinned_start"] == pytest.approx(3.5)


def test_a_pin_pulled_before_zero_is_floored_rather_than_going_negative(client):
    """``assemble_master`` floors a negative pin by accident (a non-positive gap
    inserts no silence); the plan says so explicitly, so the timeline and the
    render agree about where the block is."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -99.0}).status_code == 200

    assert _plan(client, pid).json()["sentences"][1]["pinned_start"] == 0.0


def test_the_length_is_the_audios_own_and_not_the_records(client):
    """Trap 5: never ffprobe (it may not exist in the packaged app) and not the
    record's duration when the audio can speak for itself - the strip and the
    blocks over it must share the scale of the file being drawn."""
    pid = _video(audio_seconds=13.75)
    record = store.get_project(pid)
    record["duration"] = 999.0
    store.save_project(record)

    assert _plan(client, pid).json()["duration"] == pytest.approx(13.75)


def test_without_extracted_audio_the_length_falls_back_rather_than_failing(client):
    """A transcript with no ``audio.wav`` beside it (restored from a backup, or
    the file removed by hand) still has a timeline worth drawing."""
    pid = _video()
    record = store.get_project(pid)
    record["duration"] = 42.0
    store.save_project(record)

    assert _plan(client, pid).json()["duration"] == pytest.approx(42.0)


# ── the speeds are the render's, not a second implementation of them ─────────

def test_a_sentence_spoken_faster_than_the_baseline_is_sped_up_to_fit_its_window(client):
    """The per-sentence rule, reported before anything is rendered. 82 characters
    due in 5 s is 16.4 chars/s against a 15 chars/s baseline, so the voice is
    asked for 1.09 - and the SECOND sentence, 85 characters with only its own 2 s
    slot left, is past the rule's +30% ceiling and reports exactly that."""
    pid = _video(FAST, audio_seconds=7.0)

    sentences = _plan(client, pid).json()["sentences"]
    assert sentences[0]["speed"] == pytest.approx(1.09)
    assert sentences[0]["speed"] == processing.per_sentence_speed(FAST[0]["text"], 5.0, 15.0, 1.0)
    assert sentences[1]["speed"] == 1.3, "+30% and no more - the ceiling the rule has always had"


def test_an_unhurried_sentence_is_left_at_the_jobs_own_speed(client):
    """Never slower than the user's speed: fitting text DOWN into a window drags
    the voice to 0.5x and it sounds drugged. The spare time becomes silence."""
    pid = _video(audio_seconds=12.0)

    assert [s["speed"] for s in _plan(client, pid).json()["sentences"]] == [1.0, 1.0, 1.0]
    assert [s["speed"] for s in _plan(client, pid, speed=1.2).json()["sentences"]] == [1.2, 1.2, 1.2]


def test_a_sentence_with_its_own_speed_bypasses_the_rule_entirely(client):
    """"Nothing guesses a rate" made concrete: the user's number wins, in the
    render and therefore in the plan."""
    pid = _video(FAST, audio_seconds=7.0)
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"speed": 0.9}).status_code == 200

    sentences = _plan(client, pid).json()["sentences"]
    assert sentences[0]["speed"] == 0.9, "not the 1.3 the fitting rule would have chosen"
    assert sentences[1]["speed"] == 1.3


def test_the_plans_speeds_are_the_speeds_the_render_really_synthesises_at(client, tmp_path, monkeypatch):
    """The anti-drift pin, and the reason the plan is a server route at all.

    A real ``_revoice_video`` runs beside a real plan over the SAME transcript,
    with the same baseline and the same job narration, and the speeds it asks
    the provider for must be the speeds the plan advertised. Reimplementing the
    window maths in TypeScript would have passed every other test in this file
    and failed this one the first time either side changed.
    """
    import sys
    import types

    from core.project_manager import ProjectManager

    segments = [
        {"start": 0.0, "end": 2.0, "text": FAST[0]["text"]},
        {"start": 5.0, "end": 7.0, "text": "A short one."},
        {"start": 8.0, "end": 9.0, "text": FAST[1]["text"], "speed": 1.45},
        {"start": 12.0, "end": 14.0, "text": "Muted, so the one before it inherits its room.", "muted": True},
        {"start": 20.0, "end": 24.0, "text": "The last sentence, which runs to the end of the video."},
    ]
    pid = _video(segments, audio_seconds=30.0)
    plan = _plan(client, pid, speed=1.1).json()

    # --- and now the render, with only the media engine stubbed out ----------
    class _Seg:
        def __init__(self, ms=0):
            self.ms = ms

        @classmethod
        def silent(cls, duration=0):
            return cls(int(duration))

        @classmethod
        def from_file(cls, path):
            return cls(500)

        def __add__(self, other):
            return _Seg(self.ms + other.ms)

        def __len__(self):
            return self.ms

        def __getitem__(self, item):
            return _Seg(self.ms)

        def export(self, path, **kwargs):
            from pathlib import Path

            Path(path).write_bytes(b"MASTER")

    fake_pydub = types.ModuleType("pydub")
    fake_pydub.AudioSegment = _Seg
    monkeypatch.setitem(sys.modules, "pydub", fake_pydub)

    def _scratch(prefix=""):
        d = tmp_path / f"scratch_{prefix or 'x'}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(processing, "_job_scratch", _scratch)

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"video")
    pm = ProjectManager(tmp_path / "proj")
    pm.create_project(pptx_path=source, slide_notes=[" ".join(s["text"] for s in segments)], voice_id="v")
    slide0 = pm.state.slides[0]
    slide0.original_segments = [dict(s) for s in segments]
    # The SAME helper ``services.revoice`` uses to write these two - see
    # test_the_section_is_built_once_for_the_render_and_the_plan below.
    section = narration.transcript_section(segments)
    slide0.original_start_time = section["start"]
    slide0.original_end_time = section["end"]
    pm.state.source_video_path = str(source)

    rendered = FakeTTS()
    proc = processing.VideoProcessor(voice_id=STUDIO_EDGE_VOICE, speed=1.1)
    monkeypatch.setattr(proc, "_create_tts_generator", lambda: rendered)
    monkeypatch.setattr(proc, "_calibrate_tts_baseline", lambda *a, **k: processing.DEFAULT_BASELINE_RATE)

    import core.video_creator as vc
    monkeypatch.setattr(vc, "replace_video_audio", lambda **kw: True)
    monkeypatch.setattr(vc, "trim_leading_silence_segment", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_level_opening", lambda clip, profile=None: clip)
    monkeypatch.setattr(vc, "_probe_duration", lambda path: 30.0)

    assert proc._revoice_video(pm, source, tmp_path / "out.mp4") is True

    # What the render actually asked the provider to say, and how fast.
    spoken = [s for s in plan["sentences"] if not s["muted"]]
    assert [c["text"] for c in rendered.calls] == [s["text"] for s in spoken]
    assert [c["speed"] for c in rendered.calls] == [s["speed"] for s in spoken], (
        "the plan advertised a speed the render did not use - the timeline would "
        "be auditioning clips the re-voice will not reuse"
    )
    assert [c["voice_id"] for c in rendered.calls] == [s["voice"] for s in spoken]


def test_the_section_is_built_once_for_the_render_and_the_plan():
    """The last sentence's window is bounded by the SECTION's end whenever the
    video's own duration is not known - ``_probe_duration`` answers nothing
    without ffprobe, which the packaged app may not have. The re-voice writes
    that section onto the engine's slide and the plan measures against it, so it
    is spelled once and both take it from there."""
    import inspect

    from services import revoice

    segments = [
        {"start": 1.5, "end": 4.0, "text": "First."},
        {"start": 9.0, "end": 24.0, "text": "Last."},
    ]
    assert narration.transcript_section(segments) == {"start": 1.5, "end": 24.0}
    assert narration.transcript_section([]) == {"start": 0.0, "end": 0.0}
    # A malformed timestamp falls back rather than failing the whole re-voice.
    assert narration.transcript_section([{"start": None, "end": "x"}]) == {"start": 0.0, "end": 0.0}

    source = inspect.getsource(revoice.revoice_project)
    assert "transcript_section" in source, (
        "services/revoice.py has gone back to spelling the section out itself; the "
        "audition plan would then be free to measure the last sentence against a "
        "different bound than the render"
    )


# ── the post-synthesis squeeze: the client's to model, the server's to define ─

def test_the_squeeze_numbers_are_the_render_s_own_and_are_sent_not_guessed(client):
    """The render tempo-squeezes a clip that runs more than 15% past the moment
    the next sentence is due, unless it would take more than 2x to do it. The
    timeline HAS to model that - it is what stops a long clip cascading into
    every sentence after it - and modelling it means having the two numbers. Sent
    in the payload, so the client cannot hold a second copy that drifts from the
    loop it is predicting."""
    import inspect

    pid = _video(audio_seconds=12.0)
    payload = _plan(client, pid).json()
    assert (payload["squeeze_tolerance"], payload["squeeze_max_factor"]) == (1.15, 2.0)

    # ... and the render really uses these constants rather than its old literals.
    source = inspect.getsource(processing.VideoProcessor._revoice_video)
    assert "SQUEEZE_TOLERANCE" in source and "SQUEEZE_MAX_FACTOR" in source
    assert "* 1.15" not in source and "<= 2.0" not in source


def test_each_sentence_carries_the_window_the_render_measured_for_it(client):
    """The room to the next sentence's pin - what the squeeze fits a clip INTO,
    and what the per-sentence speed was computed against."""
    pid = _video(audio_seconds=12.0)

    windows = [s["window"] for s in _plan(client, pid).json()["sentences"]]
    assert windows == [pytest.approx(5.0), pytest.approx(5.0), pytest.approx(2.0)]


def test_a_sentence_with_its_own_speed_is_never_squeezed(client):
    """Both of the render's guards travel with the plan. An explicit speed
    bypasses the squeeze as well as the fitting rule - the user named the rate,
    and tempo-adjusting it afterwards would undo the one thing they asked for -
    so the timeline must play that clip at its real length however long it is."""
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"speed": 1.2}).status_code == 200

    squeezable = [s["squeezable"] for s in _plan(client, pid).json()["sentences"]]
    assert squeezable == [True, False, True]


def test_a_sentence_that_is_not_spoken_is_never_squeezable(client):
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": True}).status_code == 200

    sentences = _plan(client, pid).json()["sentences"]
    assert (sentences[0]["squeezable"], sentences[0]["window"]) == (False, 0.0)


# ── what the render will actually speak ──────────────────────────────────────

def test_the_server_says_which_sentences_are_speakable_rather_than_the_client(client):
    """``str.strip()`` and ``String.trim()`` are NOT the same character set -
    U+001C to U+001F strip but do not trim, U+FEFF trims but does not strip - so
    a client deciding "has words" for itself would audition a sentence the
    preview route then refuses with a 400, surfacing as a per-sentence failure
    with no visible cause. One answer, from the side that owns the rule."""
    pid = _video([
        {"start": 0.0, "end": 2.0, "text": "A real sentence."},
        {"start": 3.0, "end": 4.0, "text": ""},   # strips to nothing; would NOT trim to nothing
        {"start": 5.0, "end": 6.0, "text": "   "},
        {"start": 7.0, "end": 9.0, "text": "Another real one."},
    ], audio_seconds=12.0)

    sentences = _plan(client, pid).json()["sentences"]
    assert [s["speakable"] for s in sentences] == [True, False, False, True]
    # And the route agrees with the plan about the one the browser would have
    # tried to play.
    assert client.get(f"/api/projects/{pid}/transcript/1/preview").status_code == 400


def test_a_sentence_pinned_past_the_end_of_the_video_is_marked_and_not_auditioned(client):
    """The render drops it: ``replace_video_audio`` muxes with ``-shortest``, so
    a sentence pinned at or past the video's end is not in the finished file at
    all, and phase 1 counts it a failed sentence. The plan holds both numbers,
    so it must say so - auditioning it would play the owner audio the re-voice
    will never produce, which is the exact opposite of the point."""
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/2", json={"offset": 5.0}).status_code == 200

    sentences = _plan(client, pid).json()["sentences"]
    assert sentences[2]["pinned_start"] == pytest.approx(15.0)
    assert sentences[2]["speakable"] is False
    assert sentences[2]["past_end"] is True
    assert sentences[2]["muted"] is False, "it is not muted - it is off the end"
    # The sentence before it still has its room; only the dropped one changes.
    assert sentences[1]["speakable"] is True


def test_past_end_is_only_for_a_sentence_that_would_otherwise_be_spoken(client):
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": True}).status_code == 200

    first = _plan(client, pid).json()["sentences"][0]
    assert (first["speakable"], first["past_end"]) == (False, False)


def test_a_segment_that_is_not_even_a_dictionary_is_skipped_rather_than_a_500(client):
    """A hand-edited project.json can hold anything, and the two passes over the
    transcript must agree about it."""
    pid = _video()
    record = store.get_project(pid)
    record["transcript"] = [{"start": 0.0, "end": 2.0, "text": "Fine."}, "not a segment"]
    store.save_project(record)

    r = _plan(client, pid)
    assert r.status_code == 200, r.text
    assert [s["speakable"] for s in r.json()["sentences"]] == [True, False]


# ── muting ───────────────────────────────────────────────────────────────────

def test_a_muted_sentence_is_still_drawn_but_gives_its_room_to_the_one_before(client):
    """The render filters muted sentences out BEFORE any window maths, which is
    what lets the sentence before one inherit its room. The plan returns them
    anyway, so the timeline can draw them greyed rather than leaving a hole the
    user cannot unmute."""
    pid = _video(FAST + [{"start": 10.0, "end": 12.0, "text": "A third, crowded sentence with plenty of words in it."}],
                 audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"muted": True}).status_code == 200

    sentences = _plan(client, pid).json()["sentences"]
    assert [s["muted"] for s in sentences] == [False, True, False]
    # The first sentence is now measured against the THIRD's pin, 10 s away, not
    # against the muted one's 5 s.
    assert sentences[0]["speed"] == processing.per_sentence_speed(FAST[0]["text"], 10.0, 15.0, 1.0)
    assert sentences[0]["speed"] == 1.0, (
        "with twice the room it needs no speeding up at all; it would be 1.09 "
        "measured against the muted sentence's pin"
    )


def test_a_muted_sentence_reports_the_jobs_speed_since_nothing_is_synthesised_for_it(client):
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": True}).status_code == 200

    muted = _plan(client, pid, speed=1.25).json()["sentences"][0]
    assert muted["muted"] is True and muted["speed"] == 1.25


def test_a_sentence_with_no_words_takes_no_part_in_the_windows(client):
    """The text editor can save an empty line; the render skips it exactly as it
    skips a muted one."""
    pid = _video([
        {"start": 0.0, "end": 2.0, "text": FAST[0]["text"]},
        {"start": 5.0, "end": 7.0, "text": "   "},
        {"start": 10.0, "end": 12.0, "text": "Another sentence."},
    ], audio_seconds=12.0)

    sentences = _plan(client, pid).json()["sentences"]
    assert sentences[0]["speed"] == processing.per_sentence_speed(FAST[0]["text"], 10.0, 15.0, 1.0)
    assert sentences[1]["text"] == "   "


# ── voices ───────────────────────────────────────────────────────────────────

def test_each_sentence_reports_the_voice_the_render_will_use(client):
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"voice": "en-GB-RyanNeural"}).status_code == 200

    voices = [s["voice"] for s in _plan(client, pid, voice="en-US-JennyNeural").json()["sentences"]]
    assert voices == ["en-US-JennyNeural", "en-GB-RyanNeural", "en-US-JennyNeural"]


def test_a_stored_voice_from_the_other_provider_falls_back_as_the_render_would(client):
    """``effective_voice`` is the rule the slide editor already relies on, and
    the plan has to follow it: telling the timeline a sentence will be spoken in
    an Edge voice while the Kokoro render drops it would be the plan lying."""
    pid = _video(audio_seconds=12.0)
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"voice": "en-GB-RyanNeural"}).status_code == 200

    voices = [s["voice"] for s in _plan(client, pid, provider="kokoro").json()["sentences"]]
    assert voices == [STUDIO_KOKORO_VOICE] * 3


# ── the preview_url: what makes the audition exact rather than approximate ───

def test_the_preview_url_carries_the_effective_voice_and_speed(client):
    """Not the job's - the sentence's own, after the render's rules have been
    applied. That is what makes the clip the browser fetches the clip the render
    will reuse, for every sentence rather than only for one carrying an explicit
    speed."""
    pid = _video(FAST, audio_seconds=7.0)
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"voice": "en-GB-RyanNeural"}).status_code == 200

    sentences = _plan(client, pid).json()["sentences"]
    assert sentences[0]["preview_url"] == (
        f"/api/projects/{pid}/transcript/0/preview"
        f"?provider=edge_tts&voice=en-US-AriaNeural&speed=1.09"
    )
    assert "voice=en-GB-RyanNeural" in sentences[1]["preview_url"]


def test_following_a_preview_url_publishes_the_entry_the_render_will_reuse(client, tts):
    """End to end: the plan's URL is fetched, and what comes back is sitting at
    the very cache key the re-voice looks up for that sentence at that speed in
    that voice. This is "what you hear is what you get", tested rather than
    asserted in a docstring."""
    pid = _video(FAST, audio_seconds=7.0)
    first = _plan(client, pid).json()["sentences"][0]

    r = client.get(first["preview_url"])
    assert r.status_code == 200, r.text
    assert r.content == FakeTTS.AUDIO
    assert tts.calls == [{"text": FAST[0]["text"], "voice_id": STUDIO_EDGE_VOICE, "speed": 1.09}]

    entry = narration.cache_path_for("edge_tts", FAST[0]["text"], STUDIO_EDGE_VOICE, 1.09)
    assert entry.read_bytes() == FakeTTS.AUDIO, "the render will find this clip already made"


# ── the baseline measurement ─────────────────────────────────────────────────

def test_the_baseline_is_measured_at_the_jobs_voice_over_the_whole_transcript(client, baseline):
    pid = _video(audio_seconds=12.0)

    assert _plan(client, pid, voice="en-US-JennyNeural").status_code == 200
    assert len(baseline) == 1
    assert baseline[0]["provider"] == "edge_tts"
    assert baseline[0]["voice_id"] == "en-US-JennyNeural"
    assert baseline[0]["texts"] == [s["text"] for s in GAPPY], (
        "the whole transcript is handed over; the measurement filters the muted "
        "sentences out itself"
    )


def test_a_provider_that_cannot_be_reached_falls_back_instead_of_failing(client, monkeypatch):
    """A timeline is still worth drawing without a measured speaking rate, and
    the sentences carrying an explicit speed do not use it at all."""
    monkeypatch.setattr(processing, "calibrate_tts_baseline",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no provider")))
    pid = _video(audio_seconds=12.0)

    r = _plan(client, pid)
    assert r.status_code == 200, r.text
    assert r.json()["baseline_rate"] == pytest.approx(processing.DEFAULT_BASELINE_RATE)


def test_kokoro_without_its_model_is_not_made_to_download_340_mb(client, monkeypatch, baseline):
    """Asking for a timeline must not start a 340 MB download. The model is
    probed with the stat-only ``kokoro_model_present`` and the plan falls back to
    the default rate - which is what a render with an unmeasurable baseline
    would use anyway."""
    monkeypatch.setattr("core.kokoro_tts_generator.kokoro_model_present", lambda: False)
    pid = _video(audio_seconds=12.0)

    r = _plan(client, pid, provider="kokoro")
    assert r.status_code == 200, r.text
    assert r.json()["baseline_rate"] == pytest.approx(processing.DEFAULT_BASELINE_RATE)
    assert baseline == [], "nothing was synthesised, so nothing was downloaded"


def test_the_measurement_is_paid_once_and_memoised_per_project_provider_and_voice(client, baseline):
    """The plan is re-fetched on every tab open past react-query's staleTime and
    on every change to the Re-voice card. Three identical GETs used to mean
    three calibrations - three syntheses each."""
    pid = _video(audio_seconds=12.0)

    for _ in range(3):
        assert _plan(client, pid).status_code == 200
    assert len(baseline) == 1, "the second and third requests reused the measurement"

    # A different voice is a different speaking rate, so that one is measured.
    assert _plan(client, pid, voice="en-US-JennyNeural").status_code == 200
    assert len(baseline) == 2
    assert _plan(client, pid, voice="en-US-JennyNeural").status_code == 200
    assert len(baseline) == 2


def test_changing_the_transcript_drops_the_memoised_rate(client, baseline):
    """It is measured from three of the sentences, and it skips the muted ones -
    so editing the words or muting one makes it no longer describe this
    transcript. Re-measuring is nearly free: the samples are cached on
    (text, voice, speed) like every other synthesis."""
    pid = _video(audio_seconds=12.0)
    assert _plan(client, pid).status_code == 200
    assert len(baseline) == 1

    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": True}).status_code == 200
    assert _plan(client, pid).status_code == 200
    assert len(baseline) == 2, "a mute changes which sentences are sampled"

    assert client.patch(
        f"/api/projects/{pid}/transcript",
        json={"transcript": [{"start": s["start"], "end": s["end"], "text": "Reworded."} for s in GAPPY]},
    ).status_code == 200
    assert _plan(client, pid).status_code == 200
    assert len(baseline) == 3, "a Save changes the words that were measured"


def test_deleting_the_project_drops_its_memoised_rates(client, baseline):
    pid = _video(audio_seconds=12.0)
    assert _plan(client, pid).status_code == 200
    assert any(key[0] == pid for key in narration._BASELINE_CACHE)

    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert not any(key[0] == pid for key in narration._BASELINE_CACHE)


def test_a_provider_that_hangs_is_waited_on_for_a_bounded_time(client, monkeypatch):
    """The measurement is THREE syntheses, ``generate_audio`` has no timeout
    argument and Edge's own ceiling is 120 s - so left alone this holds a server
    thread for six minutes and then answers 200 with the default rate in it,
    behind a page saying "Reading the narration…" with no way out. The measuring
    thread is a daemon and is left to finish into the shared cache; only the
    waiting stops."""
    started = threading.Event()

    def _slow(*a, **k):
        started.set()
        time.sleep(5)
        return 99.0

    monkeypatch.setattr(processing, "calibrate_tts_baseline", _slow)
    monkeypatch.setattr(narration, "BASELINE_TIMEOUT_SECONDS", 0.1)
    pid = _video(audio_seconds=12.0)

    at = time.monotonic()
    r = _plan(client, pid)
    assert r.status_code == 200, r.text
    assert time.monotonic() - at < 4, "the request waited for the provider's own ceiling"
    assert r.json()["baseline_rate"] == pytest.approx(processing.DEFAULT_BASELINE_RATE)
    assert started.is_set()


def test_a_fallback_rate_is_not_memoised(client, monkeypatch):
    """The provider may be reachable again by the next press; a 15.0 cached
    because it was unreachable once would last until the transcript changed."""
    monkeypatch.setattr(processing, "calibrate_tts_baseline",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no provider")))
    pid = _video(audio_seconds=12.0)
    assert _plan(client, pid).json()["baseline_rate"] == pytest.approx(15.0)
    assert not any(key[0] == pid for key in narration._BASELINE_CACHE)

    monkeypatch.setattr(processing, "calibrate_tts_baseline", lambda *a, **k: 21.5)
    assert _plan(client, pid).json()["baseline_rate"] == pytest.approx(21.5)


def test_the_measurement_is_skipped_when_there_is_nothing_to_speak(client, baseline):
    pid = _video([{"start": 0.0, "end": 2.0, "text": "Nothing to say."}])
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": True}).status_code == 200

    assert _plan(client, pid).status_code == 200
    assert baseline == []


# ── the answers ──────────────────────────────────────────────────────────────

def test_a_deck_has_no_narration_to_plan(client):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    r = _plan(client, deck)
    assert r.status_code == 400 and "Only video projects" in r.json()["detail"]


def test_an_unknown_provider_is_refused(client):
    pid = _video()
    r = _plan(client, pid, provider="elevenlabs")
    assert r.status_code == 400 and "Unknown narration provider" in r.json()["detail"]


def test_a_voice_from_the_other_provider_is_refused(client):
    """Asked for HERE it is a mistake worth naming - the same answer generate,
    re-voice and the preview give. Only a STORED one falls back silently."""
    pid = _video()
    r = _plan(client, pid, provider="kokoro", voice="en-US-AriaNeural")
    assert r.status_code == 400 and "looks like an Edge TTS voice" in r.json()["detail"]


def test_a_speed_outside_the_stored_bounds_is_refused(client):
    pid = _video()
    for bad in (0.4, 2.1):
        assert _plan(client, pid, speed=bad).status_code == 422, bad


def test_an_untranscribed_video_has_nothing_to_plan(client):
    pid = store.import_upload("other.mp4", b"video-bytes")["id"]
    r = _plan(client, pid)
    assert r.status_code == 404 and "transcribe it first" in r.json()["detail"]


def test_a_missing_project_is_a_404(client):
    assert _plan(client, "aabbccddeeff").status_code == 404


# ── the shape of the route ───────────────────────────────────────────────────

def test_the_plan_is_a_read_and_is_allowed_while_a_job_holds_the_project(client, monkeypatch):
    """It writes nothing, so refusing an audition while a re-voice runs would be
    a 409 on a read. Every WRITE of this record still takes ``require_idle``."""
    pid = _video(audio_seconds=12.0)
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "revoice", "status": "running"} if project_id == pid else None,
    )

    assert _plan(client, pid).status_code == 200
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 1.0}).status_code == 409
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before
    assert jobs.active_for(pid)["kind"] == "revoice"


def test_the_plan_is_a_get_so_the_audit_guard_need_not_name_it(routes):
    methods = {r.method for r in routes if r.path.endswith("/narration/plan")}
    assert methods == {"GET"}, methods
