"""Walkthroughs (W1): the record, the eight routes, the copy through the oracle
and the whole-document, versioned save.

- a walkthrough is made, listed (the caller's, newest first, counts and the
  first step's id, never a step's text), read, saved, copied and deleted (the
  folder gone);
- a step's picture is a COPY taken through ``services.captures.image_path``:
  an annotated still gives the annotated picture; deleting or re-annotating
  the still later leaves the step as it was; Replace picture takes the
  still's current picture and clamps the hotspot to the new size;
- a save carries the revision it loaded: a stale one is 409 and nothing is
  written; a step dropped by a save has its picture removed;
- every refusal of a document - a hotspot outside or too small, a text, a
  chapter or a name too long, too many steps, a picture the walkthrough does
  not hold, an unknown kind or placement, a bad id - is 400 with a plain line
  and nothing written; a body over the cap is 413;
- ownership is the walkthrough's: another editor is refused on every route,
  an administrator is not, the list is filtered; the routes work from another
  machine (the browser edition);
- the audit rows and their details (never a step's text); the limits rows.

Every still here is a Pillow-made PNG through the fake grab: nothing reads the
screen. Every walkthrough lives under the test's own temporary folder.
"""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api.routers.capture as capture_router
from api import store as auth_store
from services import capture, captures, recordings, walkthroughs
from services import projects as store
from utils.config import config
from utils.helpers import file_under

