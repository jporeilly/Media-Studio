"""The edit timeline: E1 the model and the render, E3 one list per track.

A project gains ``record["edit"] = {"version": 2, "video": {"keep": [[start,
end], ...]}, "narration": {"keep": [...]}}`` - the ranges of its ONE source
that survive the cut on each track, in source seconds - and nothing else; a
version-1 record (one ``keep``) is read as both tracks cut together. **The
transcript never moves**: ``set_transcript`` identifies a sentence by its
window, so shifting sentences after a cut would drop every adjustment on every
later sentence. The edit is applied as a PROJECTION
(``services.edit.project_transcript``) at plan time and at render time, by the
same function, so the two cannot disagree - through the NARRATION list, while
the VIDEO list cuts the picture (two lists, one axis: spec §11, trap 18).

Four things these tests hold in place:

- the arithmetic (``validate_keep``, ``to_timeline`` / ``to_source``,
  ``project_transcript``, ``whole_source``) is exhaustively pinned, because a
  client copy of it lands in E2 and shares these fixtures;
- the three routes take the store's lock, refuse a busy project, audit with
  counts and never times, and leave the transcript byte-identical;
- the audition plan returns the projected sentences - in timeline seconds,
  each still addressed by its index in the STORED transcript - and says which
  edit it projected them through;
- a locked track is untouched (trap 19): a video-only edit hands the engine
  the very transcript objects an unedited project does, and a narration-only
  edit leaves the picture whole.
"""

import json
import threading
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import edit, jobs, music, narration, processing
from services import projects as store
from utils import helpers
from utils.config import config

# Five sentences over a 12 s recording, and a cut that removes 6.0-7.5:
# the third sentence starts inside the hole and is cut with the picture it was
# spoken over; the second runs into the hole and is clamped at its edge.
SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "First sentence."},
    {"start": 5.0, "end": 7.0, "text": "Second sentence."},
    {"start": 6.5, "end": 7.4, "text": "Cut away."},
    {"start": 8.0, "end": 9.0, "text": "Fourth sentence."},
    {"start": 10.0, "end": 12.0, "text": "Fifth sentence."},
]
KEEP = [[0.0, 6.0], [7.5, 12.0]]  # output: 10.5 s

# The cases above, in one file the client's copy of the projection reads too.
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "edit_projection.json"

