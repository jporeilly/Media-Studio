"""The Workshop video canvas's server side (C2, C6): the Studio setting **Content Editor address** (the editor's own
default, the validation, admin-only through the PUT), the course published through the Content Editor's own API over
a faked editor (``httpx.MockTransport``: the config, the diff with its summary, a publish that commits and pushes, the
editor's own error text, the one plain line when nothing is listening) with the ``course.publish`` row, and the
per-workshop status the Workshop stage reads (the walkthrough the canvas would open, its project, its video, whether
the guide holds the link). No test reaches a running editor; nothing on 8000 or 5273 is ever called.
"""

from __future__ import annotations

import json

import httpx
import pytest

from api import store as auth_store
from services import content_editor, processing, studio_settings, vimeo
from test_generate import _FakeVideoProcessor, _wait_job
from test_vimeo import HASH, MANIFEST, PLACEHOLDER_GUIDE, TOKEN, VIDEO_ID, _crlf, fake  # noqa: F401 - the fake fixture
from test_walkthroughs import _hotspot, _still
from test_workshops import _course, _png, _workshop, rig  # noqa: F401 - the rig fixture
from utils.config import config

LINK = f"https://vimeo.com/{VIDEO_ID}/{HASH}"


# -- the faked editor ---------------------------------------------------------------------------------------------------------

class FakeEditor:
    """The Content Editor's publish API over an ``httpx.MockTransport``: every request kept; the answers a test sets."""

    def __init__(self, *, up_to_date=False, error=None, down=False, new_course=False):
        self.requests: list[httpx.Request] = []
        self.up_to_date = up_to_date
        self.error = error  # (status, detail) for every call
        self.down = down
        self.new_course = new_course

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("[WinError 10061] No connection could be made", request=request)
        if self.error:
            status, detail = self.error
            return httpx.Response(status, json={"detail": detail})
        path = request.url.path
        if request.method == "GET" and path == "/api/publish/config":
            return httpx.Response(200, json={"url": "https://github.com/jporeilly/Pentaho-Courses.git", "ref": "main"})
        if request.method == "GET" and path.endswith("/publish/diff"):
            course = path.split("/")[3]
            if course == "missing":
                return httpx.Response(404, json={"detail": f"No such course: {course}"})
            if self.up_to_date:
                diff = {"added": [], "modified": [], "removed": []}
            else:
                diff = {"added": [] if not self.new_course else ["01-first-win/guide.md", "course.json"], "modified": [] if self.new_course else ["01-first-win/guide.md", "01-first-win/manifest.json"], "removed": []}
            return httpx.Response(200, json={"course": course, "remoteCommit": "abc1234def", "newCourse": self.new_course, "upToDate": self.up_to_date, **diff, "skippedIgnored": [".env"]})
        if request.method == "POST" and path.endswith("/publish"):
            course = path.split("/")[3]
            body = json.loads(request.content or b"{}")
            if self.up_to_date:
                return httpx.Response(200, json={"ok": True, "upToDate": True, "commit": "abc1234def", "authoring": {"committed": False, "upToDate": True} if body.get("commit") else None, "skippedIgnored": []})
            return httpx.Response(200, json={
                "ok": True, "upToDate": False, "commit": "9f8e7d6c5b4a", "changed": {"added": 0, "modified": 2, "removed": 0},
                "authoring": {"committed": True, "commit": "1234567890ab"} if body.get("commit") else None, "skippedIgnored": [], "course": course,
            })
        return httpx.Response(404, json={"detail": f"No such endpoint: {path}"})


@pytest.fixture
def editor(monkeypatch):
    e = FakeEditor()
    monkeypatch.setattr(content_editor, "TRANSPORT", e.transport)
    return e


# -- the setting -----------------------------------------------------------------------------------------------------------------

