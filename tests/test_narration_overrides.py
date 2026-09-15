"""Per-sentence narration overrides (narration timeline, phase 1).

Re-voicing pins every Whisper sentence to the moment it was spoken and turns the
leftover time back into silence, so a narrator who spoke more slowly than the
synthetic voice gets a re-voice full of gaps. Nothing here guesses a better
rate: the user nudges the one sentence that is wrong, and these are the route,
the validation and the merge rule that let them.

The hazard this feature introduces is in the last section: the whole-list
transcript Save posts every segment back, and until ``TranscriptSegment``
forbade unknown keys that Save would have silently deleted every adjustment on
the project. That is nailed down here, not left to a code reading.
"""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, narration
from services import projects as store
from utils.config import config

SEGMENTS = [
    {"start": 0.0, "end": 2.0, "text": "First sentence."},
    {"start": 5.0, "end": 7.0, "text": "Second sentence."},
    {"start": 10.0, "end": 12.0, "text": "Third sentence."},
]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    # The per-pid locks are process-wide and belong to the store (they guard the
    # outer project.json, which several features write); a fresh dict per test
    # keeps one test's project id from sharing a lock with another's.
    monkeypatch.setattr(store, "_locks", {})


@pytest.fixture
def client():
    from api.app import app

    with TestClient(app) as c:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        yield c


def _video(segments=None) -> str:
    """A transcribed video project."""
    rec = store.import_upload("clip.mp4", b"video-bytes")
    store.set_transcript(rec["id"], [dict(s) for s in (SEGMENTS if segments is None else segments)])
    return rec["id"]


def _transcript(pid: str) -> list[dict]:
    return store.get_project(pid)["transcript"]


# ── the route ────────────────────────────────────────────────────────────────

def test_an_offset_and_a_mute_are_saved_on_the_sentence_itself(client):
    """On the segment, not in a map keyed by index: a re-transcribe then wipes
    the adjustments along with the sentences they belonged to - visible loss -
    rather than silently applying them to different sentences."""
    pid = _video()

    r = client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4})
    assert r.status_code == 200, r.text
    assert r.json()["offset"] == -0.4
    assert r.json()["text"] == "Second sentence.", "the sentence comes back as it now stands"

    assert client.patch(f"/api/projects/{pid}/transcript/2", json={"muted": True}).status_code == 200

    saved = _transcript(pid)
    assert saved[0] == SEGMENTS[0], "an untouched sentence gains no keys at all"
    assert saved[1]["offset"] == -0.4 and "muted" not in saved[1]
    assert saved[2]["muted"] is True and "offset" not in saved[2]


def test_a_field_left_out_is_left_alone_and_an_explicit_null_clears_it(client):
    """Exactly as SlideUpdate behaves."""
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 1.5, "muted": True})

    # Another field entirely: the offset and the mute stay.
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"speed": 1.2}).status_code == 200
    seg = _transcript(pid)[0]
    assert (seg["offset"], seg["muted"], seg["speed"]) == (1.5, True, 1.2)

    # Explicit nulls clear them back to the defaults, key and all.
    r = client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": None, "muted": None, "speed": None})
    assert r.status_code == 200, r.text
    assert _transcript(pid)[0] == SEGMENTS[0], "cleared means the keys are gone, not written as nulls"


def test_muted_false_clears_the_key_rather_than_writing_it(client):
    """Absent means the default, so an unmute leaves the segment as an older
    project's would be - there is no migration and none is wanted."""
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": True})
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"muted": False}).status_code == 200
    assert _transcript(pid)[0] == SEGMENTS[0]


def test_a_voice_is_stored_with_the_provider_it_belongs_to(client):
    pid = _video()
    r = client.patch(f"/api/projects/{pid}/transcript/0", json={"voice": "en-GB-RyanNeural"})
    assert r.status_code == 200, r.text
    seg = _transcript(pid)[0]
    assert seg["voice"] == "en-GB-RyanNeural"
    assert seg["provider"] == "edge_tts", "the provider travels with the voice, never alone"

    # Clearing the voice clears the provider with it.
    assert client.patch(f"/api/projects/{pid}/transcript/0", json={"voice": None}).status_code == 200
    assert _transcript(pid)[0] == SEGMENTS[0]