FAST = "A very great many words indeed packed into a slot that is much too short for them."


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(helpers, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(narration, "_BASELINE_CACHE", {})
    # The music library's directory, so a clip list is checked against an
    # empty (or a test's own) library and never the repo's assets/music.
    monkeypatch.setattr(music, "MUSIC_DIR", tmp_path / "music")


@pytest.fixture(autouse=True)
def baseline(monkeypatch):
    """The measured speaking rate, stubbed at the engine's default; the calls
    are counted so a test can see when the memo was dropped."""
    calls: list[dict] = []

    def _calibrate(segments, tts_gen, tmp_dir, *, provider, voice_id, progress=None, file_label=""):
        calls.append({"texts": [s.get("text") for s in segments]})
        return processing.DEFAULT_BASELINE_RATE

    monkeypatch.setattr(processing, "calibrate_tts_baseline", _calibrate)
    return calls


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _wav(pid: str, seconds: float) -> None:
    """The file the transcribe step leaves beside the video, at the length the
    edit is measured against (1 kHz so the header arithmetic is exact)."""
    path = store.PROJECTS_DIR / pid / "audio.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(1000)
        wf.writeframes(np.zeros(int(round(seconds * 1000)), dtype="<i2").tobytes())


def _video(segments=None, *, audio_seconds: float | None = 12.0) -> str:
    pid = store.import_upload("clip.mp4", b"video-bytes")["id"]
    store.set_transcript(pid, [dict(s) for s in (SEGMENTS if segments is None else segments)])
    if audio_seconds is not None:
        _wav(pid, audio_seconds)
    return pid


def _get(client, pid):
    return client.get(f"/api/projects/{pid}/edit")


def _put(client, pid, keep):
    """The version-1 body: one list, meaning both tracks."""
    return client.put(f"/api/projects/{pid}/edit", json={"keep": keep})


def _put_tracks(client, pid, **tracks):
    """The version-2 body: ``video=`` and / or ``narration=``."""
    return client.put(f"/api/projects/{pid}/edit", json=tracks)


def _payload(video, narration, source=12.0, music=()):
    """The version-2 answer the routes and the plan carry, for a source of
    ``source`` seconds: a whole track reports the source's length; ``music``
    is the clips as read back (with ``file_duration`` and ``missing``)."""
    def track(keep):
        return {"keep": keep, "output_duration": edit.output_duration(keep) if keep else source}
    picture = track(video)
    return {"version": 2, "video": picture, "narration": track(narration), "music": list(music),
            "source_duration": source, "output_duration": picture["output_duration"]}


def _plan(client, pid, **params):
    r = client.get(f"/api/projects/{pid}/narration/plan", params=params or None)
    assert r.status_code == 200, r.text
    return r.json()


def _edit_rows() -> list[dict]:
    return [r for r in auth_store.list_audit(limit=50) if r["action"] == "project.edit"]


# ── validate_keep ─────────────────────────────────────────────────────────────

def test_valid_ranges_come_back_as_rounded_lists_in_order():
    assert edit.validate_keep([(0, 47.3), [52.3, 341.008]], 341.008) == [[0.0, 47.3], [52.3, 341.008]]
    # Rounded to the transcript's own three decimals BEFORE the checks, so a
    # value a rounded display would send back is accepted...
    assert edit.validate_keep([[0.00049, 5.99951]], 12.0) == [[0.0, 6.0]]
    # ... including a hair under zero, which is zero (and a plain zero, not -0.0).
    checked = edit.validate_keep([[-0.0004, 1.0]], 12.0)
    assert checked == [[0.0, 1.0]] and str(checked[0][0]) == "0.0"
    # And the source's own length is rounded the same way before it bounds them.
    assert edit.validate_keep([[0.0, 341.008]], 341.00825) == [[0.0, 341.008]]


def test_touching_ranges_are_a_split_and_are_allowed():
    """Two contiguous ranges remove nothing; they are how a split is stored,
    and the timeline draws the boundary."""
    assert edit.validate_keep([[0, 6], [6, 12]], 12.0) == [[0.0, 6.0], [6.0, 12.0]]


@pytest.mark.parametrize("keep, message", [
    ([], "Keep at least one range"),
    ("nope", "must be a list"),
    ({"start": 0, "end": 1}, "must be a list"),
    ([[0.0]], "Range 1 must be a [start, end] pair"),
    ([[0.0, 1.0, 2.0]], "Range 1 must be a [start, end] pair"),
    (["ab"], "Range 1 must be a [start, end] pair"),
    ([[0.0, 1.0], 7], "Range 2 must be a [start, end] pair"),
    ([[True, 1.0]], "Range 1 must be a [start, end] pair of seconds"),
    ([["0", 1.0]], "Range 1 must be a [start, end] pair of seconds"),
    ([[None, 1.0]], "Range 1 must be a [start, end] pair of seconds"),
    ([[float("nan"), 1.0]], "finite"),
    ([[0.0, float("inf")]], "finite"),
    # A JSON integer too large for a float passes the strict schema; it used
    # to raise OverflowError out of float() and answer 500.
    ([[0, int("1" + "0" * 400)]], "Range 1 must be a [start, end] pair of finite seconds"),
    ([[-0.5, 1.0]], "Range 1 [-0.500, 1.000] starts before 0"),
    ([[2.0, 1.0]], "Range 1 [2.000, 1.000] must end after it starts"),
    ([[1.0, 1.0]], "Range 1 [1.000, 1.000] must end after it starts"),
    ([[1.0, 1.0004]], "must end after it starts"),  # the same instant once rounded
    ([[0.0, 13.0]], "Range 1 [0.000, 13.000] runs past the end of the source, which is 12.000 s long"),
    ([[0.0, 12.001]], "runs past the end of the source"),
    # The stored precision, never ``:g``: six significant digits printed
    # 8000.001 as 8000, and the message contradicted itself on a long source.
    ([[0.0, 8000.001]], "Range 1 [0.000, 8000.001] runs past the end of the source"),
    ([[0.0, 5.0], [4.0, 8.0]], "Range 2 [4.000, 8.000] overlaps or precedes range 1 [0.000, 5.000]"),
    ([[5.0, 8.0], [0.0, 2.0]], "Range 2 [0.000, 2.000] overlaps or precedes range 1 [5.000, 8.000]"),
    ([[0.0, 5.0], [5.0, 8.0], [7.999, 9.0]], "Range 3 [7.999, 9.000] overlaps or precedes range 2 [5.000, 8.000]"),
])
def test_a_bad_list_is_refused_by_name(keep, message):
    with pytest.raises(ValueError) as exc:
        edit.validate_keep(keep, 12.0)
    assert message in str(exc.value), str(exc.value)


def test_a_long_source_is_named_in_full():
    with pytest.raises(ValueError) as exc:
        edit.validate_keep([[0.0, 8000.002]], 8000.001)
    assert "which is 8000.001 s long" in str(exc.value)


def test_a_list_long_enough_to_be_an_attack_on_the_record_is_refused():
    too_many = [[i * 0.001, i * 0.001 + 0.001] for i in range(edit.MAX_RANGES + 1)]
    with pytest.raises(ValueError) as exc:
        edit.validate_keep(too_many, 12.0)
    assert f"limited to {edit.MAX_RANGES} ranges" in str(exc.value)


def test_an_unknown_source_length_cannot_check_anything():
    for bad in (0, -1.0, float("nan"), float("inf"), "twelve"):
        with pytest.raises(ValueError) as exc:
            edit.validate_keep([[0.0, 1.0]], bad)
        assert "length is unknown" in str(exc.value), bad


def test_a_source_length_of_none_skips_only_the_bound():
    """For reading back an edit whose audio has since been removed by hand:
    the shape and the order are still checked, the bound cannot be."""
    assert edit.validate_keep([[0.0, 999.0]], None) == [[0.0, 999.0]]
    with pytest.raises(ValueError):
        edit.validate_keep([[5.0, 1.0]], None)


# ── the arithmetic ────────────────────────────────────────────────────────────

def test_the_output_is_the_sum_of_the_kept_ranges():
    assert edit.output_duration(KEEP) == 10.5
    assert edit.output_duration([[0, 47.3], [52.3, 341.008]]) == 336.008, "rounded, so float noise never leaks"
    assert edit.output_duration([[0.0, 12.0]]) == 12.0


@pytest.mark.parametrize("t_source, t_timeline", [
    (0.0, 0.0), (1.25, 1.25), (5.999, 5.999),
    (6.0, 6.0),      # the end of a kept range is the join...
    (7.5, 6.0),      # ... and so is the start of the next: the same output instant
    (8.0, 6.5), (10.0, 8.5), (12.0, 10.5),
    (6.001, None), (6.5, None), (7.0, None), (7.499, None),   # the hole
    (-0.1, None), (12.001, None),                              # outside the source
])
def test_to_timeline_closes_the_holes(t_source, t_timeline):
    assert edit.to_timeline(t_source, KEEP) == t_timeline


def test_to_source_is_the_inverse_and_clamps_at_the_ends():
    for t in (0.0, 1.25, 5.999, 6.0, 6.001, 8.25, 10.5):
        assert edit.to_timeline(edit.to_source(t, KEEP), KEEP) == t, t
    assert edit.to_source(6.001, KEEP) == 7.501
    # At a join the LATER range wins: 6.0 in the source is the first frame the
    # output never shows (trim's end is exclusive), so a filmstrip or playhead
    # seeked there would show a cut frame; 7.5 is what is on screen at the join.
    assert edit.to_source(6.0, KEEP) == 7.5
    assert edit.to_timeline(6.0, KEEP) == edit.to_timeline(7.5, KEEP) == 6.0, "both sides still map to the join"
    assert edit.to_source(5.999, KEEP) == 5.999 and edit.to_source(6.001, KEEP) == 7.501
    # A playhead that has run to the end (give or take a float) shows the last
    # kept frame, and one before the start shows the first.
    assert edit.to_source(10.5000001, KEEP) == 12.0
    assert edit.to_source(99.0, KEEP) == 12.0
    assert edit.to_source(-1.0, KEEP) == 0.0
    assert edit.to_source(-1.0, [[3.0, 5.0]]) == 3.0


def test_the_shared_fixture_holds_for_this_copy_of_the_projection():
    """``frontend/src/lib/edit.ts`` is the client's copy of ``to_timeline``,
    ``to_source``, ``output_duration`` and ``whole_source`` - the one
    duplication the spec accepts, because the timeline seeks and draws on
    every pointer movement - and its vitest suite (``edit.test.ts``) reads
    this same file. The cases are the inline ones above, in one place, so a
    change to either copy that the other does not make fails here or there."""
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    keep = cases["keep"]
    assert keep == KEEP and cases["source_duration"] == 12.0, "the fixture is the inline scenario"
    assert len(cases["to_timeline"]) >= 10 and len(cases["to_source"]) >= 8, "an emptied file must not pass"
    # The one rule E1 reversed - the LATER range wins at a join - must not be
    # the row a truncation happens to drop.
    assert [6.0, 7.5] in cases["to_source"] and [7.5, 6.0] in cases["to_timeline"]

    assert edit.output_duration(keep) == cases["output_duration"]
    for t_source, t_timeline in cases["to_timeline"]:
        assert edit.to_timeline(t_source, keep) == t_timeline, (t_source, t_timeline)
    for t_timeline, t_source in cases["to_source"]:
        assert edit.to_source(t_timeline, keep) == t_source, (t_timeline, t_source)
    for t in cases["round_trip"]:
        assert edit.to_timeline(edit.to_source(t, keep), keep) == t, t
    for held, t_timeline, t_source in cases["clamps"]:
        assert edit.to_source(t_timeline, held) == t_source, (held, t_timeline)
    for held, source_duration, expected in cases["whole_source"]:
        assert edit.whole_source(held, source_duration) is expected, (held, source_duration)
    for held, expected in cases["output_durations"]:
        assert edit.output_duration(held) == expected, held


def test_whole_source_is_true_only_when_nothing_is_removed():
    assert edit.whole_source([[0.0, 12.0]], 12.0) is True
    assert edit.whole_source([[0.0, 6.0], [6.0, 12.0]], 12.0) is True, "a split removes nothing"
    assert edit.whole_source([[0.0, 12.0]], 12.0004) is True, "the source's length rounds like the ranges"
    assert edit.whole_source([[0.0, 11.999]], 12.0) is False, "one millisecond removed is an edit"
    assert edit.whole_source(KEEP, 12.0) is False
    assert edit.whole_source([[0.5, 12.0]], 12.0) is False


def test_a_sentence_is_kept_iff_its_start_is_kept():
    """Decision 1 of the spec, in one place: kept iff ``start`` lies in a kept
    range - ``[start, end)``, so a sentence beginning exactly where a cut begins
    is cut with it (that is what snapping a cut to a sentence means), and one
    beginning exactly where the next kept range starts is kept, at the join."""
    projected = edit.project_transcript(SEGMENTS, KEEP)
    assert [s["index"] for s in projected] == [0, 1, 3, 4], "the third sentence started in the hole"
    assert [s["text"] for s in projected] == ["First sentence.", "Second sentence.", "Fourth sentence.", "Fifth sentence."]
    assert [(s["start"], s["end"]) for s in projected] == [(0.0, 2.0), (5.0, 6.0), (6.5, 7.5), (8.5, 10.5)]

    on_the_cut = [{"start": 6.0, "end": 7.0, "text": "Begins where the cut begins."}]
    assert edit.project_transcript(on_the_cut, KEEP) == []
    on_the_join = [{"start": 7.5, "end": 9.0, "text": "Begins where the picture resumes."}]
    assert [(s["start"], s["end"]) for s in edit.project_transcript(on_the_join, KEEP)] == [(6.0, 7.5)]


def test_a_kept_sentences_end_is_clamped_to_its_range():
    """Nothing is ever pinned past the output: the second sentence ran into
    the hole and now ends at its edge."""
    projected = edit.project_transcript(SEGMENTS, KEEP)
    assert (projected[1]["start"], projected[1]["end"]) == (5.0, 6.0)
    # A sentence that ends before it starts (a hand-edited file) is given no
    # width rather than a negative one.
    odd = edit.project_transcript([{"start": 1.0, "end": 0.5, "text": "Odd."}], KEEP)
    assert (odd[0]["start"], odd[0]["end"]) == (1.0, 1.0)


def test_every_override_rides_through_untouched_and_the_offset_is_not_applied():
    """Only ``start`` and ``end`` are remapped. ``offset`` in particular is left
    exactly as stored, because the engine applies it itself: the pin is
    ``to_timeline(start) + offset``, and applying it here as well would apply
    it twice."""
    stored = {
        "start": 8.0, "end": 9.0, "text": "Fourth sentence.",
        "offset": -0.4, "muted": True, "voice": "en-GB-RyanNeural", "provider": "edge_tts", "speed": 1.2,
        "note": "anything else a hand-edited file carries rides through too",
    }
    (projected,) = edit.project_transcript([stored], KEEP)
    assert projected == {**stored, "index": 0, "start": 6.5, "end": 7.5}
    assert projected["offset"] == -0.4
    assert set(narration.OVERRIDE_KEYS) <= set(projected)


def test_the_projection_never_touches_the_stored_transcript():
    """Every returned dict is a new dict: the transcript on disk is what
    ``set_transcript`` identifies sentences by, and it must never move."""
    transcript = [dict(s) for s in SEGMENTS]
    before = json.dumps(transcript)
    projected = edit.project_transcript(transcript, KEEP)
    assert json.dumps(transcript) == before
    assert all(p is not s for p in projected for s in transcript)
    projected[0]["start"] = 99.0
    assert transcript[0]["start"] == 0.0


def test_a_malformed_transcript_projects_what_it_can():
    """A hand-edited project.json can hold anything: a non-dict is skipped, an
    unusable start lands at zero (the plan's own fallback), and an edit that
    cuts every sentence projects to nothing rather than failing."""
    odd = [{"start": None, "end": "x", "text": "Lands at zero."}, "not a segment", {"start": 6.5, "end": 7.0, "text": "Gone."}]
    projected = edit.project_transcript(odd, KEEP)
    assert [(s["index"], s["start"], s["end"]) for s in projected] == [(0, 0.0, 0.0)]
    assert edit.project_transcript(SEGMENTS, [[3.0, 4.0]]) == []
    assert edit.project_transcript([], KEEP) == []
    assert edit.project_transcript(None, KEEP) == []


def test_the_projection_the_plan_and_the_render_use_is_the_same_function():
    """The point of ``services.edit``: neither caller carries a copy."""
    import inspect

    from services import revoice

    assert "edit.apply(" in inspect.getsource(revoice.revoice_project)
    # The plan's one call lives in ``project_narration`` since the transcript
    # download shares it (T1): the plan goes through that, never around it.
    assert "edit.apply(" in inspect.getsource(narration.project_narration)
    assert "project_narration(" in inspect.getsource(narration.plan)
    assert "edit.apply(" not in inspect.getsource(narration.plan)
    for module in (revoice, narration):
        assert "def project_transcript" not in inspect.getsource(module)


def test_apply_is_the_one_decision_about_whether_an_edit_changes_anything():
    transcript = [dict(s) for s in SEGMENTS]
    untouched = edit.apply({"transcript": transcript}, transcript, 12.0)
    assert (untouched.keep, untouched.cut, untouched.output_duration) == (None, False, 12.0)
    assert untouched.sentences is transcript, "no edit: the transcript itself, unprojected"

    split = edit.apply({"edit": {"version": 1, "keep": [[0, 6], [6, 12]]}}, transcript, 12.0)
    assert (split.keep, split.cut, split.output_duration) == ([[0.0, 6.0], [6.0, 12.0]], False, 12.0)
    assert split.sentences is transcript, "a split removes nothing, so nothing is projected"

    cut = edit.apply({"edit": {"version": 1, "keep": KEEP}}, transcript, 12.0)
    assert (cut.keep, cut.cut, cut.output_duration) == (KEEP, True, 10.5)
    assert [s["index"] for s in cut.sentences] == [0, 1, 3, 4]

    with pytest.raises(edit.SourceLengthUnknown):
        edit.apply({"edit": {"version": 1, "keep": KEEP}}, transcript, None)
    with pytest.raises(ValueError) as exc:
        edit.apply({"edit": {"version": 3, "video": {"keep": KEEP}}}, transcript, 12.0)
    assert "different version" in str(exc.value)
    # A version-1 list under the version-2 number could mean two things, and
    # "no edit" is the quiet answer to neither.
    with pytest.raises(ValueError) as exc:
        edit.apply({"edit": {"version": 2, "keep": KEEP}}, transcript, 12.0)
    assert "mixes two shapes" in str(exc.value)


# ── the routes ────────────────────────────────────────────────────────────────

def test_a_project_with_no_edit_keeps_everything(client):
    pid = _video()
    r = _get(client, pid)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version": 2,
        "video": {"keep": None, "output_duration": 12.0},
        "narration": {"keep": None, "output_duration": 12.0},
        "music": [],
        "source_duration": 12.0,
        "output_duration": 12.0,
    }