def test_the_content_editor_address_defaults_to_the_editors_own_port_and_is_validated(rig):
    assert studio_settings.content_editor_url() == "http://localhost:8000" == studio_settings.CONTENT_EDITOR_DEFAULT_URL
    assert rig["owner"].get("/api/settings/studio").json()["settings"]["content_editor_url"] == "http://localhost:8000"
    for good, stored in (("http://127.0.0.1:8010/", "http://127.0.0.1:8010"), (" https://editor.example.test ", "https://editor.example.test"), ("", "")):
        studio_settings.update_settings({"content_editor_url": good})
        assert config._config["content_editor_url"] == stored, good
    assert studio_settings.content_editor_url() == "http://localhost:8000", "empty means the default"
    for bad, expected in (("localhost:8000", "http or https"), ("ftp://x", "http or https"), ("http://localhost:8000/api", "nothing after it"),
                          ("http://a b:8000", "http or https"), (5, "must be text"), ("http://" + "x" * 200, "longer than")):
        with pytest.raises(ValueError) as exc:
            studio_settings.update_settings({"content_editor_url": bad})
        assert expected in str(exc.value), bad
    # Through the API: an administrator's, with the audit row naming the key.
    r = rig["admin"].put("/api/settings/studio", json={"content_editor_url": "http://localhost:8010"})
    assert r.status_code == 200 and r.json()["settings"]["content_editor_url"] == "http://localhost:8010"
    assert rig["owner"].put("/api/settings/studio", json={"content_editor_url": "http://localhost:8011"}).status_code == 403
    assert rig["admin"].put("/api/settings/studio", json={"content_editor_url": "nope"}).status_code == 400
    rows = [x for x in auth_store.list_audit(limit=20) if x["action"] == "settings.update"]
    assert rows and rows[0]["detail"] == "content_editor_url" and "8010" not in rows[0]["detail"]


# -- the proxy (C6) --------------------------------------------------------------------------------------------------------------

def test_the_config_the_diff_and_the_publish_go_through_the_editor_and_leave_the_row(rig, editor):
    c = rig["owner"]
    config._config["content_editor_url"] = "http://localhost:8010"
    cfg = c.get("/api/courses/publish/config").json()
    assert cfg == {"address": "http://localhost:8010", "reachable": True, "url": "https://github.com/jporeilly/Pentaho-Courses.git", "ref": "main", "message": None}
    assert str(editor.requests[0].url) == "http://localhost:8010/api/publish/config"

    d = c.get("/api/courses/pdi-2hr-lab/publish/diff").json()
    assert d["modified"] == ["01-first-win/guide.md", "01-first-win/manifest.json"] and d["upToDate"] is False
    assert d["summary"] == "2 files change upstream: 01-first-win/guide.md, 01-first-win/manifest.json"
    assert editor.requests[1].url.path == "/api/courses/pdi-2hr-lab/publish/diff"

    before = len(auth_store.list_audit(limit=100))
    r = c.post("/api/courses/pdi-2hr-lab/publish", json={"message": "Walkthrough video: Your First Win"})
    assert r.status_code == 200, r.text
    sent = editor.requests[2]
    assert sent.method == "POST" and sent.url.path == "/api/courses/pdi-2hr-lab/publish"
    assert json.loads(sent.content) == {"message": "Walkthrough video: Your First Win", "commit": True}, "the editor commits the authoring repository too"
    out = r.json()
    assert out["ok"] is True and out["commit"] == "9f8e7d6c5b4a" and out["summary"] == "committed 9f8e7d6 and pushed (2 files), the authoring repository committed too"
    rows = auth_store.list_audit(limit=100)
    assert len(rows) == before + 1
    assert (rows[0]["action"], rows[0]["entity"], rows[0]["entity_id"], rows[0]["detail"]) == (
        "course.publish", "course", "pdi-2hr-lab", "pdi-2hr-lab: Walkthrough video: Your First Win -> committed 9f8e7d6 and pushed (2 files), the authoring repository committed too")

    # Nothing to publish: the editor says up to date, and so does the summary; an empty message leaves the editor its default.
    editor.up_to_date = True
    r = c.post("/api/courses/pdi-2hr-lab/publish", json={})
    assert r.status_code == 200 and r.json()["upToDate"] is True and r.json()["summary"] == "nothing to publish: the repository was up to date"
    assert json.loads(editor.requests[-1].content) == {"commit": True}
    assert auth_store.list_audit(limit=1)[0]["detail"] == "pdi-2hr-lab: (the editor's default message) -> nothing to publish: the repository was up to date"
    assert c.get("/api/courses/pdi-2hr-lab/publish/diff").json()["summary"] == "Nothing to publish: the repository already holds the course as it is."
    editor.up_to_date, editor.new_course = False, True
    assert c.get("/api/courses/pdi-2hr-lab/publish/diff").json()["summary"] == "A new course: 2 files go upstream."