def test_a_voice_from_the_other_provider_is_refused_now_not_at_render_time(client):
    pid = _video()
    r = client.patch(f"/api/projects/{pid}/transcript/0", json={"voice": "en-US-AriaNeural", "provider": "kokoro"})
    assert r.status_code == 400
    assert "looks like an Edge TTS voice" in r.json()["detail"]
    assert _transcript(pid)[0] == SEGMENTS[0], "nothing was written"


def test_out_of_range_values_are_refused_by_the_schema(client):
    pid = _video()
    for body in (
        {"offset": 301.0},
        {"offset": -301.0},
        {"speed": 0.4},
        {"speed": 2.1},
        {"offset": True},  # strict: a boolean is not coerced to 1.0
        {"speed": True},
        {"nudge": 1.0},  # extra="forbid"
    ):
        assert client.patch(f"/api/projects/{pid}/transcript/0", json=body).status_code == 422, body
    assert _transcript(pid) == SEGMENTS


def test_an_integer_offset_is_still_accepted(client):
    """Strict floats refuse a bool, not an int - the same rule SlideUpdate's
    pause_override follows, and JSON has no way to spell 2.0 as a float."""
    pid = _video()
    r = client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 2})
    assert r.status_code == 200, r.text
    assert _transcript(pid)[0]["offset"] == 2.0


def test_an_index_outside_the_transcript_is_a_400_naming_the_range(client):
    pid = _video()
    r = client.patch(f"/api/projects/{pid}/transcript/9", json={"offset": 1.0})
    assert r.status_code == 400
    assert "0 to 2" in r.json()["detail"], r.json()

    untranscribed = store.import_upload("other.mp4", b"video-bytes")["id"]
    r = client.patch(f"/api/projects/{untranscribed}/transcript/0", json={"offset": 1.0})
    assert r.status_code == 400 and "transcribe it first" in r.json()["detail"]


def test_only_a_video_project_has_a_transcript_to_adjust(client):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    r = client.patch(f"/api/projects/{deck}/transcript/0", json={"offset": 1.0})
    assert r.status_code == 400 and "Only video projects" in r.json()["detail"]


def test_a_missing_project_is_a_404(client):
    assert client.patch("/api/projects/aabbccddeeff/transcript/0", json={"offset": 1.0}).status_code == 404


def test_an_adjustment_is_refused_while_a_job_holds_the_project(client, monkeypatch):
    """Every slide write takes ``jobs.require_idle`` and this one must too: a
    re-voice job has already read the transcript it would be adjusting."""
    pid = _video()
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "revoice", "status": "running"} if project_id == pid else None,
    )
    r = client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 1.0})
    assert r.status_code == 409, r.text
    assert "revoice" in r.json()["detail"]


def test_the_adjustment_is_audited_by_field_name_and_never_by_sentence(client):
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4, "muted": True})

    rows = [r for r in auth_store.list_audit(limit=50) if r["action"] == "project.transcript_timing"]
    assert len(rows) == 1, rows
    assert rows[0]["entity"] == "project" and rows[0]["entity_id"] == pid
    assert rows[0]["detail"] == "segment 1: muted, offset"
    assert "Second sentence" not in (rows[0]["detail"] or ""), "the words never go in the log"


# ── the service ──────────────────────────────────────────────────────────────

def test_the_service_refuses_a_bad_value_before_it_writes_anything():
    pid = _video()
    with pytest.raises(ValueError):
        narration.update_segment(pid, 0, offset=float("nan"))
    with pytest.raises(ValueError):
        narration.update_segment(pid, 0, speed=9.0)
    with pytest.raises(narration.ProjectNotFound):
        narration.update_segment("aabbccddeeff", 0, offset=1.0)
    assert _transcript(pid) == SEGMENTS


def test_segments_and_the_spoken_count_ignore_muted_and_empty_sentences():
    pid = _video()
    assert [s["text"] for s in narration.segments(pid)] == [s["text"] for s in SEGMENTS]
    assert narration.spoken_count(pid) == 3

    narration.update_segment(pid, 0, muted=True)
    narration.update_segment(pid, 1, muted=True)
    assert narration.spoken_count(pid) == 1
    assert narration.count_spoken([{"text": "   "}, {"text": "x", "muted": True}]) == 0


def test_deleting_a_project_releases_its_lock(tmp_path):
    """The per-pid lock has no reason to outlive the project - the same release
    ``services.slides.forget`` gets on delete."""
    pid = _video()
    narration.update_segment(pid, 0, offset=1.0)
    assert pid in store._locks

    assert store.delete_project(pid) is True
    assert pid not in store._locks