def test_without_extracted_audio_there_is_no_length_to_report(client):
    """Neither length is known, and neither is claimed: null, not 0.0."""
    pid = _video(audio_seconds=None)
    assert _get(client, pid).json() == {
        "version": 2,
        "video": {"keep": None, "output_duration": None},
        "narration": {"keep": None, "output_duration": None},
        "music": [],
        "source_duration": None,
        "output_duration": None,
    }


def test_put_stores_the_ranges_and_get_reads_them_back(client):
    """The version-1 body - one list - means both tracks, cut together."""
    pid = _video()
    r = _put(client, pid, [[0, 6], [7.5, 12]])
    assert r.status_code == 200, r.text
    assert r.json() == {
        "version": 2,
        "video": {"keep": [[0.0, 6.0], [7.5, 12.0]], "output_duration": 10.5},
        "narration": {"keep": [[0.0, 6.0], [7.5, 12.0]], "output_duration": 10.5},
        "music": [],
        "source_duration": 12.0,
        "output_duration": 10.5,
    }
    assert _get(client, pid).json() == r.json()
    assert store.get_project(pid)["edit"] == {
        "version": 2,
        "video": {"keep": [[0.0, 6.0], [7.5, 12.0]]},
        "narration": {"keep": [[0.0, 6.0], [7.5, 12.0]]},
    }

    # A second PUT replaces the whole edit - it is small, and a partial PATCH buys nothing.
    assert _put(client, pid, [[1.0, 2.0]]).status_code == 200
    assert store.get_project(pid)["edit"]["video"]["keep"] == [[1.0, 2.0]]
    assert store.get_project(pid)["edit"]["narration"]["keep"] == [[1.0, 2.0]]


def test_the_transcript_never_moves(client):
    """THE rule. A cut must not touch ``start``/``end`` on disk: the transcript
    Save identifies a sentence by its window, and moving the sentences would
    drop every adjustment on every later one."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/3", json={"offset": -0.4, "speed": 1.2}).status_code == 200
    meta = store.PROJECTS_DIR / pid / "project.json"
    before = json.loads(meta.read_text(encoding="utf-8"))["transcript"]

    assert _put(client, pid, KEEP).status_code == 200
    assert client.delete(f"/api/projects/{pid}/edit").status_code == 200
    after = json.loads(meta.read_text(encoding="utf-8"))["transcript"]
    assert after == before
    assert after[3] == {"start": 8.0, "end": 9.0, "text": "Fourth sentence.", "offset": -0.4, "speed": 1.2}


def test_the_ranges_are_stored_at_the_transcripts_precision(client):
    pid = _video()
    stored = _put(client, pid, [[0.00049, 5.99951], [7.5004, 12]]).json()
    assert stored["video"]["keep"] == stored["narration"]["keep"] == [[0.0, 6.0], [7.5, 12.0]]


@pytest.mark.parametrize("keep, message", [
    ([], "Keep at least one range"),
    ([[0.0, 13.0]], "runs past the end of the source, which is 12.000 s long"),
    ([[0.0, 5.0], [4.0, 8.0]], "overlaps or precedes range 1"),
    ([[2.0, 1.0]], "must end after it starts"),
    ([[0.0, 1.0, 2.0]], "must be a [start, end] pair"),
])
def test_a_bad_list_is_a_400_that_names_the_range_and_stores_nothing(client, keep, message):
    pid = _video()
    r = _put(client, pid, keep)
    assert r.status_code == 400, r.text
    assert message in r.json()["detail"]
    assert "edit" not in store.get_project(pid)


def test_the_body_is_strict(client):
    """``extra="forbid"`` and strict numbers: an unknown key is a 422 rather
    than a silent drop, a boolean is not one second in, a string is not a
    number. NaN and infinity get through pydantic and are the validator's."""
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"keep": KEEP, "version": 1}).status_code == 422
    assert _put(client, pid, [[True, 6.0]]).status_code == 422
    assert _put_tracks(client, pid, video=[[True, 6.0]]).status_code == 422
    assert _put_tracks(client, pid, narration=[["0", 6.0]]).status_code == 422
    assert _put(client, pid, [["0", 6.0]]).status_code == 422
    r = client.put(f"/api/projects/{pid}/edit", content='{"keep": [[NaN, 6.0]]}',
                   headers={"content-type": "application/json"})
    assert r.status_code == 400 and "finite" in r.json()["detail"]
    # A 400-digit integer passes StrictInt and overflowed float() into a 500.
    r = client.put(f"/api/projects/{pid}/edit", content='{"keep": [[0, 1' + "0" * 400 + ']]}',
                   headers={"content-type": "application/json"})
    assert r.status_code == 400 and "finite" in r.json()["detail"]
    assert "edit" not in store.get_project(pid)


def test_a_video_without_extracted_audio_cannot_be_cut_yet(client):
    """The ranges are checked against the audio's length, so without
    ``audio.wav`` there is nothing to check them against: a 409 that says
    what produces it, in the words the tracks route already uses."""
    fresh = store.import_upload("raw.mp4", b"video-bytes")["id"]
    r = _put(client, fresh, [[0.0, 1.0]])
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "Transcribe the video first - its audio is extracted then."
    assert "edit" not in store.get_project(fresh)

    transcribed_but_no_wav = _video(audio_seconds=None)
    assert _put(client, transcribed_but_no_wav, [[0.0, 1.0]]).status_code == 409


def test_a_deck_has_no_picture_to_cut(client):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    for r in (_get(client, deck), _put(client, deck, [[0.0, 1.0]]), client.delete(f"/api/projects/{deck}/edit")):
        assert r.status_code == 400 and "Only video projects" in r.json()["detail"], r.text


def test_a_missing_project_is_a_404_for_all_three(client):
    absent = "aabbccddeeff"
    assert _get(client, absent).status_code == 404
    assert _put(client, absent, [[0.0, 1.0]]).status_code == 404
    assert client.delete(f"/api/projects/{absent}/edit").status_code == 404


def test_delete_goes_back_to_keep_everything_and_is_idempotent(client):
    pid = _video()
    assert _put(client, pid, KEEP).status_code == 200
    r = client.delete(f"/api/projects/{pid}/edit")
    assert r.status_code == 200, r.text
    assert r.json() == _payload(None, None)
    assert "edit" not in store.get_project(pid), "removed, not written empty: the record reads as one that never had an edit"
    together = "video: 2 ranges kept, 1.5 s removed; narration: 2 ranges kept, 1.5 s removed"
    assert [row["detail"] for row in _edit_rows()] == ["cleared", together]

    # Clearing what is already clear is a 200 that changes nothing and records
    # nothing - there was nothing to clear.
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    r = client.delete(f"/api/projects/{pid}/edit")
    assert r.status_code == 200 and r.json()["video"]["keep"] is None and r.json()["narration"]["keep"] is None
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before
    assert [row["detail"] for row in _edit_rows()] == ["cleared", together], "no row for a no-op"


def test_every_write_is_audited_with_counts_and_never_times(client):
    """"Who cut this" is answerable; where they cut is not in the log, and
    neither is any text."""
    pid = _video()
    assert _put(client, pid, KEEP).status_code == 200
    assert _put(client, pid, [[0.0, 6.0]]).status_code == 200
    assert client.delete(f"/api/projects/{pid}/edit").status_code == 200
    assert _get(client, pid).status_code == 200

    rows = _edit_rows()
    assert [row["detail"] for row in rows] == [
        "cleared",
        "video: 1 range kept, 6.0 s removed; narration: 1 range kept, 6.0 s removed",
        "video: 2 ranges kept, 1.5 s removed; narration: 2 ranges kept, 1.5 s removed",
    ]
    assert all(row["entity"] == "project" and row["entity_id"] == pid for row in rows)
    assert not any("7.5" in row["detail"] or "sentence" in row["detail"] for row in rows)


def test_writes_are_refused_while_a_job_holds_the_project_but_the_read_is_not(client, monkeypatch):
    """A re-voice job reads the edit it is rendering; the GET writes nothing."""
    pid = _video()
    assert _put(client, pid, KEEP).status_code == 200
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "revoice", "status": "running"} if project_id == pid else None,
    )

    assert _put(client, pid, [[0.0, 1.0]]).status_code == 409
    assert client.delete(f"/api/projects/{pid}/edit").status_code == 409
    assert _get(client, pid).status_code == 200
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before