def test_an_editor_error_is_its_own_text_and_a_dead_editor_is_one_plain_line(rig, monkeypatch):
    c = rig["owner"]
    down = FakeEditor(down=True)
    monkeypatch.setattr(content_editor, "TRANSPORT", down.transport)
    cfg = c.get("/api/courses/publish/config").json()
    assert cfg["reachable"] is False and cfg["url"] is None
    assert cfg["message"] == "The Content Editor is not running at http://localhost:8000: start it, then press Publish again."
    r = c.post("/api/courses/pdi-2hr-lab/publish", json={"message": "x"})
    assert r.status_code == 502 and r.json()["detail"] == cfg["message"]
    assert c.get("/api/courses/pdi-2hr-lab/publish/diff").status_code == 502
    assert not any(x["action"] == "course.publish" for x in auth_store.list_audit(limit=50)), "no row for a publish that did not happen"

    erring = FakeEditor(error=(502, "git push failed: remote: Permission denied"))
    monkeypatch.setattr(content_editor, "TRANSPORT", erring.transport)
    r = c.post("/api/courses/pdi-2hr-lab/publish", json={"message": "x"})
    assert r.status_code == 502 and r.json()["detail"] == "git push failed: remote: Permission denied"
    cfg = c.get("/api/courses/publish/config").json()
    assert cfg["reachable"] is False and cfg["message"] == "git push failed: remote: Permission denied"

    ok = FakeEditor()
    monkeypatch.setattr(content_editor, "TRANSPORT", ok.transport)
    r = c.get("/api/courses/missing/publish/diff")
    assert r.status_code == 404 and r.json()["detail"] == "No such course: missing"
    asked = len(ok.requests)
    assert c.get("/api/courses/.hidden/publish/diff").status_code == 404 and c.post("/api/courses/a%20b/publish", json={}).status_code == 404
    assert len(ok.requests) == asked, "a name that is not a folder name never reaches the editor"
    assert c.post("/api/courses/pdi-2hr-lab/publish", json={"message": "m" * 501}).status_code == 422
    assert c.post("/api/courses/pdi-2hr-lab/publish", json={"commit": False}).status_code == 422, "the body is the message alone; the editor always commits"
    # Signed out: 401, and the editor is not asked.
    from fastapi.testclient import TestClient

    assert TestClient(rig["app"]).post("/api/courses/pdi-2hr-lab/publish", json={}).status_code == 401


def test_the_summaries_say_the_diff_and_the_answer_plainly():
    s = content_editor.diff_summary
    assert s({"added": ["a"], "modified": [], "removed": []}) == "1 file changes upstream: a"
    assert s({"added": ["a", "b"], "modified": ["c", "d", "e"], "removed": ["f", "g", "h"]}) == "8 files change upstream: a, b, c, d, e, f and 2 more"
    assert s({"added": [], "modified": [], "removed": [], "upToDate": True}) == "Nothing to publish: the repository already holds the course as it is."
    assert s({"added": ["x"], "newCourse": True}) == "A new course: 1 file goes upstream."
    p = content_editor.publish_summary
    assert p({"ok": True, "upToDate": True, "commit": "abc1234def"}) == "nothing to publish: the repository was up to date"
    assert p({"ok": True, "commit": "abcdef0123", "changed": {"added": 1, "modified": 0, "removed": 0}}) == "committed abcdef0 and pushed (1 file)"
    assert p({"ok": True, "commit": "", "changed": {}}) == "committed and pushed"
    assert p({"ok": True, "commit": "abcdef0123", "changed": {"modified": 2}, "authoring": {"committed": True}}) == "committed abcdef0 and pushed (2 files), the authoring repository committed too"
    assert content_editor.TIMEOUT_S == 30.0 and content_editor.DEFAULT_MESSAGE.format(title="X") == "Walkthrough video: X"


# -- the workshop status (C2) --------------------------------------------------------------------------------------------------------