# ── the hazard: the whole-list Save must not destroy the adjustments ─────────

def test_saving_the_words_keeps_every_adjustment(client):
    """THE trap this feature introduces. ``ProjectDetail`` posts the WHOLE
    transcript on every Save and ``set_transcript`` writes it back wholesale, so
    without the index-wise merge each Save would quietly delete every offset and
    mute on the project."""
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4, "muted": True})
    client.patch(f"/api/projects/{pid}/transcript/2", json={"speed": 1.15, "voice": "en-GB-RyanNeural"})

    edited = [dict(s) for s in SEGMENTS]
    edited[0]["text"] = "First sentence, corrected."
    r = client.patch(f"/api/projects/{pid}/transcript", json={"transcript": edited})
    assert r.status_code == 200, r.text

    saved = _transcript(pid)
    assert saved[0]["text"] == "First sentence, corrected.", "the words are what a Save changes"
    assert saved[1]["offset"] == -0.4 and saved[1]["muted"] is True
    assert saved[2]["speed"] == 1.15 and saved[2]["voice"] == "en-GB-RyanNeural"
    assert saved[2]["provider"] == "edge_tts"


def test_a_save_landing_beside_an_adjustment_destroys_neither(client):
    """Both are read-modify-writes of the SAME file, so they take the same lock -
    which is why the lock lives in ``services.projects`` beside the file rather
    than in whichever feature needed one first. Without it the later writer wins
    on the whole record and silently discards what the other did, with both
    requests answering 200 and ``dropped`` reporting 0.

    The UI produces this pairing directly: an offset commits on blur, and the
    Save click follows it immediately.
    """
    pid = _video()
    words = [dict(s) for s in SEGMENTS]
    words[0]["text"] = "First sentence, corrected."

    for attempt in range(40):
        store.set_transcript(pid, [dict(s) for s in SEGMENTS])  # back to no adjustments
        start = threading.Barrier(2)
        failures: list[BaseException] = []

        def adjust():
            start.wait()
            try:
                narration.update_segment(pid, 2, offset=-0.4)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        def save():
            start.wait()
            try:
                store.set_transcript(pid, [dict(s) for s in words])
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)

        threads = [threading.Thread(target=adjust), threading.Thread(target=save)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert failures == [], failures
        saved = store.get_project(pid)
        assert saved is not None, f"run {attempt}: project.json was torn - get_project answers None"
        # Whichever landed second, both changes are on the record.
        assert saved["transcript"][2].get("offset") == -0.4, f"run {attempt}: the adjustment was lost"
        assert saved["transcript"][0]["text"] in (
            "First sentence.", "First sentence, corrected.",
        ), saved["transcript"][0]["text"]


def test_a_half_written_record_is_never_visible(tmp_path):
    """``save_project`` writes a temp file and renames it into place. A direct
    write tore under the collision above, and ``get_project`` answers None for a
    record it cannot parse - so a torn write made the project 404 forever with
    its video still on disk."""
    pid = _video()
    meta = store.PROJECTS_DIR / pid / "project.json"
    before = meta.read_text(encoding="utf-8")

    record = store.get_project(pid)
    record["name"] = "renamed"
    store.save_project(record)

    assert json.loads(meta.read_text(encoding="utf-8"))["name"] == "renamed"
    assert before != meta.read_text(encoding="utf-8")
    leftovers = [p.name for p in (store.PROJECTS_DIR / pid).iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], f"a temp file was left behind: {leftovers}"


def test_the_whole_list_save_cannot_move_a_sentence(client):
    """The list PATCH is a TEXT editor: an override key in its body is a 422
    rather than a silent drop (pydantic's default would have ignored it and the
    stripped list would have gone straight to disk)."""
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/0", json={"offset": 1.0})

    smuggled = [dict(s) for s in SEGMENTS]
    smuggled[0]["offset"] = 99.0
    r = client.patch(f"/api/projects/{pid}/transcript", json={"transcript": smuggled})
    assert r.status_code == 422, r.text
    assert _transcript(pid)[0]["offset"] == 1.0, "the stored adjustment is untouched"


def test_a_sentence_that_is_no_longer_in_the_list_loses_its_adjustment(client):
    """A sentence the Save does not contain any more takes its adjustment with
    it. A sentence that IS still there, at the same index and the same window,
    keeps its own - identity is the window, not the length of the list."""
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4})
    client.patch(f"/api/projects/{pid}/transcript/2", json={"muted": True})

    shorter = [dict(s) for s in SEGMENTS[:2]]
    record, dropped = store.set_transcript(pid, shorter)
    assert dropped == 1, "only the sentence that is gone lost its adjustment"
    assert record["transcript"][1]["offset"] == -0.4, "the sentence still in the list keeps its own"
    assert len(record["transcript"]) == 2

    rows = [r for r in auth_store.list_audit(limit=50) if r["action"] == "project.transcript_edit"]
    assert rows == [] or "dropped" not in (rows[0]["detail"] or ""), "that write went through the store, not the route"