def test_a_write_landing_beside_an_adjustment_destroys_neither(client):
    """Both are read-modify-writes of the same file, so both take
    ``services.projects.project_lock`` - the store's, never a lock of the
    feature's own. Without it the later writer wins on the whole record."""
    pid = _video()
    for attempt in range(20):
        record = store.get_project(pid)
        record.pop("edit", None)
        record["transcript"] = [dict(s) for s in SEGMENTS]
        store.save_project(record)
        start = threading.Barrier(2)
        failures: list[BaseException] = []

        def adjust():
            start.wait()
            try:
                narration.update_segment(pid, 3, offset=-0.4)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        def cut():
            start.wait()
            try:
                edit.set_edit(pid, video=KEEP, narration=KEEP)
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)

        threads = [threading.Thread(target=adjust), threading.Thread(target=cut)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert failures == [], failures
        saved = store.get_project(pid)
        assert saved is not None, f"run {attempt}: project.json was torn"
        assert saved["transcript"][3].get("offset") == -0.4, f"run {attempt}: the adjustment was lost"
        assert saved.get("edit", {}).get("video", {}).get("keep") == KEEP, f"run {attempt}: the edit was lost"


def test_an_edit_this_version_cannot_read_is_refused_rather_than_ignored(client):
    """Rendering the whole video while the user believes a cut is applied is
    the one thing that must not happen quietly."""
    pid = _video()
    record = store.get_project(pid)
    record["edit"] = {"version": 3, "video": {"keep": KEEP}}
    store.save_project(record)

    r = _get(client, pid)
    assert r.status_code == 400 and "different version" in r.json()["detail"]
    r = client.get(f"/api/projects/{pid}/narration/plan")
    assert r.status_code == 400 and "different version" in r.json()["detail"]
    # A new PUT replaces it, so the user is not stuck.
    assert _put(client, pid, KEEP).status_code == 200
    assert _get(client, pid).status_code == 200

    record = store.get_project(pid)
    record["edit"] = {"version": 1, "keep": [[5.0, 1.0]]}
    store.save_project(record)
    assert _get(client, pid).status_code == 400
    assert client.delete(f"/api/projects/{pid}/edit").status_code == 200
    assert _get(client, pid).status_code == 200


def test_storing_or_clearing_an_edit_drops_the_memoised_speaking_rate(client, baseline):
    """The rate is measured from three of the sentences the render will speak,
    and the edit decides which those are."""
    pid = _video()
    _plan(client, pid)
    assert len(baseline) == 1
    assert _put(client, pid, KEEP).status_code == 200
    _plan(client, pid)
    assert len(baseline) == 2
    assert baseline[1]["texts"] == ["First sentence.", "Second sentence.", "Fourth sentence.", "Fifth sentence."], (
        "measured over the projected sentences, as the render measures"
    )
    assert client.delete(f"/api/projects/{pid}/edit").status_code == 200
    _plan(client, pid)
    assert len(baseline) == 3


def test_an_edit_whose_audio_has_gone_is_read_back_but_not_applied(client):
    """The audio removed by hand after the edit was made: the GET still
    answers (the bound cannot be checked, so it is not), and the plan refuses
    to apply an edit it cannot measure, as the render does."""
    pid = _video()
    assert _put(client, pid, KEEP).status_code == 200
    (store.PROJECTS_DIR / pid / "audio.wav").unlink()

    assert _get(client, pid).json() == {
        "version": 2,
        "video": {"keep": KEEP, "output_duration": 10.5},
        "narration": {"keep": KEEP, "output_duration": 10.5},
        "music": [],
        "source_duration": None,
        "output_duration": 10.5,
    }
    r = client.get(f"/api/projects/{pid}/narration/plan")
    assert r.status_code == 400 and "extracted audio is missing" in r.json()["detail"]


# ── the plan ──────────────────────────────────────────────────────────────────

def test_the_plan_returns_the_sentences_in_timeline_seconds(client):
    """The client never reimplements render arithmetic: the plan's sentences
    are already where the render will put them, holes closed, and a sentence
    the edit removed is not in the plan because it is not in the render."""
    pid = _video()
    assert _put(client, pid, KEEP).status_code == 200
    plan = _plan(client, pid)

    assert plan["duration"] == 10.5, "the output's length, not the source's"
    assert plan["edit"] == _payload(KEEP, KEEP)
    sentences = plan["sentences"]
    assert [s["index"] for s in sentences] == [0, 1, 3, 4], "addressed by their index in the STORED transcript"
    assert [(s["start"], s["end"]) for s in sentences] == [(0.0, 2.0), (5.0, 6.0), (6.5, 7.5), (8.5, 10.5)]
    assert [s["pinned_start"] for s in sentences] == [0.0, 5.0, 6.5, 8.5]
    assert [s["window"] for s in sentences] == [pytest.approx(5.0), pytest.approx(1.5), pytest.approx(2.0), pytest.approx(2.0)]
    assert sentences[2]["preview_url"].startswith(f"/api/projects/{pid}/transcript/3/preview")
    assert all(s["speakable"] and not s["past_end"] for s in sentences), "nothing is past the end by construction"


def test_overrides_ride_through_the_projection_and_the_offset_is_applied_once(client):
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/3", json={"offset": -0.4, "speed": 1.2}).status_code == 200
    assert client.patch(f"/api/projects/{pid}/transcript/4", json={"muted": True}).status_code == 200
    assert _put(client, pid, KEEP).status_code == 200

    by_index = {s["index"]: s for s in _plan(client, pid)["sentences"]}
    fourth = by_index[3]
    assert (fourth["start"], fourth["end"]) == (6.5, 7.5), "the block keeps its timeline window"
    assert fourth["pinned_start"] == pytest.approx(6.1), "to_timeline(start) + offset - once"
    assert fourth["speed"] == 1.2 and fourth["squeezable"] is False
    assert by_index[4]["muted"] is True and by_index[4]["speakable"] is False
    # And the PATCH still addressed the right sentence after the projection.
    assert store.get_project(pid)["transcript"][3]["text"] == "Fourth sentence."


def test_a_sentence_cut_by_the_edit_gives_its_room_to_the_one_before(client):
    """Exactly as a muted one does: the window runs to the next sentence that
    is actually spoken, which is now further away in the output."""
    fast = [
        {"start": 0.0, "end": 2.0, "text": FAST},
        {"start": 5.0, "end": 7.0, "text": "Cut with the picture."},
        {"start": 10.0, "end": 12.0, "text": "Third."},
    ]
    pid = _video(fast)
    assert _plan(client, pid)["sentences"][0]["speed"] == pytest.approx(1.09), "82 characters due in 5 s"

    assert _put(client, pid, [[0.0, 4.0], [8.0, 12.0]]).status_code == 200
    sentences = _plan(client, pid)["sentences"]
    assert [s["index"] for s in sentences] == [0, 2]
    assert sentences[0]["window"] == pytest.approx(6.0), "to the third sentence's pin, now at 6.0"
    assert sentences[0]["speed"] == processing.per_sentence_speed(FAST, 6.0, 15.0, 1.0) == 1.0


def test_an_edit_that_keeps_everything_changes_nothing_but_is_still_reported(client):
    pid = _video()
    untouched = _plan(client, pid)
    assert untouched["edit"] == _payload(None, None)

    assert _put(client, pid, [[0.0, 6.0], [6.0, 12.0]]).status_code == 200
    split = _plan(client, pid)
    assert split["sentences"] == untouched["sentences"]
    assert split["duration"] == untouched["duration"] == 12.0
    assert split["edit"]["video"]["keep"] == [[0.0, 6.0], [6.0, 12.0]], "so the timeline can draw the boundary"
    assert split["edit"]["narration"]["keep"] == [[0.0, 6.0], [6.0, 12.0]]


def test_the_plan_is_unchanged_for_a_project_that_never_had_an_edit(client):
    """The no-edit path is today's, key for key: only ``edit`` was added."""
    pid = _video()
    plan = _plan(client, pid)
    assert set(plan) == {"duration", "baseline_rate", "squeeze_tolerance", "squeeze_max_factor", "edit", "sentences"}
    assert [s["index"] for s in plan["sentences"]] == [0, 1, 2, 3, 4]
    assert [(s["start"], s["end"]) for s in plan["sentences"]] == [(s["start"], s["end"]) for s in SEGMENTS]


def test_the_ownership_sweep_names_all_three_routes():
    """Belt to the sweep's own braces: the guard in test_project_ownership
    fails on a missing route, and this says which file to look in."""
    from test_project_ownership import PROJECT_SCOPED_ROUTES

    for method in ("GET", "PUT", "DELETE"):
        assert (method, "/api/projects/{pid}/edit") in PROJECT_SCOPED_ROUTES, method


# ── tracks (E3): one list per track ───────────────────────────────────────────

# A narration list that differs from the picture's: it removes 6.4-7.0 - the
# third sentence again, but the sentences after it move by 0.6 s here and by
# 1.5 s under KEEP - so a projection through the WRONG list gives the same
# indices and the wrong starts. That is the trap the per-track cases exist to
# catch (spec §11.6, trap 18).
NARRATION = [[0.0, 6.4], [7.0, 12.0]]  # output: 11.4 s


def test_a_version_1_record_is_read_as_cut_together_and_written_back_as_version_2(client):
    """Trap 22: version 1 is a shape this code KNOWS - E1's one list, meaning
    both tracks - and whoever bumps the version reads the older shape rather
    than refusing it. Nothing migrates; the next write rewrites it."""
    pid = _video()
    record = store.get_project(pid)
    record["edit"] = {"version": 1, "keep": KEEP}
    store.save_project(record)

    assert edit.stored_tracks(record, 12.0) == (KEEP, KEEP)
    assert edit.stored_keep(record, 12.0) == KEEP, "E1's name still answers the picture's list"
    assert edit.describe(record) == _payload(KEEP, KEEP)
    assert _get(client, pid).json() == _payload(KEEP, KEEP)
    plan = _plan(client, pid)
    assert plan["edit"] == _payload(KEEP, KEEP) and plan["duration"] == 10.5
    assert [s["index"] for s in plan["sentences"]] == [0, 1, 3, 4], "E1's behaviour, exactly"

    assert store.get_project(pid)["edit"]["version"] == 1, "read, not rewritten: no migration"
    assert _put_tracks(client, pid, narration=NARRATION).status_code == 200
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION},
    }, "rewritten as the two tracks it always meant; the one the body did not name keeps the v1 list"


