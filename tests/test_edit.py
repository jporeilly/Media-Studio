"""The edit timeline, phase E1: the model and the render, no UI.

A project gains ``record["edit"] = {"version": 1, "keep": [[start, end], ...]}``
- the ranges of its ONE source that survive the cut, in source seconds - and
nothing else. **The transcript never moves**: ``set_transcript`` identifies a
sentence by its window, so shifting sentences after a cut would drop every
adjustment on every later sentence. The edit is applied as a PROJECTION
(``services.edit.project_transcript``) at plan time and at render time, by the
same function, so the two cannot disagree.

Three things these tests hold in place:

- the arithmetic (``validate_keep``, ``to_timeline`` / ``to_source``,
  ``project_transcript``, ``whole_source``) is exhaustively pinned, because a
  client copy of it lands in E2 and shares these fixtures;
- the three routes take the store's lock, refuse a busy project, audit with
  counts and never times, and leave the transcript byte-identical;
- the audition plan returns the projected sentences - in timeline seconds,
  each still addressed by its index in the STORED transcript - and says which
  edit it projected them through.
"""

import json
import threading
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import edit, jobs, narration, processing
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
    return client.put(f"/api/projects/{pid}/edit", json={"keep": keep})


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
    assert "edit.apply(" in inspect.getsource(narration.plan)
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
        edit.apply({"edit": {"version": 2, "keep": KEEP}}, transcript, 12.0)
    assert "different version" in str(exc.value)


# ── the routes ────────────────────────────────────────────────────────────────

def test_a_project_with_no_edit_keeps_everything(client):
    pid = _video()
    r = _get(client, pid)
    assert r.status_code == 200, r.text
    assert r.json() == {"version": 1, "keep": None, "source_duration": 12.0, "output_duration": 12.0}


def test_without_extracted_audio_there_is_no_length_to_report(client):
    """Neither length is known, and neither is claimed: null, not 0.0."""
    pid = _video(audio_seconds=None)
    assert _get(client, pid).json() == {"version": 1, "keep": None, "source_duration": None, "output_duration": None}


def test_put_stores_the_ranges_and_get_reads_them_back(client):
    pid = _video()
    r = _put(client, pid, [[0, 6], [7.5, 12]])
    assert r.status_code == 200, r.text
    assert r.json() == {"version": 1, "keep": [[0.0, 6.0], [7.5, 12.0]], "source_duration": 12.0, "output_duration": 10.5}
    assert _get(client, pid).json() == r.json()
    assert store.get_project(pid)["edit"] == {"version": 1, "keep": [[0.0, 6.0], [7.5, 12.0]]}

    # A second PUT replaces the whole list - it is small, and a partial PATCH buys nothing.
    assert _put(client, pid, [[1.0, 2.0]]).status_code == 200
    assert store.get_project(pid)["edit"]["keep"] == [[1.0, 2.0]]


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
    assert _put(client, pid, [[0.00049, 5.99951], [7.5004, 12]]).json()["keep"] == [[0.0, 6.0], [7.5, 12.0]]


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
    assert client.put(f"/api/projects/{pid}/edit", json={}).status_code == 422
    assert _put(client, pid, [[True, 6.0]]).status_code == 422
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
    assert r.json() == {"version": 1, "keep": None, "source_duration": 12.0, "output_duration": 12.0}
    assert "edit" not in store.get_project(pid), "removed, not written empty: the record reads as one that never had an edit"
    assert [row["detail"] for row in _edit_rows()] == ["cleared", "2 ranges kept, 1.5 s removed"]

    # Clearing what is already clear is a 200 that changes nothing and records
    # nothing - there was nothing to clear.
    before = (store.PROJECTS_DIR / pid / "project.json").read_bytes()
    r = client.delete(f"/api/projects/{pid}/edit")
    assert r.status_code == 200 and r.json()["keep"] is None
    assert (store.PROJECTS_DIR / pid / "project.json").read_bytes() == before
    assert [row["detail"] for row in _edit_rows()] == ["cleared", "2 ranges kept, 1.5 s removed"], "no row for a no-op"


def test_every_write_is_audited_with_counts_and_never_times(client):
    """"Who cut this" is answerable; where they cut is not in the log, and
    neither is any text."""
    pid = _video()
    assert _put(client, pid, KEEP).status_code == 200
    assert _put(client, pid, [[0.0, 6.0]]).status_code == 200
    assert client.delete(f"/api/projects/{pid}/edit").status_code == 200
    assert _get(client, pid).status_code == 200

    rows = _edit_rows()
    assert [row["detail"] for row in rows] == ["cleared", "1 range kept, 6.0 s removed", "2 ranges kept, 1.5 s removed"]
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
                edit.set_edit(pid, KEEP)
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
        assert saved.get("edit", {}).get("keep") == KEEP, f"run {attempt}: the edit was lost"


def test_an_edit_this_version_cannot_read_is_refused_rather_than_ignored(client):
    """Rendering the whole video while the user believes a cut is applied is
    the one thing that must not happen quietly."""
    pid = _video()
    record = store.get_project(pid)
    record["edit"] = {"version": 2, "keep": KEEP}
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

    assert _get(client, pid).json() == {"version": 1, "keep": KEEP, "source_duration": None, "output_duration": 10.5}
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
    assert plan["edit"] == {"version": 1, "keep": KEEP, "source_duration": 12.0, "output_duration": 10.5}
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
    assert untouched["edit"] == {"version": 1, "keep": None, "source_duration": 12.0, "output_duration": 12.0}

    assert _put(client, pid, [[0.0, 6.0], [6.0, 12.0]]).status_code == 200
    split = _plan(client, pid)
    assert split["sentences"] == untouched["sentences"]
    assert split["duration"] == untouched["duration"] == 12.0
    assert split["edit"]["keep"] == [[0.0, 6.0], [6.0, 12.0]], "so the timeline can draw the boundary"


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