PASSWORD = "Owner-pass-12345"
LOOPBACK = ("127.0.0.1", 50000)
RED, BLUE, GREEN, YELLOW = (200, 30, 30), (0, 0, 255), (0, 200, 0), (240, 220, 0)
LIMITS_MD = Path(__file__).resolve().parent.parent / "docs" / "reference" / "limits.md"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(captures, "CAPTURES_DIR", tmp_path / "captures")
    monkeypatch.setattr(captures, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(walkthroughs, "WALKTHROUGHS_DIR", tmp_path / "walkthroughs")
    monkeypatch.setattr(walkthroughs, "_locks", {})
    monkeypatch.setattr(capture_router, "SCREEN_DIR", tmp_path / "temp" / "capture")
    monkeypatch.setattr(recordings, "RECORDINGS_DIR", tmp_path / "temp" / "recordings")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(auth_store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(auth_store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    from api.app import app

    with TestClient(app, client=LOOPBACK) as admin:
        seeded = auth_store.authenticate("admin", "admin")
        auth_store.change_password(seeded["id"], "admin", must_change=False)
        assert admin.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
        auth_store.create_user("other", PASSWORD, "Otto Other", role="editor", must_change_password=False)
        other = TestClient(app, client=LOOPBACK)
        assert other.post("/api/auth/login", json={"username": "other", "password": PASSWORD}).status_code == 200
        auth_store.create_user("owner", PASSWORD, "Olive Owner", role="editor", must_change_password=False)
        owner = TestClient(app, client=LOOPBACK)
        assert owner.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
        yield {"admin": admin, "other": other, "owner": owner, "tmp": tmp_path, "app": app}


# -- helpers --------------------------------------------------------------------------

def _fake_grab(colour=RED):
    """A stand-in for gdigrab: a PNG of the asked size and colour."""

    def grab(x, y, width, height, *, cursor, out_dir):
        capture.validate_region(x, y, width, height)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        out = Path(out_dir) / f"still-fake-{width}x{height}.png"
        Image.new("RGB", (width, height), colour).save(out)
        return out

    return grab


def _png(size: tuple[int, int], colour=BLUE) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _still(rig, monkeypatch, width: int, height: int, colour=RED, name: str = "Still", who: str = "admin") -> str:
    monkeypatch.setattr(captures.capture, "take_still", _fake_grab(colour))
    r = rig[who].post("/api/captures", json={"x": 0, "y": 0, "width": width, "height": height, "name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _annotate(c: TestClient, cid: str, size: tuple[int, int], colour=BLUE):
    """Annotate a still with one arrow and a picture of ``colour``: from then on the oracle answers it."""
    doc = {"version": 1, "crop": None, "shapes": [{"id": "a1", "type": "arrow", "colour": "#e53935", "stroke": "medium", "x1": 1.0, "y1": 1.0, "x2": 5.0, "y2": 5.0}]}
    r = c.put(f"/api/captures/{cid}/annotations", data={"document": json.dumps(doc)}, files={"image": ("annotated.png", _png(size, colour), "image/png")})
    assert r.status_code == 200, r.text


def _new(c: TestClient, name: str = "Tour") -> dict:
    r = c.post("/api/walkthroughs", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def _add(c: TestClient, wid: str, cid: str, after: str | None = None) -> dict:
    body = {"capture_id": cid}
    if after:
        body["after"] = after
    r = c.post(f"/api/walkthroughs/{wid}/steps", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _body(rec: dict, **changes) -> dict:
    """The PUT body the builder sends: the document and the revision it loaded, nothing else."""
    out = {"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": [dict(s) for s in rec["steps"]]}
    out.update(changes)
    return out


def _put(c: TestClient, wid: str, body):
    content = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return c.put(f"/api/walkthroughs/{wid}", content=content, headers={"Content-Type": "application/json"})


def _pixel(c: TestClient, wid: str, sid: str, xy=(0, 0)):
    r = c.get(f"/api/walkthroughs/{wid}/steps/{sid}/image")
    assert r.status_code == 200, r.text
    return Image.open(io.BytesIO(r.content)).convert("RGB").getpixel(xy)


def _folder(rig, wid: str) -> Path:
    return rig["tmp"] / "walkthroughs" / wid


def _pictures(rig, wid: str) -> list[str]:
    return sorted(p.name for p in _folder(rig, wid).iterdir() if p.name.startswith("step-"))


def _stored(rig, wid: str) -> dict:
    return json.loads((_folder(rig, wid) / "walkthrough.json").read_text(encoding="utf-8"))


def _hotspot(x=2, y=2, width=10, height=10) -> dict:
    return {"x": x, "y": y, "width": width, "height": height}


def _signed_in_from(app, client: tuple[str, int]) -> TestClient:
    c = TestClient(app, client=client)
    assert c.post("/api/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
    return c


# -- the record and the list ----------------------------------------------------------------

def test_create_list_get_and_delete(rig):
    c = rig["admin"]
    rec = _new(c, "  Onboarding tour  ")
    assert len(rec["id"]) == 12 and rec["name"] == "Onboarding tour" and rec["revision"] == 1 and rec["version"] == 1
    assert rec["steps"] == [] and rec["owner_id"] and rec["owner_name"] and rec["created_at"] == rec["updated_at"]
    assert (_folder(rig, rec["id"]) / "walkthrough.json").is_file()

    listed = c.get("/api/walkthroughs").json()["walkthroughs"]
    assert [w["id"] for w in listed] == [rec["id"]]
    assert listed[0] == {
        "id": rec["id"], "name": "Onboarding tour", "owner_id": rec["owner_id"], "owner_name": rec["owner_name"],
        "created_at": rec["created_at"], "updated_at": rec["updated_at"], "revision": 1,
        "step_count": 0, "chapter_count": 0, "first_step_id": None,
    }
    assert c.get(f"/api/walkthroughs/{rec['id']}").json() == rec

    assert c.delete(f"/api/walkthroughs/{rec['id']}").status_code == 204
    assert not _folder(rig, rec["id"]).exists(), "the folder is gone"
    assert c.get(f"/api/walkthroughs/{rec['id']}").status_code == 404
    assert c.delete(f"/api/walkthroughs/{rec['id']}").status_code == 404
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == []
    assert c.get("/api/walkthroughs/not-an-id").status_code == 404
    assert c.get("/api/walkthroughs/000000000000").status_code == 404


def test_a_name_is_required_and_bounded(rig):
    c = rig["admin"]
    for bad in ("", "   ", "x" * (walkthroughs.MAX_NAME_CHARS + 1)):
        assert c.post("/api/walkthroughs", json={"name": bad}).status_code == 422, bad
    assert c.post("/api/walkthroughs", json={"name": "x" * walkthroughs.MAX_NAME_CHARS}).status_code == 200
    assert c.post("/api/walkthroughs", json={"name": "ok", "steps": []}).status_code == 422, "a new walkthrough takes a name only"


def test_the_list_is_newest_first_with_counts_and_no_texts(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 40, 30)
    first = _new(c, "First")
    second = _new(c, "Second")
    rec = _add(c, first["id"], cid)
    rec = _add(c, first["id"], cid)
    steps = rec["steps"]
    steps[0] = {**steps[0], "text": "secret words of step one", "chapter": "Start"}
    steps[1] = {**steps[1], "text": "more words", "chapter": "Middle"}
    assert _put(c, first["id"], _body(rec, steps=steps)).status_code == 200
    r = c.get("/api/walkthroughs")
    listed = r.json()["walkthroughs"]
    assert [w["name"] for w in listed] == ["First", "Second"], "the one saved last is newest"
    assert listed[1]["id"] == second["id"]
    assert listed[0]["step_count"] == 2 and listed[0]["chapter_count"] == 2 and listed[0]["first_step_id"] == steps[0]["id"]
    assert listed[1]["step_count"] == 0 and listed[1]["first_step_id"] is None
    assert "secret words" not in r.text and "steps" not in listed[0], "the list carries no steps and no texts"


# -- the copy through the oracle ----------------------------------------------------------------

def test_add_step_copies_the_captures_current_picture_through_the_oracle(rig, monkeypatch):
    c = rig["admin"]
    plain = _still(rig, monkeypatch, 40, 30, RED, name="Plain")
    marked = _still(rig, monkeypatch, 60, 20, RED, name="Marked")
    _annotate(c, marked, (60, 20), BLUE)
    w = _new(c)
    rec = _add(c, w["id"], plain)
    rec = _add(c, w["id"], marked)
    assert rec["revision"] == 3 and len(rec["steps"]) == 2
    a, b = rec["steps"]
    assert a["image"] == f"step-{a['id']}.png" and (a["width"], a["height"]) == (40, 30)
    assert a == {"id": a["id"], "image": a["image"], "width": 40, "height": 30, "hotspot": None, "kind": "tooltip", "text": "",
                 "instruction": "", "placement": "auto", "chapter": "", "source": {"capture_id": plain, "annotated": False}}
    assert b["source"] == {"capture_id": marked, "annotated": True} and (b["width"], b["height"]) == (60, 20)
    # The annotated still gives the ANNOTATED picture (blue), the plain one its still (red).
    assert _pixel(c, w["id"], a["id"]) == RED
    assert _pixel(c, w["id"], b["id"]) == BLUE
    assert _pictures(rig, w["id"]) == sorted([a["image"], b["image"]])
    assert Image.open(_folder(rig, w["id"]) / b["image"]).size == (60, 20)
    listed = c.get("/api/walkthroughs").json()["walkthroughs"][0]
    assert listed["step_count"] == 2 and listed["first_step_id"] == a["id"]
    # The image route: inline, never cached.
    r = c.get(f"/api/walkthroughs/{w['id']}/steps/{a['id']}/image")
    assert r.headers["content-type"] == "image/png" and r.headers["cache-control"] == "no-store"
    assert "attachment" not in r.headers.get("content-disposition", "")
    assert c.get(f"/api/walkthroughs/{w['id']}/steps/000000000000/image").status_code == 404
    assert c.get(f"/api/walkthroughs/{w['id']}/steps/nope/image").status_code == 404


def test_the_step_is_a_copy_the_capture_cannot_change(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 40, 30, RED)
    _annotate(c, cid, (40, 30), BLUE)
    w = _new(c)
    sid = _add(c, w["id"], cid)["steps"][0]["id"]
    assert _pixel(c, w["id"], sid) == BLUE
    # Re-annotated green: the step stays blue. Reverted: still blue. Deleted: still blue, still served.
    _annotate(c, cid, (40, 30), GREEN)
    assert _pixel(c, w["id"], sid) == BLUE
    assert c.delete(f"/api/captures/{cid}/annotations").status_code == 204
    assert _pixel(c, w["id"], sid) == BLUE
    assert c.delete(f"/api/captures/{cid}").status_code == 204
    assert _pixel(c, w["id"], sid) == BLUE
    rec = c.get(f"/api/walkthroughs/{w['id']}").json()
    assert rec["steps"][0]["source"] == {"capture_id": cid, "annotated": True}, "the source is informational and stays"
    # And the walkthrough saves without it: nothing reads the capture again.
    assert _put(c, w["id"], _body(rec)).status_code == 200


def test_add_step_needs_a_capture_of_the_callers(rig, monkeypatch):
    admin, other = rig["admin"], rig["other"]
    theirs = _still(rig, monkeypatch, 8, 8, who="other")
    w = _new(admin)
    assert admin.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": "000000000000"}).status_code == 404
    assert admin.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": "nope"}).status_code == 422
    # An editor adding somebody else's still to their own walkthrough: refused as the Captures routes refuse it.
    mine = _new(other)
    adminstill = _still(rig, monkeypatch, 8, 8)
    r = other.post(f"/api/walkthroughs/{mine['id']}/steps", json={"capture_id": adminstill})
    assert r.status_code == 403 and r.json()["detail"] == "This capture is not yours."
    # An administrator may use anyone's still.
    assert admin.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": theirs}).status_code == 200
    # A capture whose picture is gone from disk: 404, no step.
    gone = _still(rig, monkeypatch, 8, 8)
    (rig["tmp"] / "captures" / gone / "still.png").unlink()
    r = admin.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": gone})
    assert r.status_code == 404 and "missing" in r.json()["detail"]
    assert len(admin.get(f"/api/walkthroughs/{w['id']}").json()["steps"]) == 1


def test_insert_after_and_append(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    a = _add(c, w["id"], cid)["steps"][0]["id"]
    rec = _add(c, w["id"], cid, after=a)
    b = rec["steps"][1]["id"]
    rec = _add(c, w["id"], cid, after=a)
    x = rec["steps"][1]["id"]
    assert [s["id"] for s in rec["steps"]] == [a, x, b]
    rec = _add(c, w["id"], cid)
    assert [s["id"] for s in rec["steps"]][:3] == [a, x, b] and len(rec["steps"]) == 4, "no after: appended"
    r = c.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": cid, "after": "000000000000"})
    assert r.status_code == 400 and "insert after" in r.json()["detail"]
    assert len(c.get(f"/api/walkthroughs/{w['id']}").json()["steps"]) == 4
    assert len(_pictures(rig, w["id"])) == 4, "a refused add copies nothing"
    audit = auth_store.list_audit(action="walkthrough.add_step")
    assert audit[0]["detail"] == f"capture {cid} -> step 4 of 4" and audit[-1]["detail"] == f"capture {cid} -> step 1 of 1"


def test_add_step_stops_at_the_cap(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 2)
    _add(c, w["id"], cid)
    _add(c, w["id"], cid)
    r = c.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": cid})
    assert r.status_code == 400 and r.json()["detail"] == "A walkthrough can hold at most 2 steps."
    assert len(_pictures(rig, w["id"])) == 2


# -- replace picture -----------------------------------------------------------------------------

def test_replace_picture_takes_the_current_picture_and_clamps_the_hotspot(rig, monkeypatch):
    c = rig["admin"]
    big = _still(rig, monkeypatch, 40, 30, RED)
    small = _still(rig, monkeypatch, 20, 16, GREEN)
    tiny = _still(rig, monkeypatch, 6, 6, YELLOW)
    w = _new(c)
    rec = _add(c, w["id"], big)
    sid = rec["steps"][0]["id"]
    rec["steps"][0]["hotspot"] = _hotspot(28, 18, 10, 10)
    rec["steps"][0]["text"] = "Click here"
    assert _put(c, w["id"], _body(rec)).status_code == 200
    assert _pixel(c, w["id"], sid) == RED

    r = c.post(f"/api/walkthroughs/{w['id']}/steps/{sid}/picture", json={"capture_id": small})
    assert r.status_code == 200, r.text
    step = r.json()["steps"][0]
    assert step["id"] == sid and step["image"] == f"step-{sid}.png" and (step["width"], step["height"]) == (20, 16)
    assert step["hotspot"] == {"x": 10, "y": 6, "width": 10, "height": 10}, "moved in to fit the 20 x 16 picture"
    assert step["text"] == "Click here" and step["source"] == {"capture_id": small, "annotated": False}
    assert r.json()["revision"] == 4, "created 1, added 2, saved 3, replaced 4"
    assert _pixel(c, w["id"], sid) == GREEN
    assert _pictures(rig, w["id"]) == [f"step-{sid}.png"], "the same file, replaced in place"
    # The annotated picture of the new still, once there is one, is what a later replace copies.
    _annotate(c, small, (20, 16), BLUE)
    r = c.post(f"/api/walkthroughs/{w['id']}/steps/{sid}/picture", json={"capture_id": small})
    assert r.status_code == 200 and r.json()["steps"][0]["source"]["annotated"] is True
    assert _pixel(c, w["id"], sid) == BLUE
    # A picture that cannot hold a hotspot at all: the hotspot is dropped.
    r = c.post(f"/api/walkthroughs/{w['id']}/steps/{sid}/picture", json={"capture_id": tiny})
    assert r.status_code == 200 and r.json()["steps"][0]["hotspot"] is None
    assert _pixel(c, w["id"], sid) == YELLOW
    assert c.post(f"/api/walkthroughs/{w['id']}/steps/000000000000/picture", json={"capture_id": big}).status_code == 404
    assert c.post(f"/api/walkthroughs/{w['id']}/steps/nope/picture", json={"capture_id": big}).status_code == 404
    rows = auth_store.list_audit(action="walkthrough.replace_picture")
    assert rows[-1]["detail"] == f"step {sid}: capture {small}"


def test_clamp_hotspot_itself():
    clamp = walkthroughs.clamp_hotspot
    assert clamp(None, 100, 100) is None
    assert clamp(_hotspot(10, 10, 20, 20), 100, 100) == _hotspot(10, 10, 20, 20)
    assert clamp(_hotspot(95, 95, 20, 20), 100, 100) == _hotspot(80, 80, 20, 20), "moved in"
    assert clamp(_hotspot(0, 0, 200, 300), 100, 50) == _hotspot(0, 0, 100, 50), "shrunk to the picture"
    assert clamp(_hotspot(50, 50, 20, 20), 60, 60) == _hotspot(40, 40, 20, 20)
    assert clamp(_hotspot(0, 0, 20, 20), 7, 100) is None and clamp(_hotspot(0, 0, 20, 20), 100, 7) is None
    assert clamp(_hotspot(0, 0, 2, 2), 100, 100) == _hotspot(0, 0, 8, 8), "never under the minimum side"


# -- the save -------------------------------------------------------------------------------------

def test_put_saves_the_whole_document_bumps_the_revision_and_removes_dropped_pictures(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 40, 30)
    w = _new(c, "Tour")
    for _ in range(3):
        rec = _add(c, w["id"], cid)
    a, b, d = rec["steps"]
    assert rec["revision"] == 4
    steps = [
        {**d, "chapter": "Finish", "text": "The end.\nTwo lines.", "kind": "modal"},
        {**a, "hotspot": _hotspot(0, 0, 40, 30), "text": "Press Save", "placement": "left", "chapter": "Start"},
    ]
    r = _put(c, w["id"], _body(rec, name="  Renamed tour ", steps=steps))
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["revision"] == 5 and saved["name"] == "Renamed tour" and saved["updated_at"] > rec["updated_at"]
    assert [s["id"] for s in saved["steps"]] == [d["id"], a["id"]], "the order is the document's"
    assert saved["steps"][0]["chapter"] == "Finish" and saved["steps"][0]["kind"] == "modal" and saved["steps"][0]["text"] == "The end.\nTwo lines."
    assert saved["steps"][1]["hotspot"] == _hotspot(0, 0, 40, 30) and saved["steps"][1]["placement"] == "left"
    assert saved["id"] == w["id"] and saved["owner_id"] == rec["owner_id"] and saved["created_at"] == rec["created_at"]
    assert c.get(f"/api/walkthroughs/{w['id']}").json() == saved
    assert _stored(rig, w["id"]) == saved
    # The dropped step's picture is gone, the kept ones are there, and its image route is a 404.
    assert _pictures(rig, w["id"]) == sorted([a["image"], d["image"]])
    assert c.get(f"/api/walkthroughs/{w['id']}/steps/{b['id']}/image").status_code == 404
    assert _pixel(c, w["id"], a["id"]) == RED
    rows = auth_store.list_audit(action="walkthrough.update")
    assert rows[0]["detail"] == "2 steps, 2 chapters" and rows[0]["entity_id"] == w["id"]
    assert "Press Save" not in json.dumps(rows) and "The end" not in json.dumps(rows)
    # A save of nothing: an empty walkthrough, every picture removed.
    r = _put(c, w["id"], _body(saved, steps=[]))
    assert r.status_code == 200 and r.json()["steps"] == [] and r.json()["revision"] == 6
    assert _pictures(rig, w["id"]) == []
    assert auth_store.list_audit(action="walkthrough.update")[0]["detail"] == "0 steps, 0 chapters"


def test_a_stale_revision_is_409_and_nothing_is_written(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 40, 30)
    w = _new(c)
    rec = _add(c, w["id"], cid)
    assert rec["revision"] == 2
    # Two builders loaded revision 2; the first saves, the second is stale.
    first = _body(rec, name="First saver")
    second = _body(rec, name="Second saver", steps=[])
    assert _put(c, w["id"], first).status_code == 200
    r = _put(c, w["id"], second)
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "This walkthrough was changed elsewhere: reload it."
    stored = _stored(rig, w["id"])
    assert stored["name"] == "First saver" and stored["revision"] == 3 and len(stored["steps"]) == 1
    assert _pictures(rig, w["id"]) == [rec["steps"][0]["image"]], "the stale save dropped no picture"
    assert len(auth_store.list_audit(action="walkthrough.update")) == 1, "a refused save records nothing"
    # An older revision, and a newer one nobody has, are stale too.
    assert _put(c, w["id"], _body(rec, revision=1)).status_code == 409
    assert _put(c, w["id"], _body(rec, revision=99)).status_code == 409
    assert _put(c, w["id"], _body(rec, revision=3)).status_code == 200


def _bad_cases():
    """(label, a function from the loaded record to the PUT body, a word the detail must carry)."""
    long_text = "x" * (walkthroughs.MAX_STEP_TEXT + 1)
    long_instruction = "i" * (walkthroughs.MAX_INSTRUCTION_CHARS + 1)
    long_chapter = "c" * (walkthroughs.MAX_CHAPTER_CHARS + 1)
    long_name = "n" * (walkthroughs.MAX_NAME_CHARS + 1)

    def with_step(**patch):
        return lambda rec: _body(rec, steps=[{**rec["steps"][0], **patch}, rec["steps"][1]])

    return [
        ("hotspot past the right edge", with_step(hotspot=_hotspot(35, 0, 10, 10)), "past the picture's edge"),
        ("hotspot past the bottom edge", with_step(hotspot=_hotspot(0, 25, 10, 10)), "past the picture's edge"),
        ("hotspot too small", with_step(hotspot=_hotspot(0, 0, 7, 10)), "hotspot.width"),
        ("hotspot too small the other way", with_step(hotspot=_hotspot(0, 0, 10, 7)), "hotspot.height"),
        ("hotspot negative", with_step(hotspot=_hotspot(-1, 0, 10, 10)), "hotspot.x"),
        ("hotspot with a fraction", with_step(hotspot={"x": 0.5, "y": 0, "width": 10, "height": 10}), "hotspot.x"),
        ("hotspot with an extra field", with_step(hotspot={**_hotspot(), "spin": 1}), "spin"),
        ("text too long", with_step(text=long_text), "text"),
        ("text with a control character", with_step(text="a\x00b"), "control"),
        ("instruction too long", with_step(instruction=long_instruction), "instruction"),
        ("instruction with a control character", with_step(instruction="a\x00b"), "control"),
        ("chapter too long", with_step(chapter=long_chapter), "chapter"),
        ("chapter with a line break", with_step(chapter="a\nb"), "control"),
        ("name too long", lambda rec: _body(rec, name=long_name), "name"),
        ("name empty", lambda rec: _body(rec, name="   "), "name"),
        ("name with a control character", lambda rec: _body(rec, name="Tour\x1bx"), "control"),
        # A step's picture is ``step-<its id>.png``, nothing else: another step's file, a file of no step, or two
        # steps' files swapped are all refused (Replace picture writes by the step's id, so a swapped record would
        # leave two steps on one file and an orphan).
        ("a picture named for another step", with_step(image="step-000000000000.png"), "must be step-"),
        ("two steps' pictures swapped", lambda rec: _body(rec, steps=[
            {**rec["steps"][0], "image": rec["steps"][1]["image"]}, {**rec["steps"][1], "image": rec["steps"][0]["image"]},
        ]), "must be step-"),
        ("a picture name that is not a step's", with_step(image="../../capture.json"), "image"),
        ("a size that is not the picture's", with_step(width=41), "40x30"),
        ("an unknown kind", with_step(kind="toast"), "kind"),
        ("an unknown placement", with_step(placement="middle"), "placement"),
        ("a bad step id", with_step(id="not-an-id"), "id"),
        # The second step takes the first's id AND its picture (each step's picture is named by its id), so it is
        # the uniqueness of the ids that refuses it.
        ("two steps with one id", lambda rec: _body(rec, steps=[rec["steps"][0], {**rec["steps"][1], "id": rec["steps"][0]["id"], "image": rec["steps"][0]["image"]}]), "same id"),
        ("two steps with one picture", lambda rec: _body(rec, steps=[rec["steps"][0], {**rec["steps"][1], "image": rec["steps"][0]["image"]}]), "must be step-"),
        ("an unknown step field", with_step(notes="x"), "notes"),
        ("an unknown document field", lambda rec: _body(rec, owner_id="me"), "owner_id"),
        ("another version", lambda rec: _body(rec, version=2), "version"),
        ("no revision", lambda rec: {k: v for k, v in _body(rec).items() if k != "revision"}, "revision"),
        ("a revision that is not a number", lambda rec: _body(rec, revision="2"), "revision"),
        ("a bad source id", with_step(source={"capture_id": "zzz", "annotated": False}), "capture_id"),
        ("not an object", lambda rec: [1, 2], "not valid"),
        ("not json", lambda rec: "{not json", "not valid JSON"),
    ]


BAD = _bad_cases()


@pytest.mark.parametrize("label, make, said", BAD, ids=[b[0] for b in BAD])
def test_a_bad_document_is_refused_with_400_and_nothing_is_written(rig, monkeypatch, label, make, said):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 40, 30)
    w = _new(c)
    _add(c, w["id"], cid)
    rec = _add(c, w["id"], cid)
    before = _stored(rig, w["id"])
    pictures = _pictures(rig, w["id"])
    r = _put(c, w["id"], make(rec))
    assert r.status_code == 400, f"{label}: {r.status_code} {r.text}"
    detail = r.json()["detail"]
    assert isinstance(detail, str) and said.lower() in detail.lower(), f"{label}: {detail!r}"
    assert _stored(rig, w["id"]) == before, label
    assert _pictures(rig, w["id"]) == pictures, label
    assert c.get(f"/api/walkthroughs/{w['id']}").json()["revision"] == before["revision"]
    assert auth_store.list_audit(action="walkthrough.update") == [], "a refused save records nothing"


def test_too_many_steps_is_refused_at_the_cap_the_server_names(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    for _ in range(3):
        rec = _add(c, w["id"], cid)
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 2)
    r = _put(c, w["id"], _body(rec))
    assert r.status_code == 400 and "at most 2 steps" in r.json()["detail"]
    assert _stored(rig, w["id"])["revision"] == rec["revision"]
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 3)
    assert _put(c, w["id"], _body(rec)).status_code == 200


def test_a_put_body_over_the_cap_is_413_and_nothing_is_written(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    rec = _add(c, w["id"], cid)
    monkeypatch.setattr(walkthroughs, "MAX_PUT_BYTES", 300)
    body = _body(rec, steps=[{**rec["steps"][0], "text": "t" * 600}])
    r = _put(c, w["id"], body)
    assert r.status_code == 413, r.text
    assert r.json()["detail"] == "A walkthrough's document may not exceed 2 MB.", "the line names the real cap"
    assert _stored(rig, w["id"]) == rec
    # Without a length header the stream is capped as it arrives, the same answer.
    r = c.put(f"/api/walkthroughs/{w['id']}", content=iter([json.dumps(body).encode("utf-8")]), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    monkeypatch.setattr(walkthroughs, "MAX_PUT_BYTES", 2 * 1024 * 1024)
    assert _put(c, w["id"], body).status_code == 200
    assert walkthroughs.MAX_PUT_BYTES == 2 * 1024 * 1024


def test_a_picture_missing_from_disk(rig, monkeypatch):
    """A step whose file went missing behind the app's back: its image is a 404, a save naming it is
    refused (the folder does not hold it), and a save without it goes through."""
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    _add(c, w["id"], cid)
    rec = _add(c, w["id"], cid)
    lost = rec["steps"][0]
    (_folder(rig, w["id"]) / lost["image"]).unlink()
    assert c.get(f"/api/walkthroughs/{w['id']}/steps/{lost['id']}/image").status_code == 404
    r = _put(c, w["id"], _body(rec))
    assert r.status_code == 400 and "does not hold" in r.json()["detail"]
    assert _put(c, w["id"], _body(rec, steps=[rec["steps"][1]])).status_code == 200


def test_every_file_is_proven_under_its_folder(rig, monkeypatch, tmp_path):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    rec = _add(c, w["id"], cid)
    sid = rec["steps"][0]["id"]
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png((8, 8)))
    # A record edited on disk to name a file elsewhere: never served.
    stored = _stored(rig, w["id"])
    for name in ("../outside.png", str(outside), "..\\outside.png", "walkthrough.json"):
        stored["steps"][0]["image"] = name
        (_folder(rig, w["id"]) / "walkthrough.json").write_text(json.dumps(stored), encoding="utf-8")
        assert walkthroughs.step_image_path(w["id"], sid) is None or name == "walkthrough.json", name
        r = c.get(f"/api/walkthroughs/{w['id']}/steps/{sid}/image")
        assert r.status_code == 404 or name == "walkthrough.json", name
    assert file_under(_folder(rig, w["id"]), "../outside.png") is None
    assert file_under(_folder(rig, w["id"]), str(outside)) is None
    assert file_under(_folder(rig, w["id"]), "missing.png") is None
    assert file_under(_folder(rig, w["id"]), "walkthrough.json") == (_folder(rig, w["id"]) / "walkthrough.json").resolve()
    # ``held_pictures`` reads only step pictures, by the files' own headers.
    (_folder(rig, w["id"]) / "step-000000000000.png").write_bytes(b"not a png")
    (_folder(rig, w["id"]) / "stray.png").write_bytes(_png((3, 3)))
    assert walkthroughs.held_pictures(w["id"]) == {f"step-{sid}.png": (8, 8)}
    assert walkthroughs.held_pictures("nope") == {} and walkthroughs.held_pictures("000000000000") == {}


# -- duplicate -----------------------------------------------------------------------------------

def test_duplicate_copies_the_record_and_the_pictures(rig, monkeypatch):
    admin, other = rig["admin"], rig["other"]
    cid = _still(rig, monkeypatch, 40, 30, RED)
    w = _new(admin, "Tour")
    _add(admin, w["id"], cid)
    rec = _add(admin, w["id"], cid)
    rec["steps"][0]["text"] = "Hello"
    rec["steps"][0]["chapter"] = "One"
    rec["steps"][0]["hotspot"] = _hotspot()
    assert _put(admin, w["id"], _body(rec)).status_code == 200
    r = admin.post(f"/api/walkthroughs/{w['id']}/duplicate")
    assert r.status_code == 200, r.text
    copy = r.json()
    assert copy["id"] != w["id"] and copy["name"] == "Tour (copy)" and copy["revision"] == 1
    assert [{k: v for k, v in s.items()} for s in copy["steps"]] == rec["steps"]
    assert copy["owner_id"] == rec["owner_id"]
    assert _pictures(rig, copy["id"]) == _pictures(rig, w["id"])
    assert _pixel(admin, copy["id"], copy["steps"][0]["id"]) == RED
    assert [x["id"] for x in admin.get("/api/walkthroughs").json()["walkthroughs"]] == [copy["id"], w["id"]]
    # Separate files: deleting the original leaves the copy whole.
    assert admin.delete(f"/api/walkthroughs/{w['id']}").status_code == 204
    assert _pixel(admin, copy["id"], copy["steps"][0]["id"]) == RED
    assert admin.get(f"/api/walkthroughs/{copy['id']}").json()["steps"] == copy["steps"]
    # The copy an administrator makes of another editor's walkthrough is the administrator's.
    theirs = _new(other, "Theirs")
    mine = admin.post(f"/api/walkthroughs/{theirs['id']}/duplicate").json()
    assert mine["owner_id"] != theirs["owner_id"] and other.get(f"/api/walkthroughs/{mine['id']}").status_code == 403
    rows = auth_store.list_audit(action="walkthrough.duplicate")
    assert rows[-1]["detail"] == f"{w['id']} -> {copy['id']}: Tour" and rows[-1]["entity_id"] == copy["id"]
    assert "Hello" not in json.dumps(rows)


# -- ownership ---------------------------------------------------------------------------------

#: Every walkthrough route, with a body that passes validation, so a refusal is the guard's and
#: not FastAPI's 422. ``{wid}`` / ``{sid}`` / ``{cid}`` are filled from the owner's own records.
WALKTHROUGH_ROUTES: dict[tuple[str, str], dict | None] = {
    ("GET", "/api/walkthroughs/{wid}"): None,
    ("PUT", "/api/walkthroughs/{wid}"): {"version": 1, "name": "x", "revision": 1, "steps": []},
    ("DELETE", "/api/walkthroughs/{wid}"): None,
    ("POST", "/api/walkthroughs/{wid}/steps"): {"capture_id": "{cid}"},
    ("POST", "/api/walkthroughs/{wid}/steps/{sid}/picture"): {"capture_id": "{cid}"},
    ("GET", "/api/walkthroughs/{wid}/steps/{sid}/image"): None,
    ("GET", "/api/walkthroughs/{wid}/transcript"): None,
    ("POST", "/api/walkthroughs/{wid}/duplicate"): None,
}


def _fill(template: str, ids: dict) -> str:
    for key, value in ids.items():
        template = template.replace("{" + key + "}", value)
    return template


def _call(client: TestClient, method: str, path: str, body, ids: dict):
    if body is None:
        return client.request(method, path)
    return client.request(method, path, json={k: _fill(v, ids) if isinstance(v, str) else v for k, v in body.items()})


def test_another_editor_is_refused_every_route_and_an_administrator_is_not(rig, monkeypatch):
    owner, other, admin = rig["owner"], rig["other"], rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8, who="owner")
    w = _new(owner, "Olive's")
    rec = _add(owner, w["id"], cid)
    ids = {"wid": w["id"], "sid": rec["steps"][0]["id"], "cid": cid}
    before = _stored(rig, w["id"])

    refused = []
    for (method, template), body in WALKTHROUGH_ROUTES.items():
        r = _call(other, method, _fill(template, ids), body, ids)
        if r.status_code != 403 or r.json().get("detail") != "This walkthrough is not yours.":
            refused.append(f"{method} {template} -> {r.status_code} {r.text[:120]}")
    assert refused == [], "these routes did not refuse another editor with a 403:\n  " + "\n  ".join(refused)
    assert _stored(rig, w["id"]) == before and _pictures(rig, w["id"]) == [rec["steps"][0]["image"]]
    assert [x["id"] for x in other.get("/api/walkthroughs").json()["walkthroughs"]] == []
    assert [x["id"] for x in owner.get("/api/walkthroughs").json()["walkthroughs"]] == [w["id"]]
    assert [x["id"] for x in admin.get("/api/walkthroughs").json()["walkthroughs"]] == [w["id"]]

    # The administrator may use every route on it (the delete last).
    assert admin.get(f"/api/walkthroughs/{w['id']}").status_code == 200
    assert admin.get(f"/api/walkthroughs/{w['id']}/steps/{ids['sid']}/image").status_code == 200
    assert admin.get(f"/api/walkthroughs/{w['id']}/transcript").status_code == 200
    assert admin.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": cid}).status_code == 200
    assert admin.post(f"/api/walkthroughs/{w['id']}/steps/{ids['sid']}/picture", json={"capture_id": cid}).status_code == 200
    current = admin.get(f"/api/walkthroughs/{w['id']}").json()
    assert _put(admin, w["id"], _body(current, name="Admin saved")).status_code == 200
    assert admin.post(f"/api/walkthroughs/{w['id']}/duplicate").status_code == 200
    assert admin.delete(f"/api/walkthroughs/{w['id']}").status_code == 204
    assert owner.get(f"/api/walkthroughs/{w['id']}").status_code == 404
    # A missing walkthrough is a 404 for everyone, on every route.
    absent = {"wid": "aabbccddeeff", "sid": ids["sid"], "cid": cid}
    for who in ("owner", "other", "admin"):
        for (method, template), body in WALKTHROUGH_ROUTES.items():
            r = _call(rig[who], method, _fill(template, absent), body, absent)
            assert r.status_code == 404, f"{who} {method} {template} -> {r.status_code}"


def test_the_sweep_covers_every_walkthrough_route_the_app_has(routes):
    """An anti-rot guard on the guard above: a new ``{wid}`` route must join the table."""
    live = {(r.method, r.path) for r in routes if "{wid}" in r.path}
    assert sorted(live - set(WALKTHROUGH_ROUTES)) == [], "routes missing from WALKTHROUGH_ROUTES"
    assert sorted(set(WALKTHROUGH_ROUTES) - live) == [], "WALKTHROUGH_ROUTES names routes the app no longer has"
    plain = {(r.method, r.path) for r in routes if r.path.startswith("/api/walkthroughs") and "{wid}" not in r.path}
    assert plain == {("GET", "/api/walkthroughs"), ("POST", "/api/walkthroughs")}


def test_every_route_needs_a_session(rig):
    anonymous = TestClient(rig["app"], client=LOOPBACK)
    assert anonymous.get("/api/walkthroughs").status_code == 401
    assert anonymous.post("/api/walkthroughs", json={"name": "x"}).status_code == 401
    assert anonymous.get("/api/walkthroughs/aabbccddeeff").status_code == 401
    assert anonymous.get("/api/walkthroughs/aabbccddeeff/steps/aabbccddeeff/image").status_code == 401


def test_the_routes_work_from_another_machine(rig, monkeypatch):
    """Walkthroughs touch no screen: the browser edition builds and plays them; only taking a still
    is this computer's."""
    cid = _still(rig, monkeypatch, 8, 8)
    remote = _signed_in_from(rig["app"], ("10.20.30.40", 50123))
    w = remote.post("/api/walkthroughs", json={"name": "Remote"}).json()
    rec = remote.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": cid}).json()
    sid = rec["steps"][0]["id"]
    assert remote.get(f"/api/walkthroughs/{w['id']}/steps/{sid}/image").status_code == 200
    assert _put(remote, w["id"], _body(rec)).status_code == 200
    assert remote.post(f"/api/walkthroughs/{w['id']}/steps/{sid}/picture", json={"capture_id": cid}).status_code == 200
    assert remote.post(f"/api/walkthroughs/{w['id']}/duplicate").status_code == 200
    assert remote.delete(f"/api/walkthroughs/{w['id']}").status_code == 204
    assert remote.post("/api/captures", json={"x": 0, "y": 0, "width": 8, "height": 8}).status_code == 403, "a still is still local"


# -- the audit rows -------------------------------------------------------------------------------

def test_the_audit_rows_carry_counts_and_names_never_a_steps_text(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 40, 30)
    w = _new(c, "Audited tour")
    rec = _add(c, w["id"], cid)
    sid = rec["steps"][0]["id"]
    rec["steps"][0]["text"] = "SECRET-TEXT-OF-STEP"
    rec["steps"][0]["chapter"] = "SECRET-CHAPTER"
    assert _put(c, w["id"], _body(rec)).status_code == 200
    assert c.post(f"/api/walkthroughs/{w['id']}/steps/{sid}/picture", json={"capture_id": cid}).status_code == 200
    copy = c.post(f"/api/walkthroughs/{w['id']}/duplicate").json()
    assert c.delete(f"/api/walkthroughs/{w['id']}").status_code == 204
    rows = auth_store.list_audit(limit=100)
    actions = [(r["action"], r["entity"], r["entity_id"], r["detail"]) for r in rows if r["action"].startswith("walkthrough.")]
    assert actions == [
        ("walkthrough.delete", "walkthrough", w["id"], "Audited tour"),
        ("walkthrough.duplicate", "walkthrough", copy["id"], f"{w['id']} -> {copy['id']}: Audited tour"),
        ("walkthrough.replace_picture", "walkthrough", w["id"], f"step {sid}: capture {cid}"),
        ("walkthrough.update", "walkthrough", w["id"], "1 step, 1 chapter"),
        ("walkthrough.add_step", "walkthrough", w["id"], f"capture {cid} -> step 1 of 1"),
        ("walkthrough.create", "walkthrough", w["id"], "Audited tour"),
    ]
    assert "SECRET" not in json.dumps(rows), "no step text and no chapter name reaches the log"
    assert all(r["user_id"] for r in rows if r["action"].startswith("walkthrough."))


# -- the limits rows ----------------------------------------------------------------------------

def test_the_limits_rows_name_the_walkthrough_constants():
    """The constants behind the rows (``tests/test_docs.py`` checks every row's line references)."""
    text = LIMITS_MD.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| Walkthrough")]
    assert rows, "no Walkthrough rows in docs/reference/limits.md"
    joined = "\n".join(rows)
    for number in (walkthroughs.MAX_STEPS, walkthroughs.MAX_CHAPTER_CHARS, walkthroughs.MAX_NAME_CHARS,
                   walkthroughs.MIN_HOTSPOT_SIDE, walkthroughs.MAX_PUT_BYTES // 1024 ** 2):
        assert str(number) in joined, number
    assert "1,000" in joined or "1000" in joined
    assert "2,000" in joined or "2000" in joined
    for name in ("MAX_STEPS", "MAX_STEP_TEXT", "MAX_INSTRUCTION_CHARS", "MAX_CHAPTER_CHARS", "MIN_HOTSPOT_SIDE", "MAX_PUT_BYTES"):
        assert name in text, name


# -- the fix round: the name's rule on the type, delete under the lock, a non-PNG source, odd bodies, the copy's name ----

def test_a_name_with_a_control_character_is_refused_on_create_and_a_created_record_saves_as_returned(rig):
    """The rule is on the ``WalkthroughName`` TYPE, so the create refuses what the save refuses (m1): before, a
    create accepted ``"Tour\\x1bx"`` and the builder's first untouched save of it was then refused."""
    c = rig["admin"]
    root = rig["tmp"] / "walkthroughs"
    before = {p.name for p in root.iterdir()} if root.is_dir() else set()
    r = c.post("/api/walkthroughs", json={"name": "Tour\x1bx"})
    assert r.status_code == 422, r.text
    assert "control" in r.text
    after = {p.name for p in root.iterdir()} if root.is_dir() else set()
    assert after == before, "a refused create makes no folder"
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == []
    # What the create accepts, the save accepts as returned: the record round-trips untouched.
    rec = _new(c, "  Plain tour  ")
    assert rec["name"] == "Plain tour"
    assert _put(c, rec["id"], _body(rec)).status_code == 200


def test_delete_waits_for_the_walkthroughs_lock(rig):
    """A delete racing a save (the list in one tab, the builder in another): the delete takes the walkthrough's
    own lock, so the folder survives until the saver lets go, and the lock leaves the table in the same critical
    section (m4). Before, ``rmtree`` ran under no lock and ``_write_record`` could resurrect an empty record."""
    c = rig["admin"]
    wid = _new(c)["id"]
    lock = walkthroughs._lock(wid)
    assert lock.acquire(timeout=1)
    results: list[bool] = []
    worker = threading.Thread(target=lambda: results.append(walkthroughs.delete_walkthrough(wid)))
    worker.start()
    worker.join(0.5)
    try:
        assert worker.is_alive(), "the delete must wait for the lock"
        assert _folder(rig, wid).is_dir() and results == []
    finally:
        lock.release()
    worker.join(5)
    assert not worker.is_alive() and results == [True]
    assert not _folder(rig, wid).exists()
    assert wid not in walkthroughs._locks, "the lock left the table inside the critical section"
    assert c.get(f"/api/walkthroughs/{wid}").status_code == 404


def test_a_capture_whose_picture_is_not_a_png_cannot_be_a_step(rig, monkeypatch):
    """The copy reads the picture's own header: a still whose file is not a PNG is refused with 400, no step is
    added, the temporary copy is gone and the folder holds only the record (m5a)."""
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    (rig["tmp"] / "captures" / cid / "still.png").write_bytes(b"GIF89a not a png at all")
    w = _new(c)
    r = c.post(f"/api/walkthroughs/{w['id']}/steps", json={"capture_id": cid})
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "The capture's picture is not a PNG; it cannot be a step."
    assert c.get(f"/api/walkthroughs/{w['id']}").json()["steps"] == []
    assert sorted(p.name for p in _folder(rig, w["id"]).iterdir()) == ["walkthrough.json"]
    assert auth_store.list_audit(action="walkthrough.add_step") == []


def test_a_body_that_is_not_json_gets_the_routes_own_line_whatever_python_says(rig, monkeypatch):
    """An integer of more than 4,300 digits makes ``json.loads`` raise a plain ``ValueError`` (not a
    ``JSONDecodeError``); it is the route's "not valid JSON" line, never the app-wide handler's Python text (n1)."""
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    rec = _add(c, w["id"], cid)
    r = _put(c, w["id"], '{"revision": ' + "9" * 5000 + "}")
    assert r.status_code == 400, r.text
    assert r.json()["detail"].startswith("The walkthrough is not valid JSON:")
    assert "sys.set_int_max_str_digits" not in r.json()["detail"]
    assert _stored(rig, w["id"]) == rec


def test_the_copys_name_keeps_copy_and_a_chapter_is_bounded_after_the_strip(rig, monkeypatch):
    """n6: a 120-character name's copy is shortened to make room for " (copy)"; 80 spaces and a letter are
    one letter, and stored so."""
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    longest = "n" * walkthroughs.MAX_NAME_CHARS
    w = _new(c, longest)
    assert w["name"] == longest
    copy = c.post(f"/api/walkthroughs/{w['id']}/duplicate").json()
    assert copy["name"].endswith(" (copy)") and len(copy["name"]) == walkthroughs.MAX_NAME_CHARS
    assert copy["name"].startswith("n" * (walkthroughs.MAX_NAME_CHARS - len(" (copy)")))
    rec = _add(c, w["id"], cid)
    rec["steps"][0]["chapter"] = " " * walkthroughs.MAX_CHAPTER_CHARS + "x"
    r = _put(c, w["id"], _body(rec))
    assert r.status_code == 200, r.text
    assert r.json()["steps"][0]["chapter"] == "x"
    # The stripped 80 are within the bound, whatever the spaces around them.
    r = _put(c, w["id"], _body(c.get(f"/api/walkthroughs/{w['id']}").json(), steps=[{**r.json()["steps"][0], "chapter": " " + "c" * walkthroughs.MAX_CHAPTER_CHARS + " "}]))
    assert r.status_code == 200, r.text
    assert r.json()["steps"][0]["chapter"] == "c" * walkthroughs.MAX_CHAPTER_CHARS


# -- the addendum: the instruction and the transcript --------------------------------------------------

#: The same document as ``frontend/src/lib/walkthrough.test.ts`` holds under the same name, hand-synchronised:
#: the two tests assert the same Markdown byte for byte, each naming the other. Change one, change both.
TRANSCRIPT_FIXTURE = {
    "name": "  Fixture tour ",
    "steps": [
        {"id": "aaaaaaaaaaaa", "chapter": "Start", "instruction": "Open the menu.\nPick Import.", "text": "ignored when there is an instruction"},
        {"id": "bbbbbbbbbbbb", "chapter": "", "instruction": "", "text": "Press Save"},
        {"id": "cccccccccccc", "chapter": " Finish ", "instruction": "   ", "text": ""},
        # A RAW record's CRLF and CR (the API stores LF; a hand-made record may not): one line break each, both sides.
        {"id": "dddddddddddd", "chapter": "", "instruction": "Line one\r\nLine two\rthree", "text": ""},
    ],
}
TRANSCRIPT_EXPECTED = (
    "# Fixture tour\n\n## Start\n\n1. Open the menu.\n   Pick Import.\n2. Press Save\n\n## Finish\n\n3. (no instruction yet)\n"
    "4. Line one\n   Line two\n   three\n"
)


def test_narration_of_is_the_instruction_else_the_text():
    """The ONE fallback rule (the page's ``narrationOf`` in ``walkthrough.test.ts`` asserts the same cases)."""
    assert walkthroughs.narration_of({"instruction": "Drag it onto the canvas", "text": "Drag"}) == "Drag it onto the canvas"
    assert walkthroughs.narration_of({"instruction": "   ", "text": "Press Save"}) == "Press Save"
    assert walkthroughs.narration_of({"instruction": "", "text": "Press Save"}) == "Press Save"
    assert walkthroughs.narration_of({"text": "Old record"}) == "Old record"
    assert walkthroughs.narration_of({"instruction": "", "text": ""}) == ""
    assert walkthroughs.narration_of({}) == ""


def test_transcript_markdown_matches_the_pages_transcript_byte_for_byte():
    """``frontend/src/lib/walkthrough.test.ts`` > "transcript … the same bytes as the backend's" holds the same
    fixture and the same expected text."""
    assert walkthroughs.transcript_markdown(TRANSCRIPT_FIXTURE) == TRANSCRIPT_EXPECTED
    assert walkthroughs.transcript_markdown({"name": "Empty", "steps": []}) == "# Empty\n\n(no steps yet)\n"
    assert walkthroughs.transcript_markdown({"name": "", "steps": []}) == "# Walkthrough\n\n(no steps yet)\n"
    # No chapter at all: numbered items straight under the title.
    assert walkthroughs.transcript_markdown({"name": "Flat", "steps": [{"text": "One"}, {"instruction": "Two"}]}) == "# Flat\n\n1. One\n2. Two\n"


def test_a_steps_instruction_round_trips_and_old_records_read_as_empty(rig, monkeypatch):
    c = rig["admin"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c)
    rec = _add(c, w["id"], cid)
    assert rec["steps"][0]["instruction"] == "", "a new step has an empty instruction"
    rec["steps"][0]["instruction"] = "Drag the\r\nText file input\ronto the canvas"
    rec["steps"][0]["text"] = "Drag it"
    r = _put(c, w["id"], _body(rec))
    assert r.status_code == 200, r.text
    assert r.json()["steps"][0]["instruction"] == "Drag the\nText file input\nonto the canvas", "CRLF and CR normalised"
    assert c.get(f"/api/walkthroughs/{w['id']}").json()["steps"][0]["instruction"] == "Drag the\nText file input\nonto the canvas"
    assert "instruction" not in c.get("/api/walkthroughs").json()["walkthroughs"][0], "the list carries no instructions"
    # A record from before the field: read as "", saved as returned, and the transcript falls back to the text.
    stored = _stored(rig, w["id"])
    del stored["steps"][0]["instruction"]
    (_folder(rig, w["id"]) / "walkthrough.json").write_text(json.dumps(stored), encoding="utf-8")
    old = c.get(f"/api/walkthroughs/{w['id']}").json()
    assert old["steps"][0]["instruction"] == ""
    assert c.get(f"/api/walkthroughs/{w['id']}/transcript").text == f"# {old['name']}\n\n1. Drag it\n"
    assert _put(c, w["id"], _body(old)).status_code == 200
    assert _stored(rig, w["id"])["steps"][0]["instruction"] == ""


def test_the_transcript_route_answers_markdown_and_a_download(rig, monkeypatch):
    c, other = rig["admin"], rig["other"]
    cid = _still(rig, monkeypatch, 8, 8)
    w = _new(c, "Tour: Part 1/2")
    _add(c, w["id"], cid)
    rec = _add(c, w["id"], cid)
    rec["steps"][0].update({"chapter": "Start", "instruction": "Open the menu.\nPick Import."})
    rec["steps"][1].update({"text": "Press Save"})
    assert _put(c, w["id"], _body(rec)).status_code == 200
    rows_before = len(auth_store.list_audit(limit=100))
    r = c.get(f"/api/walkthroughs/{w['id']}/transcript")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.headers["cache-control"] == "no-store" and "content-disposition" not in r.headers
    assert r.content == b"# Tour: Part 1/2\n\n## Start\n\n1. Open the menu.\n   Pick Import.\n2. Press Save\n"
    assert r.content == walkthroughs.transcript_markdown(c.get(f"/api/walkthroughs/{w['id']}").json()).encode("utf-8")
    d = c.get(f"/api/walkthroughs/{w['id']}/transcript?download=1")
    assert d.status_code == 200 and d.content == r.content
    assert d.headers["content-disposition"] == 'attachment; filename="Tour-Part-12.md"'
    assert len(auth_store.list_audit(limit=100)) == rows_before, "a read records nothing"
    assert other.get(f"/api/walkthroughs/{w['id']}/transcript").status_code == 403
    assert TestClient(rig["app"], client=LOOPBACK).get(f"/api/walkthroughs/{w['id']}/transcript").status_code == 401
    assert c.get("/api/walkthroughs/aabbccddeeff/transcript").status_code == 404