def test_a_version_2_record_reads_one_list_per_track():
    assert edit.stored_tracks({}, 12.0) == (None, None)
    assert edit.stored_tracks({"edit": {"version": 2}}, 12.0) == (None, None), "both absent: no edit at all"
    assert edit.stored_tracks({"edit": {"version": 2, "video": {"keep": KEEP}}}, 12.0) == (KEEP, None)
    assert edit.stored_tracks({"edit": {"version": 2, "narration": {"keep": NARRATION}}}, 12.0) == (None, NARRATION)
    both = {"edit": {"version": 2, "video": {"keep": [(0, 6), [7.5, 12]]}, "narration": {"keep": NARRATION}, "music": []}}
    assert edit.stored_tracks(both, 12.0) == (KEEP, NARRATION), "validated and rounded; E4's key rides through"
    assert edit.stored_keep(both, 12.0) == KEEP, "the PICTURE's list, never the narration's"

    # A bad track is refused by name; so is a track that is not a track, and a
    # version this code has never seen.
    with pytest.raises(ValueError) as exc:
        edit.stored_tracks({"edit": {"version": 2, "narration": {"keep": [[5.0, 1.0]]}}}, 12.0)
    assert str(exc.value).startswith("narration: Range 1 [5.000, 1.000] must end after it starts")
    with pytest.raises(ValueError) as exc:
        edit.stored_tracks({"edit": {"version": 2, "video": KEEP}}, 12.0)
    assert str(exc.value).startswith("video: The edit must be a list")
    with pytest.raises(ValueError):
        edit.stored_tracks({"edit": {"version": 2, "video": {"keep": None}}}, 12.0)
    with pytest.raises(ValueError):
        edit.stored_tracks({"edit": "nope"}, 12.0)
    with pytest.raises(ValueError) as exc:
        edit.stored_tracks({"edit": {"version": 3}}, 12.0)
    assert "different version" in str(exc.value)


def test_apply_projects_through_the_narration_list_and_cuts_with_the_video_list():
    """Two lists, one axis (trap 18), and a locked track untouched (trap 19):
    a video-only edit hands back the very transcript objects, a narration-only
    edit leaves the picture whole, and a list that only splits does neither."""
    transcript = [dict(s) for s in SEGMENTS]

    video_only = edit.apply({"edit": {"version": 2, "video": {"keep": KEEP}}}, transcript, 12.0)
    assert (video_only.video, video_only.narration) == (KEEP, None)
    assert (video_only.cut, video_only.projected) == (True, False)
    assert video_only.sentences is transcript, "nothing projected: the objects an unedited project hands the engine"
    assert (video_only.output_duration, video_only.narration_duration) == (10.5, 12.0)
    assert video_only.keep == KEEP, "E1's name is the picture's list"

    narration_only = edit.apply({"edit": {"version": 2, "narration": {"keep": NARRATION}}}, transcript, 12.0)
    assert (narration_only.video, narration_only.narration) == (None, NARRATION)
    assert (narration_only.cut, narration_only.projected) == (False, True)
    assert [(s["index"], s["start"], s["end"]) for s in narration_only.sentences] == [
        (0, 0.0, 2.0), (1, 5.0, 6.4), (3, 7.4, 8.4), (4, 9.4, 11.4),
    ]
    assert (narration_only.output_duration, narration_only.narration_duration) == (12.0, 11.4), "the picture is whole"
    assert narration_only.keep is None

    both = edit.apply({"edit": {"version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION}}}, transcript, 12.0)
    assert (both.cut, both.projected) == (True, True)
    assert [s["start"] for s in both.sentences] == [0.0, 5.0, 7.4, 9.4], "through the NARRATION list, never the video's"
    assert (both.output_duration, both.narration_duration) == (10.5, 11.4)

    # A list that only splits removes nothing on either track: nothing is
    # projected, nothing is cut, and the path is the unedited one (trap 23).
    for track in ("video", "narration"):
        split = edit.apply({"edit": {"version": 2, track: {"keep": [[0.0, 6.0], [6.0, 12.0]]}}}, transcript, 12.0)
        assert (split.cut, split.projected) == (False, False), track
        assert split.sentences is transcript, track
        assert (split.output_duration, split.narration_duration) == (12.0, 12.0), track

    assert json.dumps(transcript) == json.dumps(SEGMENTS), "the transcript never moves"


def test_the_shared_fixture_pins_the_per_track_projection():
    """The fixture's ``tracks`` section, read by ``edit.test.ts`` as well: the
    client draws each lane through its own list, and these cases are what
    tell a projection through the wrong list from the right one."""
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tracks = cases["tracks"]
    assert tracks["segments"] == [[s["start"], s["end"]] for s in SEGMENTS], "the fixture is the inline scenario"
    assert tracks["video"] == KEEP and tracks["narration"] == NARRATION
    assert set(tracks["cases"]) == {"video_only", "narration_only", "both", "together"}, "an emptied section must not pass"
    for name, case in tracks["cases"].items():
        stored = {t: {"keep": case[t]} for t in ("video", "narration") if case[t] is not None}
        applied = edit.apply({"edit": {"version": 2, **stored}}, SEGMENTS, cases["source_duration"])
        listed = ([(s["index"], s) for s in applied.sentences] if applied.projected
                  else list(enumerate(applied.sentences)))
        assert [[i, s["start"], s["end"]] for i, s in listed] == case["sentences"], name
        assert (applied.cut, applied.projected) == (case["cut"], case["projected"]), name
        assert (applied.output_duration, applied.narration_duration) == (case["duration"], case["narration_duration"]), name
    # The two per-track cases must disagree with cut-together on at least one
    # start, or they could not catch a projection through the wrong list.
    assert tracks["cases"]["both"]["sentences"] != tracks["cases"]["together"]["sentences"]


def test_put_takes_one_list_per_track_and_the_v1_body_means_both(client):
    pid = _video()
    r = _put_tracks(client, pid, video=KEEP)
    assert r.status_code == 200, r.text
    assert r.json() == _payload(KEEP, None)
    assert store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}}, "a track not given is stored absent"

    r = _put_tracks(client, pid, video=None, narration=NARRATION)
    assert r.status_code == 200, r.text
    assert r.json() == _payload(None, NARRATION)
    assert store.get_project(pid)["edit"] == {"version": 2, "narration": {"keep": NARRATION}}, "an explicit null too"

    r = _put_tracks(client, pid, video=KEEP, narration=NARRATION)
    assert r.status_code == 200 and r.json() == _payload(KEEP, NARRATION)
    assert r.json()["output_duration"] == 10.5, "the top-level length is the picture's"
    assert r.json()["narration"]["output_duration"] == 11.4
    assert _get(client, pid).json() == r.json()

    # One track null, the other not named: the picture is whole again and the
    # narration's list is exactly where it was.
    r = _put_tracks(client, pid, video=None)
    assert r.status_code == 200 and r.json() == _payload(None, NARRATION)
    assert store.get_project(pid)["edit"] == {"version": 2, "narration": {"keep": NARRATION}}
    assert _put_tracks(client, pid, video=KEEP, narration=KEEP).status_code == 200

    # The version-1 body means both tracks, for curl and any older client...
    assert _put(client, pid, KEEP).json() == _payload(KEEP, KEEP)

    # ... but not beside a per-track list, whether that list is given or null.
    for body in ({"keep": KEEP, "video": KEEP}, {"keep": KEEP, "video": None}, {"keep": KEEP, "music": None}):
        r = client.put(f"/api/projects/{pid}/edit", json=body)
        assert r.status_code == 400 and "not both" in r.json()["detail"], body
    assert store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}, "narration": {"keep": KEEP}}, "refused: nothing written"

    # Both tracks null is "both whole again", not a refusal: with no music
    # stored there is nothing left to describe, so the key goes entirely -
    # which is what the client sends when the last cut is undone.
    r = client.put(f"/api/projects/{pid}/edit", json={"video": None, "narration": None})
    assert r.status_code == 200 and r.json() == _payload(None, None)
    assert "edit" not in store.get_project(pid), "an edit that is no edit is removed, as DELETE removes it"


def test_a_put_merges_into_the_stored_edit_and_keeps_the_keys_it_does_not_own(client):
    """``stored_tracks`` lets a key this version does not read ride through
    on read; the write must not drop it. Only the keys the BODY NAMES are
    this call's to write - the others, the music included, are left exactly
    as they were - and a version-1 ``keep`` never survives under 2."""
    pid = _video()
    record = store.get_project(pid)
    clip = {"id": "m1", "file": "bed.mp3", "at": 1.0, "in": 0.0, "out": 2.0, "gain": 0.2, "fade_in": 0.0, "fade_out": 0.0}
    record["edit"] = {"version": 2, "video": {"keep": KEEP}, "music": [clip]}
    store.save_project(record)

    assert _put_tracks(client, pid, narration=NARRATION).status_code == 200
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION}, "music": [clip],
    }, "the music survives, and so does the video track the body did not name"
    # Read back with the library's verdict on its file - not in this (empty)
    # library, so flagged rather than refused (trap 25).
    assert _get(client, pid).json() == _payload(KEEP, NARRATION, music=[{**clip, "file_duration": None, "missing": True}])

    # A version-1 record is both tracks cut together, so it is rewritten in
    # the version-2 shape rather than dropped by a PUT that names one track.
    record = store.get_project(pid)
    record["edit"] = {"version": 1, "keep": KEEP, "music": []}
    store.save_project(record)
    assert _put_tracks(client, pid, video=[[0.0, 12.0]]).status_code == 200
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": [[0.0, 12.0]]}, "narration": {"keep": KEEP}, "music": [],
    }, "the v1 list became the narration's; the video's was replaced"