def test_the_workshop_status_names_the_walkthrough_the_project_the_video_and_the_place(rig, monkeypatch, fake):
    c = rig["owner"]
    folder = _course(rig["courses"], "demo", summary="# Table of contents\n\n## Part one\n\n* [First](01-first/guide.md)\n* [Second](02-second/guide.md)\n")
    _workshop(folder, "01-first", "", manifest=None, images={"authored.png": _png((40, 30), (0, 128, 0))})
    (folder / "01-first" / "guide.md").write_bytes(_crlf(PLACEHOLDER_GUIDE).encode("utf-8"))
    (folder / "01-first" / "manifest.json").write_bytes(_crlf(MANIFEST).encode("utf-8"))
    _workshop(folder, "02-second", "# Second\n\n## 1. Do it\n\n1. Click **Go**.\n", manifest={"title": "Second", "order": 2})

    r = c.get("/api/courses/demo/workshop-status")
    assert r.status_code == 200, r.text
    assert r.json() == {"course": "demo", "workshops": {"01-first": {"walkthrough": None, "project": None}, "02-second": {"walkthrough": None, "project": None}}}

    rec = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"}).json()
    status = c.get("/api/courses/demo/workshop-status").json()["workshops"]
    w = status["01-first"]["walkthrough"]
    assert w["id"] == rec["id"] and w["name"] == rec["name"] and w["step_count"] == len(rec["steps"]) == 3 and w["pictured"] == 0
    assert status["01-first"]["project"] is None
    cid = _still(rig, monkeypatch, 400, 300, who="owner")
    assert c.post(f"/api/walkthroughs/{rec['id']}/steps/{rec['steps'][0]['id']}/picture", json={"capture_id": cid, "hotspot": _hotspot(10, 10, 50, 30)}).status_code == 200
    assert c.get("/api/courses/demo/workshop-status").json()["workshops"]["01-first"]["walkthrough"]["pictured"] == 1

    # The newest walkthrough of the workshop is the one; another account sees none of it.
    again = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first", "name": "Second import"}).json()
    assert c.get("/api/courses/demo/workshop-status").json()["workshops"]["01-first"]["walkthrough"]["id"] == again["id"]
    assert rig["other"].get("/api/courses/demo/workshop-status").json()["workshops"]["01-first"]["walkthrough"] is None
    assert rig["admin"].get("/api/courses/demo/workshop-status").json()["workshops"]["01-first"]["walkthrough"]["id"] == again["id"]
    assert c.delete(f"/api/walkthroughs/{again['id']}").status_code == 204

    # Rendered, on Vimeo and placed: the project with its video, its Vimeo record and the guide holding the link.
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    config._config["vimeo_token"] = TOKEN
    config._config["courses_export"] = True
    r = c.post(f"/api/walkthroughs/{rec['id']}/video-to-course", json={"name": "Tour", "notes": False, "zoom": True})
    assert r.status_code == 200, r.text
    assert _wait_job(c, r.json()["job_id"])["status"] == "done"
    entry = c.get("/api/courses/demo/workshop-status").json()["workshops"]["01-first"]
    p = entry["project"]
    assert p["id"] == r.json()["project_id"] and p["has_video"] is True and p["vimeo"]["link"] == LINK and p["vimeo"]["privacy"] == "unlisted"
    assert p["placed"] is True and p["guide"] == "demo/01-first/guide.md" and p["rendered_at"]
    assert entry["walkthrough"]["id"] == rec["id"]
    # The guide reverted: not placed any more - derived from the file, never stored.
    (folder / "01-first" / "guide.md").write_bytes(_crlf(PLACEHOLDER_GUIDE).encode("utf-8"))
    assert c.get("/api/courses/demo/workshop-status").json()["workshops"]["01-first"]["project"]["placed"] is False

    assert c.get("/api/courses/nope/workshop-status").status_code == 404
    assert c.get("/api/courses/.demo/workshop-status").status_code == 404, "a name that is not a folder name is no course"
    config._config["courses_folder"] = ""
    assert c.get("/api/courses/demo/workshop-status").status_code == 400
    assert vimeo.NO_TOKEN_MESSAGE  # the token's line is the Vimeo module's, read by the dialog's GET
