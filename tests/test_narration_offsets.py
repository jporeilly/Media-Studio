"""The batch offsets write (the edit timeline, phase E3: dragging the narration).

``PATCH /api/projects/{pid}/narration/offsets`` moves several sentences in ONE
request - the timeline's drag of a marquee of blocks, its nudge keys, its
Reset timing - applied as ONE read-modify-write under the project's lock. A
drag of twelve sentences must never be twelve interleaving writes of the whole
record (``docs/porting/edit-timeline.md`` §11.3, trap 21). The single-sentence
PATCH stays exactly as it was; both take the store's lock, so a drag landing
beside a List-view adjustment destroys neither. The values go through the very
rule the single PATCH applies (``services.narration._offset``), never a copy.
"""

import threading

import pytest
from fastapi.testclient import TestClient

from api import store as auth_store
from services import jobs, narration, processing
from services import projects as store
from utils import helpers
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
    monkeypatch.setattr(store, "_locks", {})
    monkeypatch.setattr(helpers, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(narration, "_BASELINE_CACHE", {})


@pytest.fixture(autouse=True)
def baseline(monkeypatch):
    """The measured speaking rate, stubbed; the calls are counted so a test
    can see whether the memo was dropped."""
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


def _video(segments=None) -> str:
    rec = store.import_upload("clip.mp4", b"video-bytes")
    store.set_transcript(rec["id"], [dict(s) for s in (SEGMENTS if segments is None else segments)])
    return rec["id"]


def _transcript(pid: str) -> list[dict]:
    return store.get_project(pid)["transcript"]


def _patch(client, pid, offsets):
    return client.patch(f"/api/projects/{pid}/narration/offsets", json={"offsets": offsets})


def _timing_rows() -> list[dict]:
    return [r for r in auth_store.list_audit(limit=50) if r["action"] == "project.transcript_timing"]


# ── the route ────────────────────────────────────────────────────────────────

def test_several_offsets_are_written_at_once_and_come_back_with_their_index(client):
    pid = _video()
    r = _patch(client, pid, [{"index": 2, "offset": -0.4}, {"index": 0, "offset": 1.25}])
    assert r.status_code == 200, r.text
    assert r.json() == {"sentences": [
        {"index": 0, "start": 0.0, "end": 2.0, "text": "First sentence.", "offset": 1.25},
        {"index": 2, "start": 10.0, "end": 12.0, "text": "Third sentence.", "offset": -0.4},
    ]}, "the updated sentences, in index order, each addressed by its index in the stored transcript"

    saved = _transcript(pid)
    assert saved[0]["offset"] == 1.25 and saved[2]["offset"] == -0.4
    assert saved[1] == SEGMENTS[1], "a sentence not named gains no keys at all"
    assert [(s["start"], s["end"]) for s in saved] == [(s["start"], s["end"]) for s in SEGMENTS], "the transcript never moves"

    # The plan pins them where the offsets say.
    plan = client.get(f"/api/projects/{pid}/narration/plan").json()
    assert [s["pinned_start"] for s in plan["sentences"]] == [1.25, 5.0, 9.6]


def test_the_values_go_through_the_single_patch_s_own_rule(client):
    """Three decimals, an int accepted, 0 and null both clearing the key -
    ``services.narration._offset``, reused rather than copied."""
    pid = _video()
    assert _patch(client, pid, [{"index": 0, "offset": 0.12345}, {"index": 1, "offset": 2}]).status_code == 200
    assert _transcript(pid)[0]["offset"] == 0.123 and _transcript(pid)[1]["offset"] == 2.0

    assert _patch(client, pid, [{"index": 0, "offset": 0}, {"index": 1, "offset": None}]).status_code == 200
    assert _transcript(pid) == SEGMENTS, "0 and null both clear the key: absent means the default"
    assert _patch(client, pid, [{"index": 2}]).status_code == 200, "an offset left out is null"
    assert _transcript(pid) == SEGMENTS


def test_the_body_is_strict(client):
    """``extra="forbid"`` on both models, strict numbers, at least one entry."""
    pid = _video()
    for body in (
        {"offsets": []},
        {"offsets": [{"index": 0, "offset": 301.0}]},
        {"offsets": [{"index": 0, "offset": -301.0}]},
        {"offsets": [{"index": 0, "offset": True}]},
        {"offsets": [{"index": 0, "offset": "0.4"}]},
        {"offsets": [{"index": 1.5, "offset": 0.4}]},
        {"offsets": [{"index": "1", "offset": 0.4}]},
        {"offsets": [{"index": True, "offset": 0.4}]},
        {"offsets": [{"offset": 0.4}]},
        {"offsets": [{"index": 0, "offset": 0.4, "speed": 1.2}]},
        {"offsets": [{"index": 0, "offset": 0.4}], "muted": True},
        {},
    ):
        assert client.patch(f"/api/projects/{pid}/narration/offsets", json=body).status_code == 422, body
    # NaN fails the schema's bounds, exactly as it fails ``SegmentOverride``'s
    # on the single PATCH; the 422 cannot echo a NaN back and lands as the
    # app's 400. Whatever the words, nothing is written.
    r = client.patch(f"/api/projects/{pid}/narration/offsets", content='{"offsets": [{"index": 0, "offset": NaN}]}',
                     headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert _transcript(pid) == SEGMENTS


def test_an_index_outside_the_transcript_is_a_400_naming_it_and_nothing_is_written(client):
    pid = _video()
    r = _patch(client, pid, [{"index": 0, "offset": 0.4}, {"index": 9, "offset": 0.4}])
    assert r.status_code == 400, r.text
    assert "index 9" in r.json()["detail"] and "0 to 2" in r.json()["detail"]
    assert _transcript(pid) == SEGMENTS, "the first entry was valid and was still not written"
    r = _patch(client, pid, [{"index": -1, "offset": 0.4}])
    assert r.status_code == 400 and "index -1" in r.json()["detail"]

    untranscribed = store.import_upload("other.mp4", b"video-bytes")["id"]
    r = _patch(client, untranscribed, [{"index": 0, "offset": 0.4}])
    assert r.status_code == 400 and "transcribe it first" in r.json()["detail"]


def test_a_duplicate_index_is_refused_and_nothing_is_written(client):
    pid = _video()
    r = _patch(client, pid, [{"index": 1, "offset": 0.4}, {"index": 1, "offset": 0.5}])
    assert r.status_code == 400 and "given twice" in r.json()["detail"]
    assert _transcript(pid) == SEGMENTS


def test_a_deck_and_a_missing_project_are_refused_as_the_single_patch_refuses_them(client):
    deck = store.import_upload("deck.pptx", b"pptx-bytes")["id"]
    r = _patch(client, deck, [{"index": 0, "offset": 0.4}])
    assert r.status_code == 400 and "Only video projects" in r.json()["detail"]
    assert _patch(client, "aabbccddeeff", [{"index": 0, "offset": 0.4}]).status_code == 404


def test_the_batch_is_refused_while_a_job_holds_the_project(client, monkeypatch):
    pid = _video()
    monkeypatch.setattr(
        jobs, "active_for",
        lambda project_id: {"id": "j1", "kind": "revoice", "status": "running"} if project_id == pid else None,
    )
    r = _patch(client, pid, [{"index": 0, "offset": 0.4}])
    assert r.status_code == 409, r.text
    assert "revoice" in r.json()["detail"]
    assert _transcript(pid) == SEGMENTS


def test_the_batch_is_audited_once_by_indices_and_field_name_never_by_value_or_words(client):
    pid = _video()
    assert _patch(client, pid, [{"index": 2, "offset": -0.4}, {"index": 0, "offset": 1.0}, {"index": 1, "offset": None}]).status_code == 200
    rows = _timing_rows()
    assert len(rows) == 1, rows
    assert rows[0]["entity"] == "project" and rows[0]["entity_id"] == pid
    assert rows[0]["detail"] == "segments 0, 1, 2: offset"
    assert "0.4" not in rows[0]["detail"] and "sentence" not in rows[0]["detail"].lower()


def test_the_baseline_memo_is_not_dropped_by_a_batch_of_offsets(client, baseline):
    """An offset moves where a sentence is pinned, not the texts the rate is
    measured over - unlike a mute, which changes the sample set."""
    pid = _video()
    assert client.get(f"/api/projects/{pid}/narration/plan").status_code == 200
    assert len(baseline) == 1
    assert _patch(client, pid, [{"index": 0, "offset": 0.4}, {"index": 1, "offset": 0.4}]).status_code == 200
    assert client.get(f"/api/projects/{pid}/narration/plan").status_code == 200
    assert len(baseline) == 1, "not re-measured"


def test_the_ownership_sweep_names_the_route():
    from test_project_ownership import PROJECT_SCOPED_ROUTES

    assert ("PATCH", "/api/projects/{pid}/narration/offsets") in PROJECT_SCOPED_ROUTES


# ── the service ──────────────────────────────────────────────────────────────

def test_the_batch_is_one_write_under_the_projects_lock(monkeypatch):
    """Trap 21: N offsets, ONE ``save_project``, inside ``project_lock`` -
    never one write per sentence."""
    pid = _video()
    real_lock = store.project_lock
    real_save = store.save_project
    entered: list[int] = []
    saves: list[bool] = []

    class Recording:
        def __init__(self, lock):
            self.lock = lock

        def __enter__(self):
            entered.append(1)
            return self.lock.__enter__()

        def __exit__(self, *exc):
            return self.lock.__exit__(*exc)

    def _save(record):
        saves.append(real_lock(pid).locked())
        real_save(record)

    monkeypatch.setattr(store, "project_lock", lambda project_id: Recording(real_lock(project_id)))
    monkeypatch.setattr(store, "save_project", _save)

    updated = narration.update_offsets(pid, [(0, 0.4), (1, -0.2), (2, None)])
    assert [s["index"] for s in updated] == [0, 1, 2]
    assert len(saves) == 1, "one write for three sentences"
    assert saves == [True], "written while the lock was held"
    assert len(entered) == 1
    assert _transcript(pid)[0]["offset"] == 0.4 and _transcript(pid)[1]["offset"] == -0.2 and "offset" not in _transcript(pid)[2]


def test_the_service_refuses_before_it_writes_anything():
    pid = _video()
    with pytest.raises(ValueError):
        narration.update_offsets(pid, [(0, float("nan"))])
    with pytest.raises(ValueError):
        narration.update_offsets(pid, [(0, 0.4), (0, 0.5)])
    with pytest.raises(ValueError):
        narration.update_offsets(pid, [(True, 0.4)])
    with pytest.raises(ValueError):
        narration.update_offsets(pid, [])
    with pytest.raises(narration.SegmentNotFound):
        narration.update_offsets(pid, [(0, 0.4), (3, 0.4)])
    with pytest.raises(narration.ProjectNotFound):
        narration.update_offsets("aabbccddeeff", [(0, 0.4)])
    assert _transcript(pid) == SEGMENTS


def test_a_drag_landing_beside_a_single_adjustment_destroys_neither():
    """Both are read-modify-writes of the same file and both take
    ``services.projects.project_lock``; without it the later writer wins on
    the whole record. The UI produces this pairing: a List-view box commits
    on blur while a drag on the timeline is released."""
    pid = _video()
    for attempt in range(20):
        store.set_transcript(pid, [dict(s) for s in SEGMENTS])
        start = threading.Barrier(2)
        failures: list[BaseException] = []

        def drag():
            start.wait()
            try:
                narration.update_offsets(pid, [(0, 0.4), (2, -0.4)])
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                failures.append(exc)

        def adjust():
            start.wait()
            try:
                narration.update_segment(pid, 1, speed=1.2)
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)

        threads = [threading.Thread(target=drag), threading.Thread(target=adjust)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

        assert failures == [], failures
        saved = store.get_project(pid)
        assert saved is not None, f"run {attempt}: project.json was torn"
        assert saved["transcript"][0].get("offset") == 0.4, f"run {attempt}: the drag was lost"
        assert saved["transcript"][2].get("offset") == -0.4, f"run {attempt}: the drag was lost"
        assert saved["transcript"][1].get("speed") == 1.2, f"run {attempt}: the adjustment was lost"


def test_the_single_sentence_patch_is_unchanged(client):
    """The List view's route stays exactly as it was: one sentence, its own
    audit row, the same keys on disk as the batch writes."""
    pid = _video()
    assert client.patch(f"/api/projects/{pid}/transcript/1", json={"offset": -0.4}).status_code == 200
    assert _patch(client, pid, [{"index": 0, "offset": -0.4}]).status_code == 200
    assert _transcript(pid)[0]["offset"] == _transcript(pid)[1]["offset"] == -0.4
    assert [row["detail"] for row in _timing_rows()] == ["segments 0: offset", "segment 1: offset"]