def test_a_bad_track_is_a_400_that_names_the_track_and_the_range_and_stores_nothing(client):
    pid = _video()
    r = _put_tracks(client, pid, video=KEEP, narration=[[0.0, 5.0], [4.0, 8.0]])
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("narration: Range 2 [4.000, 8.000] overlaps or precedes range 1")
    assert "edit" not in store.get_project(pid)
    r = _put_tracks(client, pid, video=[], narration=NARRATION)
    assert r.status_code == 400 and r.json()["detail"].startswith("video: Keep at least one range")
    assert "edit" not in store.get_project(pid)
    # The rest of E1's answers are unchanged.
    assert _put_tracks(client, "aabbccddeeff", video=KEEP).status_code == 404
    assert _put_tracks(client, _video(audio_seconds=None), narration=NARRATION).status_code == 409


def test_the_audit_summary_is_per_track_and_never_carries_times(client):
    """The summary describes the edit as it now STANDS, not the keys the
    body happened to name."""
    pid = _video()
    assert _put_tracks(client, pid, video=KEEP).status_code == 200
    assert _put_tracks(client, pid, narration=[[0.5, 12.0]]).status_code == 200
    assert _put_tracks(client, pid, video=None).status_code == 200
    assert _put_tracks(client, pid, video=[[0.0, 6.0], [6.0, 12.0]], narration=NARRATION).status_code == 200
    assert [row["detail"] for row in _edit_rows()] == [
        "video: 2 ranges kept, 0.0 s removed; narration: 2 ranges kept, 0.6 s removed",
        "video: whole; narration: 1 range kept, 0.5 s removed",
        "video: 2 ranges kept, 1.5 s removed; narration: 1 range kept, 0.5 s removed",
        "video: 2 ranges kept, 1.5 s removed; narration: whole",
    ]
    assert not any("7.5" in row["detail"] or "6.4" in row["detail"] for row in _edit_rows())


def test_a_body_that_names_nothing_changes_nothing_and_records_nothing(client, monkeypatch):
    """``{}`` is a 200 that answers with the edit as it stands - nothing
    written, nothing forgotten, no audit row - exactly as a ``DELETE`` with
    nothing to clear is. It was a 400, which made an empty commit an error
    the client had to special-case."""
    pid = _video()
    assert _put_tracks(client, pid, video=KEEP).status_code == 200
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    monkeypatch.setattr(store, "save_project", lambda record: pytest.fail("nothing to write, and it wrote"))

    for body in ({}, {"keep": None}):
        r = client.put(f"/api/projects/{pid}/edit", json=body)
        assert r.status_code == 200, (body, r.text)
        assert r.json() == _payload(KEEP, None), body
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before
    assert [row["detail"] for row in _edit_rows()] == ["video: 2 ranges kept, 1.5 s removed; narration: whole"], (
        "one row, for the one PUT that changed something"
    )
    stored, changed = edit.set_edit(pid)
    assert changed is False and stored == _payload(KEEP, None), "the service answers the same way"


def test_the_plan_projects_only_through_the_narration_list(client):
    """``listed`` follows ``projected``, never ``cut``: a video-only edit
    projects nothing, a narration-only edit projects everything, and the
    picture's length is what bounds the narration's timeline either way."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/4", json={"offset": 1.0}).status_code == 200

    # Video only: the picture is cut, and every sentence stays where it was
    # spoken - the third included, spoken over the removed picture (it plays
    # over what follows: trap 19). The fifth, aimed at 11.0 s, now sits past
    # the 10.5 s picture, where the mux drops it: `past_end` (trap 11).
    assert _put_tracks(client, pid, video=KEEP).status_code == 200
    plan = _plan(client, pid)
    assert plan["duration"] == 10.5 and plan["edit"] == _payload(KEEP, None)
    assert [s["index"] for s in plan["sentences"]] == [0, 1, 2, 3, 4], "nothing dropped: the narration is locked"
    assert [(s["start"], s["end"]) for s in plan["sentences"]] == [(s["start"], s["end"]) for s in SEGMENTS]
    assert [s["pinned_start"] for s in plan["sentences"]] == [0.0, 5.0, 6.5, 8.0, 11.0]
    assert plan["sentences"][4]["past_end"] is True and plan["sentences"][4]["speakable"] is False

    # Narration only: the picture is whole and 12 s long; the sentences move.
    # (The video list is cleared explicitly - a key the body does not name is
    # left as it is.)
    assert _put_tracks(client, pid, video=None, narration=NARRATION).status_code == 200
    plan = _plan(client, pid)
    assert plan["duration"] == 12.0 and plan["edit"] == _payload(None, NARRATION)
    assert [s["index"] for s in plan["sentences"]] == [0, 1, 3, 4]
    assert [(s["start"], s["end"]) for s in plan["sentences"]] == [(0.0, 2.0), (5.0, 6.4), (7.4, 8.4), (9.4, 11.4)]
    assert plan["sentences"][3]["pinned_start"] == pytest.approx(10.4), "to_timeline(start) + offset, once"
    assert plan["sentences"][3]["past_end"] is False

    # Both: the narration's timeline, bounded by the picture's length.
    assert _put_tracks(client, pid, video=KEEP, narration=NARRATION).status_code == 200
    plan = _plan(client, pid)
    assert plan["duration"] == 10.5 and plan["edit"] == _payload(KEEP, NARRATION)
    assert [(s["index"], s["start"]) for s in plan["sentences"]] == [(0, 0.0), (1, 5.0), (3, 7.4), (4, 9.4)]
    assert plan["sentences"][3]["pinned_start"] == pytest.approx(10.4)
    assert plan["sentences"][3]["past_end"] is False, "10.4 is inside the 10.5 s picture"
    assert plan["sentences"][3]["window"] == pytest.approx(1.0), "to its own end, 11.4, which lies past the picture"


# ── the music lane (E4a): clips on the output axis ────────────────────────────

# The library as the edit sees it: name -> recorded length. No file on disk is
# needed to validate a clip list, only the index's lengths.
LIBRARY = {"bed.mp3": 30.0, "sting.wav": 4.5}


def _clip(**over) -> dict:
    """A valid clip on ``bed.mp3``; ``over`` replaces fields, and a value of
    ``...`` removes one. ``in`` is a keyword, so it is spelled by unpacking:
    ``{**_clip(), "in": 5.0}``."""
    clip = {"id": "m1", "file": "bed.mp3", "at": 1.0, "in": 0.0, "out": 10.0,
            "gain": 0.15, "fade_in": 1.0, "fade_out": 2.0}
    for key, value in over.items():
        if value is ...:
            clip.pop(key)
        else:
            clip[key] = value
    return clip


def _read_back(clip: dict, library: dict = LIBRARY) -> dict:
    """A stored clip as the routes and the plan report it."""
    duration = library.get(clip["file"])
    return {**clip, "file_duration": duration, "missing": duration is None}


@pytest.fixture
def library(monkeypatch):
    """The music library's index as the edit reads it, with no file on disk."""
    monkeypatch.setattr(music, "library", lambda: dict(LIBRARY))
    return dict(LIBRARY)


CLIP_A = _clip(id="a", file="sting.wav", at=0.5, out=4.5, fade_in=0.5, fade_out=0.5)
CLIP_B = _clip(id="b", at=12.5, out=20.0)


def test_valid_clips_come_back_rounded_typed_and_sorted_by_at():
    late = {**_clip(id="late"), "at": 20.0004, "in": 5.0, "out": 29.9996, "gain": 1, "fade_in": 0, "fade_out": 0}
    early = {**_clip(id="early", file="sting.wav"), "at": 0, "out": 4.5, "fade_in": 0.5, "fade_out": 0.5}
    tied = {**_clip(id="tied"), "at": -0.0004, "out": 1, "fade_in": 0, "fade_out": 0}
    checked = edit.validate_music([late, early, tied], LIBRARY)
    assert [c["id"] for c in checked] == ["early", "tied", "late"], "sorted by at; equal starts keep their order"
    assert checked[2] == {"id": "late", "file": "bed.mp3", "at": 20.0, "in": 5.0, "out": 30.0,
                          "gain": 1.0, "fade_in": 0.0, "fade_out": 0.0}, "rounded to 3 dp, ints as floats"
    assert all(isinstance(v, float) for c in checked for k, v in c.items() if k not in ("id", "file"))
    assert str(checked[1]["at"]) == "0.0", "a hair under zero is zero, never -0.0"
    assert edit.validate_music([], LIBRARY) == [], "an empty list is no music, and is allowed"
    # The list handed in is not touched.
    assert late["at"] == 20.0004


def test_the_edges_of_every_bound_are_allowed():
    ten = _clip(fade_in=4.0, fade_out=6.0)                      # the fades fill the clip exactly
    to_the_end = {**_clip(id="e"), "in": 20.0, "out": 30.0}     # out at the file's end
    rounds_in = {**_clip(id="r"), "out": 30.0004}               # rounds to the end
    shortest = {**_clip(id="s", fade_in=0.05, fade_out=0.05), "in": 0.1, "out": 0.2}  # exactly 0.1 s
    silent = _clip(id="z", gain=0)
    checked = edit.validate_music([ten, to_the_end, rounds_in, shortest, silent], LIBRARY)
    assert [c["out"] for c in checked] == [10.0, 30.0, 30.0, 0.2, 10.0]