def test_a_same_length_save_with_different_sentences_does_not_slide_the_adjustments(client):
    """Length alone is not identity. A Save that deletes one sentence and adds
    another keeps the count, and carrying by index alone would move every
    adjustment down a row onto a sentence it was never meant for - the silent
    corruption an index-keyed map was rejected for. The WINDOW is the identity:
    the words are what a Save may change, start and end are not."""
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/2", json={"offset": -0.4, "muted": True})

    # Three sentences in, three out - but the first is gone and a new one is at
    # the end, so index 2 is no longer the sentence that adjustment belonged to.
    reshuffled = [
        {"start": 5.0, "end": 7.0, "text": "Second sentence."},
        {"start": 10.0, "end": 12.0, "text": "Third sentence."},
        {"start": 13.0, "end": 15.0, "text": "A new closing sentence."},
    ]
    r = client.patch(f"/api/projects/{pid}/transcript", json={"transcript": reshuffled})
    assert r.status_code == 200, r.text
    assert r.json()["timing_adjustments_dropped"] == 1

    saved = _transcript(pid)
    assert all("offset" not in s and "muted" not in s for s in saved), (
        "the adjustment must be dropped, never carried onto a different sentence"
    )


def test_the_route_reports_how_many_adjustments_a_save_dropped(client):
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/2", json={"offset": -0.4})

    r = client.patch(f"/api/projects/{pid}/transcript", json={"transcript": [dict(s) for s in SEGMENTS[:2]]})
    assert r.status_code == 200, r.text
    assert r.json()["timing_adjustments_dropped"] == 1, "the answer says so, not only the log"
    row = [x for x in auth_store.list_audit(limit=50) if x["action"] == "project.transcript_edit"][0]
    assert row["detail"] == "2 segments, 1 timing adjustment dropped"


def test_a_clean_save_reports_nothing_dropped(client):
    pid = _video()
    client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4})

    r = client.patch(f"/api/projects/{pid}/transcript", json={"transcript": [dict(s) for s in SEGMENTS]})
    assert r.json()["timing_adjustments_dropped"] == 0
    assert _transcript(pid)[1]["offset"] == -0.4


def test_the_whole_list_save_is_refused_while_a_job_holds_the_project(client, monkeypatch):
    """It merges the stored adjustments now, so it is a writer of this record
    like any other - and a transcribe or re-voice job holds its own copy."""
    pid = _video()
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "transcribe", "status": "running"} if project_id == pid else None,
    )
    r = client.patch(f"/api/projects/{pid}/transcript", json={"transcript": [dict(s) for s in SEGMENTS]})
    assert r.status_code == 409, r.text


# ── trap 7: muting everything ────────────────────────────────────────────────

def test_muting_every_sentence_refuses_the_re_voice_with_a_reason(client):
    """Without this the job fails inside the engine with a bare "Re-voice
    failed": ``_revoice_video`` returns False on empty chunks. Refused at the
    route, the way an empty transcript already is."""
    pid = _video()
    for index in range(3):
        assert client.patch(f"/api/projects/{pid}/transcript/{index}", json={"muted": True}).status_code == 200

    r = client.post(f"/api/projects/{pid}/revoice", json={"voice_id": "en-US-AriaNeural"})
    assert r.status_code == 400, r.text
    assert "Every sentence is muted" in r.json()["detail"]
    assert jobs.active_for(pid) is None, "no job was started"

    # One sentence back is enough.
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"muted": False}).status_code == 200
    assert narration.spoken_count(pid) == 1