@pytest.mark.parametrize("clips, message", [
    ("nope", "The music must be a list of clips"),
    ({"id": "m1"}, "The music must be a list of clips"),
    ([1], "music clip 1 must be an object with id, file, at, in, out, gain, fade_in, fade_out"),
    ([_clip(gain=...)], "music clip 1 (m1): missing gain; a clip has exactly id, file, at, in, out, gain, fade_in, fade_out"),
    ([_clip(extra=1)], "music clip 1 (m1): unknown extra; a clip has exactly"),
    ([_clip(gain=..., extra=1)], "music clip 1 (m1): missing gain and unknown extra"),
    ([_clip(id="")], "music clip 1: id must be 1-32 characters of a-z, 0-9, _ or -"),
    ([_clip(id="Bad ID")], "music clip 1: id must be"),
    ([_clip(id="x" * 33)], "music clip 1: id must be"),
    ([_clip(id=7)], "music clip 1: id must be"),
    ([_clip(), _clip(at=2.0)], "music clip 2 (m1): id 'm1' is already used by music clip 1"),
    ([_clip(id="a"), _clip(id="m3f9a1", file="x.mp3")], "music clip 2 (m3f9a1): file 'x.mp3' is not in the library"),
    ([_clip(file="")], "music clip 1 (m1): file must be the name of a library file"),
    ([_clip(file=3)], "file must be the name of a library file"),
    ([_clip(at=True)], "music clip 1 (m1): at must be a number"),
    ([_clip(at="1")], "at must be a number"),
    ([_clip(at=None)], "at must be a number"),
    ([_clip(gain=float("nan"))], "music clip 1 (m1): gain must be a finite number"),
    ([_clip(out=float("inf"))], "out must be a finite number"),
    ([_clip(at=int("1" + "0" * 400))], "at must be a finite number"),
    ([_clip(at=-0.5)], "music clip 1 (m1): at (-0.500) starts before 0"),
    ([{**_clip(), "in": -1.0}], "music clip 1 (m1): in (-1.000) starts before 0"),
    ([{**_clip(), "in": 10.0}], "music clip 1 (m1): out (10.000) must be after in (10.000)"),
    ([{**_clip(), "in": 11.0}], "must be after in"),
    ([{**_clip(fade_in=0, fade_out=0), "in": 5.0, "out": 5.05}], "music clip 1 (m1): the clip (0.050 s from in to out) is shorter than 0.1 s"),
    ([_clip(out=30.001)], "music clip 1 (m1): out (30.001) runs past the end of 'bed.mp3', which is 30.000 s long"),
    ([_clip(gain=-0.1)], "music clip 1 (m1): gain (-0.100) must be between 0 and 1"),
    ([_clip(gain=1.001)], "gain (1.001) must be between 0 and 1"),
    ([_clip(fade_in=-1)], "music clip 1 (m1): fade_in and fade_out must be 0 s or more"),
    ([_clip(fade_out=-0.001)], "fade_in and fade_out must be 0 s or more"),
    ([_clip(fade_in=5, fade_out=5.001)], "music clip 1 (m1): fade_in + fade_out (10.001 s) is longer than the clip (10.000 s)"),
])
def test_a_bad_clip_is_refused_by_position_id_and_field(clips, message):
    with pytest.raises(ValueError) as exc:
        edit.validate_music(clips, LIBRARY)
    assert message in str(exc.value), str(exc.value)


def test_a_clip_list_long_enough_to_be_an_attack_on_the_record_is_refused():
    many = [_clip(id=f"c{i}", at=float(i)) for i in range(edit.MAX_CLIPS + 1)]
    with pytest.raises(ValueError) as exc:
        edit.validate_music(many, LIBRARY)
    assert f"limited to {edit.MAX_CLIPS} clips" in str(exc.value)
    assert len(edit.validate_music(many[:-1], LIBRARY)) == edit.MAX_CLIPS


def test_stored_music_reads_absent_none_and_empty_as_no_music():
    for record in ({}, {"edit": None}, {"edit": {"version": 2}}, {"edit": {"version": 2, "music": None}},
                   {"edit": {"version": 2, "music": []}}, {"edit": {"version": 1, "keep": KEEP}}):
        assert edit.stored_music(record, LIBRARY) == [], record


def test_stored_music_reads_the_clips_back_with_their_files_lengths(library):
    record = {"edit": {"version": 2, "video": {"keep": KEEP}, "music": [CLIP_B, CLIP_A]}}
    assert edit.stored_music(record, LIBRARY) == [_read_back(CLIP_A), _read_back(CLIP_B)], "sorted, annotated"
    assert edit.stored_music(record) == edit.stored_music(record, LIBRARY), "no library given: the index is read"
    # The record's clips are not touched.
    assert "missing" not in record["edit"]["music"][0]


def test_a_file_that_left_the_library_is_flagged_on_read_never_refused():
    """Trap 25: the clip comes back marked, its slice unbounded (the file's
    length is unknown now); storing it again would be refused."""
    gone = _clip(file="gone.mp3", out=999.0)
    record = {"edit": {"version": 2, "music": [gone]}}
    (clip,) = edit.stored_music(record, LIBRARY)
    assert clip == {**gone, "file_duration": None, "missing": True}
    with pytest.raises(ValueError) as exc:
        edit.validate_music([gone], LIBRARY)
    assert "file 'gone.mp3' is not in the library" in str(exc.value)


def test_a_broken_clip_list_is_refused_on_read_naming_the_clip():
    """As an unreadable track list is (E1's rule): never a silent no-music."""
    with pytest.raises(ValueError) as exc:
        edit.stored_music({"edit": {"version": 2, "music": [_clip(gain="loud")]}}, LIBRARY)
    assert "This project's music cannot be read (music clip 1 (m1): gain must be a number.)" in str(exc.value)
    assert "clear the edit" in str(exc.value)
    with pytest.raises(ValueError):
        edit.stored_music({"edit": {"version": 2, "music": "bed.mp3"}}, LIBRARY)
    with pytest.raises(ValueError) as exc:
        edit.stored_music({"edit": "nope"}, LIBRARY)
    assert "different version" in str(exc.value)


def test_apply_carries_the_music_and_changes_nothing_else():
    transcript = [dict(s) for s in SEGMENTS]
    untouched = edit.apply({}, transcript, 12.0, LIBRARY)
    assert untouched.music == [] and untouched.sentences is transcript

    music_only = edit.apply({"edit": {"version": 2, "music": [CLIP_B, CLIP_A]}}, transcript, 12.0, LIBRARY)
    assert music_only.music == [_read_back(CLIP_A), _read_back(CLIP_B)]
    assert (music_only.cut, music_only.projected) == (False, False)
    assert music_only.sentences is transcript, "music is placed on the output; it projects nothing"
    assert (music_only.output_duration, music_only.narration_duration) == (12.0, 12.0)
    assert edit.apply({"edit": {"version": 2, "music": [CLIP_A]}}, transcript, None, LIBRARY).music == [_read_back(CLIP_A)], (
        "a music-only edit needs no source length"
    )

    both = edit.apply({"edit": {"version": 2, "video": {"keep": KEEP}, "music": [CLIP_A]}}, transcript, 12.0, LIBRARY)
    assert both.cut is True and both.music == [_read_back(CLIP_A)] and both.output_duration == 10.5
    # And a record whose tracks this version cannot read is still refused, music or not.
    with pytest.raises(ValueError):
        edit.apply({"edit": {"version": 3, "music": [CLIP_A]}}, transcript, 12.0, LIBRARY)


def test_set_edit_writes_only_the_keys_it_is_given_and_clears_on_none_or_empty(library):
    """One rule for all three: UNCHANGED (the default) leaves a key exactly
    as it is, ``None`` clears it - a track back to whole, the music gone -
    and a list replaces it. So a cut never drops the clips and a clip
    commit never drops the cut."""
    pid = _video()
    stored, changed = edit.set_edit(pid, music=[CLIP_B, CLIP_A])
    assert changed and stored == _payload(None, None, music=[_read_back(CLIP_A), _read_back(CLIP_B)]), (
        "a music-only edit is an edit"
    )
    assert store.get_project(pid)["edit"] == {"version": 2, "music": [CLIP_A, CLIP_B]}, "sorted; nothing derived is stored"

    edit.set_edit(pid, video=KEEP)
    assert store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}, "music": [CLIP_A, CLIP_B]}, (
        "UNCHANGED: a cut leaves the clips exactly as they were"
    )
    stored, _ = edit.set_edit(pid, narration=NARRATION)
    assert stored["music"] == [_read_back(CLIP_A), _read_back(CLIP_B)], "and answers with them"
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION}, "music": [CLIP_A, CLIP_B],
    }, "and the video list it was not given"

    edit.set_edit(pid, music=[CLIP_A])
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION}, "music": [CLIP_A],
    }, "a music-only call leaves both track lists alone"

    stored, _ = edit.set_edit(pid, video=None, narration=None)
    assert stored == _payload(None, None, music=[_read_back(CLIP_A)]), "null: the tracks are whole, the music kept"
    assert store.get_project(pid)["edit"] == {"version": 2, "music": [CLIP_A]}

    edit.set_edit(pid, video=KEEP, music=[])
    assert store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}}, "[] clears"
    edit.set_edit(pid, music=[CLIP_A])
    edit.set_edit(pid, narration=NARRATION, music=None)
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION},
    }, "None clears"

    stored, changed = edit.set_edit(pid)
    assert changed is False, "nothing given: a no-op, not a refusal"
    assert stored == _payload(KEEP, NARRATION)

    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    with pytest.raises(ValueError) as exc:
        edit.set_edit(pid, video=KEEP, music=[_clip(file="x.mp3")])
    assert "file 'x.mp3' is not in the library" in str(exc.value)
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before, "validated before the lock: nothing written"


def test_an_edit_that_is_no_edit_is_removed_rather_than_stored(library):
    """``{"music": []}`` on a project that has none must not leave
    ``{"version": 2}`` behind: a record with an edit that says nothing reads
    as edited everywhere it is looked at, and ``clear_edit`` would then
    report it as cleared."""
    pid = _video()
    stored, changed = edit.set_edit(pid, music=[])
    assert changed and stored == _payload(None, None)
    assert "edit" not in store.get_project(pid)

    edit.set_edit(pid, video=KEEP, music=[CLIP_A])
    edit.set_edit(pid, video=None, music=None)
    assert "edit" not in store.get_project(pid), "the last key cleared takes the edit with it"

    # A key this version does not read rides through only while a track or
    # the music is still there to describe.
    record = store.get_project(pid)
    record["edit"] = {"version": 2, "video": {"keep": KEEP}, "captions": {"style": "bold"}}
    store.save_project(record)
    edit.set_edit(pid, narration=NARRATION)
    assert store.get_project(pid)["edit"]["captions"] == {"style": "bold"}
    edit.set_edit(pid, video=None, narration=None)
    assert "edit" not in store.get_project(pid)


def test_a_corrupt_stored_clip_list_refuses_a_cut_before_it_is_written(library):
    """The read-back is built INSIDE the lock and before ``save_project``:
    a hand-edited record whose music cannot be read refuses the call rather
    than landing the cut and then raising on the way out with the write
    already done."""
    pid = _video()
    record = store.get_project(pid)
    record["edit"] = {"version": 2, "music": [{"id": "x"}]}
    store.save_project(record)
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()

    with pytest.raises(ValueError) as exc:
        edit.set_edit(pid, video=KEEP)
    assert "This project's music cannot be read" in str(exc.value)
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before, "the cut was not written"

    # The same for a stored TRACK list this version cannot read...
    record["edit"] = {"version": 2, "narration": {"keep": [[5.0, 1.0]]}}
    store.save_project(record)
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    with pytest.raises(ValueError) as exc:
        edit.set_edit(pid, music=[CLIP_A])
    assert "must end after it starts" in str(exc.value)
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before
    # ... and replacing the unreadable list is still the way out.
    edit.set_edit(pid, narration=NARRATION, music=[CLIP_A])
    assert store.get_project(pid)["edit"] == {"version": 2, "narration": {"keep": NARRATION}, "music": [CLIP_A]}


def test_a_music_only_edit_needs_no_extracted_audio(library):
    """The clips are measured against the library, not the source; a track
    list still needs the audio's length."""
    pid = _video(audio_seconds=None)
    stored, _ = edit.set_edit(pid, music=[CLIP_A])
    assert stored == _payload(None, None, source=None, music=[_read_back(CLIP_A)])
    with pytest.raises(edit.SourceLengthUnknown):
        edit.set_edit(pid, video=KEEP)


def test_clear_edit_removes_the_music_with_the_tracks(library):
    pid = _video()
    edit.set_edit(pid, video=KEEP, music=[CLIP_A])
    cleared, had = edit.clear_edit(pid)
    assert had and cleared == _payload(None, None) and "edit" not in store.get_project(pid)


# the routes and the plan

def test_put_stores_the_music_and_the_get_and_the_plan_carry_it(client, library):
    pid = _video()
    r = client.put(f"/api/projects/{pid}/edit", json={"video": KEEP, "music": [CLIP_B, CLIP_A]})
    assert r.status_code == 200, r.text
    expected = _payload(KEEP, None, music=[_read_back(CLIP_A), _read_back(CLIP_B)])
    assert r.json() == expected
    assert _get(client, pid).json() == expected
    assert _plan(client, pid)["edit"] == expected, "the plan's edit block carries the clips the lane draws"
    assert store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}, "music": [CLIP_A, CLIP_B]}
    assert [row["detail"] for row in _edit_rows()] == [
        "video: 2 ranges kept, 1.5 s removed; narration: whole; music: 2 clips",
    ]
    assert not any("sting" in row["detail"] or "12.5" in row["detail"] for row in _edit_rows()), "never a file or a position"


def test_a_key_the_put_does_not_name_is_left_exactly_as_it_was(client, library):
    """One rule for the two tracks and the music alike (trap 32): E3's
    client sends its tracks and no ``music``, and E4b's lane sends ``music``
    and no tracks - neither may drop what it did not send."""
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_A]}).status_code == 200
    assert store.get_project(pid)["edit"] == {"version": 2, "music": [CLIP_A]}, "a music-only body is allowed"

    assert _put_tracks(client, pid, video=KEEP).status_code == 200
    assert store.get_project(pid)["edit"]["music"] == [CLIP_A], "a per-track cut keeps it"
    assert _put(client, pid, KEEP).status_code == 200
    assert store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}, "narration": {"keep": KEEP}, "music": [CLIP_A]}, (
        "so does the version-1 body"
    )
    assert _get(client, pid).json()["music"] == [_read_back(CLIP_A)]

    # And the other way round: a music-only commit keeps BOTH track lists,
    # which is what E4b sends on every clip add, move, gain and fade change.
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_B]}).status_code == 200
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": KEEP}, "music": [CLIP_B],
    }, "the picture's cut survives a clip commit"

    r = client.put(f"/api/projects/{pid}/edit", json={"narration": NARRATION, "music": None})
    assert r.status_code == 200 and r.json()["music"] == []
    assert store.get_project(pid)["edit"] == {
        "version": 2, "video": {"keep": KEEP}, "narration": {"keep": NARRATION},
    }, "null clears the music; the video list is not this body's business"
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_A]}).status_code == 200
    r = client.put(f"/api/projects/{pid}/edit", json={"narration": None, "music": []})
    assert r.status_code == 200 and store.get_project(pid)["edit"] == {"version": 2, "video": {"keep": KEEP}}, "[] clears"
    r = client.put(f"/api/projects/{pid}/edit", json={"video": None})
    assert r.status_code == 200 and "edit" not in store.get_project(pid), "the last key cleared takes the edit with it"

    details = [row["detail"] for row in _edit_rows()]  # newest first
    assert details[-1] == "video: whole; narration: whole; music: 1 clip"
    assert details[0] == "video: whole; narration: whole", "no clips, no music part"


def test_a_music_only_put_on_an_unedited_project_stores_nothing_when_it_clears(client, library):
    """``{"music": []}`` where there is no music writes an edit that is no
    edit - ``{"version": 2}`` - unless the empty merge removes the key."""
    pid = _video()
    r = client.put(f"/api/projects/{pid}/edit", json={"music": []})
    assert r.status_code == 200 and r.json() == _payload(None, None)
    assert "edit" not in store.get_project(pid)
    assert [row["detail"] for row in _edit_rows()] == ["video: whole; narration: whole"]
    # And the GET agrees: a project that reads as one that never had an edit.
    assert _get(client, pid).json() == _payload(None, None)


def test_keep_beside_music_is_a_400_like_keep_beside_a_track(client, library):
    pid = _video()
    for body in ({"keep": KEEP, "music": [CLIP_A]}, {"keep": KEEP, "music": None}):
        r = client.put(f"/api/projects/{pid}/edit", json=body)
        assert r.status_code == 400 and "not both" in r.json()["detail"], body
    assert "edit" not in store.get_project(pid)


def test_a_clip_body_is_strict(client, library):
    """``extra="forbid"`` and strict numbers on every clip; the JSON key is
    ``in`` and nothing else; NaN reaches the validator and is its 400."""
    pid = _video()
    bad = [
        {"music": [{**CLIP_A, "extra": 1}]},
        {"music": [{k: v for k, v in CLIP_A.items() if k != "in"} | {"in_": 0.0}]},
        {"music": [{k: v for k, v in CLIP_A.items() if k != "gain"}]},
        {"music": [{**CLIP_A, "gain": True}]},
        {"music": [{**CLIP_A, "at": "1"}]},
        {"music": [{**CLIP_A, "id": 7}]},
        {"music": "nope"},
        {"music": [1]},
    ]
    for body in bad:
        assert client.put(f"/api/projects/{pid}/edit", json=body).status_code == 422, body
    nan = ('{"music": [{"id": "a", "file": "bed.mp3", "at": NaN, "in": 0, "out": 1, '
           '"gain": 0.1, "fade_in": 0, "fade_out": 0}]}')
    r = client.put(f"/api/projects/{pid}/edit", content=nan, headers={"content-type": "application/json"})
    assert r.status_code == 400 and "at must be a finite number" in r.json()["detail"]
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [{**CLIP_A, "at": 0, "gain": 1}]}).status_code == 200, "ints are fine"
    assert store.get_project(pid)["edit"]["music"] == [{**CLIP_A, "at": 0.0, "gain": 1.0}]


def test_a_clip_naming_a_file_not_in_the_library_is_a_400_naming_the_clip(client, library):
    pid = _video()
    r = client.put(f"/api/projects/{pid}/edit", json={"video": KEEP, "music": [CLIP_A, _clip(id="m3f9a1", file="x.mp3")]})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "music clip 2 (m3f9a1): file 'x.mp3' is not in the library."
    assert "edit" not in store.get_project(pid), "nothing stored, the video list included"
    r = client.put(f"/api/projects/{pid}/edit", json={"music": [_clip(out=31.0)]})
    assert r.status_code == 400 and "runs past the end of 'bed.mp3'" in r.json()["detail"]


def test_delete_clears_the_music_too(client, library):
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"video": KEEP, "music": [CLIP_A]}).status_code == 200
    r = client.delete(f"/api/projects/{pid}/edit")
    assert r.status_code == 200 and r.json() == _payload(None, None)
    assert "edit" not in store.get_project(pid)
    assert _get(client, pid).json()["music"] == []
    assert [row["detail"] for row in _edit_rows()] == ["cleared", "video: 2 ranges kept, 1.5 s removed; narration: whole; music: 1 clip"]


def test_a_file_that_left_the_library_is_reported_missing_by_the_get_and_the_plan(client, library, monkeypatch):
    pid = _video()
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_A, CLIP_B]}).status_code == 200
    monkeypatch.setattr(music, "library", lambda: {"bed.mp3": 30.0})  # sting.wav has gone

    gone = {**CLIP_A, "file_duration": None, "missing": True}
    assert _get(client, pid).json()["music"] == [gone, _read_back(CLIP_B)]
    assert _plan(client, pid)["edit"]["music"] == [gone, _read_back(CLIP_B)]
    # Sending the list back as it is - the missing clip included - is refused
    # (checked to exist at write time); dropping the clip is accepted.
    r = client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_A, CLIP_B]})
    assert r.status_code == 400 and "file 'sting.wav' is not in the library" in r.json()["detail"]
    assert client.put(f"/api/projects/{pid}/edit", json={"music": [CLIP_B]}).status_code == 200
    assert _get(client, pid).json()["music"] == [_read_back(CLIP_B)]
