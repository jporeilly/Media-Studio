"""Video to course (R1-R4): ONE job on the walkthrough's project that renders the video, publishes it to Vimeo and
places the link in the course workshop's guide - the dialog's facts, the three phases with their progress and their
audit rows, each phase's failure (what is kept, the phase's name before the service's own message), a cancel in each
phase, run again (a new video, the old id in ``previous``, the link replaced; "just place again"), a walkthrough from
no course (no place phase), no token (400 before anything is made) and the setting off (rendered and published, the
lines in the result). The render is the generate tests' fake processor, Vimeo the Vimeo tests' faked transport, the
course a fixture under the test's own folder: no real call, no real render.
"""

from __future__ import annotations

import json

import pytest

import test_vimeo as vimeo_tests  # the module the fake reads its ids from (patched for a second video)
from api import store as auth_store
from services import course_video, jobs, processing, video_to_course, vimeo
from services import projects as store
from test_generate import _FakeVideoProcessor, _wait_job
from test_vimeo import FIGURE_GUIDE, HASH, MANIFEST, PLACEHOLDER_GUIDE, TOKEN, VIDEO_ID, FakeVimeo, _crlf, fake  # noqa: F401 - the fake fixture
from test_walkthroughs import _add, _hotspot, _new, _still
from test_workshops import _course, _png, _workshop, rig  # noqa: F401 - the rig fixture
from utils.config import config

LINK = f"https://vimeo.com/{VIDEO_ID}/{HASH}"
URL = "/api/walkthroughs/{wid}/video-to-course"


# -- the rig -----------------------------------------------------------------------------------------------------------

def _imported(rig, monkeypatch, guide=PLACEHOLDER_GUIDE, *, who="owner", manifest=MANIFEST) -> dict:
    """The demo course with one workshop holding ``guide`` (CRLF), imported by ``who`` and its first step pictured
    (a hotspot too): the walkthrough's id, the client and the course's files."""
    folder = _course(rig["courses"], "demo")
    _workshop(folder, "01-first", "", manifest=None, images={"authored.png": _png((40, 30), (0, 128, 0))})
    (folder / "01-first" / "guide.md").write_bytes(_crlf(guide).encode("utf-8"))
    if manifest is not None:
        (folder / "01-first" / "manifest.json").write_bytes(_crlf(manifest).encode("utf-8"))
    c = rig[who]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    rec = r.json()
    cid = _still(rig, monkeypatch, 400, 300, who=who)
    r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{rec['steps'][0]['id']}/picture", json={"capture_id": cid, "hotspot": _hotspot(10, 10, 50, 30)})
    assert r.status_code == 200, r.text
    return {"wid": rec["id"], "c": c, "folder": folder, "guide": folder / "01-first" / "guide.md", "manifest": folder / "01-first" / "manifest.json",
            "name": rec["name"], "steps": len(rec["steps"])}


def _plain(rig, monkeypatch, *, who="owner") -> dict:
    """A walkthrough built from stills, from no course."""
    c = rig[who]
    cid = _still(rig, monkeypatch, 400, 300, who=who)
    w = _new(c, "From stills")
    _add(c, w["id"], cid)
    return {"wid": w["id"], "c": c, "name": "From stills"}


def _ready(monkeypatch, *, token=True, allowed=True):
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.last = None
    if token:
        config._config["vimeo_token"] = TOKEN
    config._config["courses_export"] = allowed


def _start(c, wid: str, **body):
    return c.post(URL.format(wid=wid), json={"name": "User Console tour", "notes": False, "zoom": True, **body})


def _rows(*actions: str) -> list[tuple]:
    return [(x["action"], x["entity"], x["entity_id"], x["detail"]) for x in reversed(auth_store.list_audit(limit=200))
            if not actions or x["action"] in actions]


# -- the dialog's facts (R1, R3) ------------------------------------------------------------------------------------------

def test_the_defaults_say_where_the_link_goes_the_token_and_the_setting(rig, monkeypatch):
    s = _imported(rig, monkeypatch)
    c, wid = s["c"], s["wid"]
    d = c.get(URL.format(wid=wid)).json()
    assert d["name"] == s["name"] and d["privacy"] == "unlisted" and [o["value"] for o in d["privacies"]] == ["unlisted", "private", "anybody"]
    assert d["token_set"] is False and d["allowed"] is False and d["project"] is None and d["job_kind"] == "video_to_course"
    assert d["course"] == {"course": "demo", "slug": "01-first", "title": "Demo course"}
    place = d["place"]
    assert place["how"] == "placeholder" and place["line"] == 12 and place["guide"] == "demo/01-first/guide.md"
    assert place["placed"] is False and place["error"] is None and place["caption"] == s["name"]
    config._config["vimeo_token"] = TOKEN
    config._config["courses_export"] = True
    d = c.get(URL.format(wid=wid)).json()
    assert d["token_set"] is True and d["allowed"] is True

    # A guide with neither placeholder nor figure: the one button inserts after the first section heading, and says so.
    s["guide"].write_bytes(_crlf(vimeo_tests.BARE_GUIDE).encode("utf-8"))
    place = c.get(URL.format(wid=wid)).json()["place"]
    assert place["how"] == "insert" and place["heading"] == "## 1. Open it" and place["line"] == 5

    # A course that cannot be read says why, and nothing fails.
    s["guide"].unlink()
    place = c.get(URL.format(wid=wid)).json()["place"]
    assert place["how"] is None and "01-first" in place["error"], "the list leaves a workshop without its guide out, and the error names it"

    # A walkthrough from no course workshop: no course part at all.
    p = _plain(rig, monkeypatch)
    d = c.get(URL.format(wid=p["wid"])).json()
    assert d["course"] is None and d["place"] is None and d["project"] is None
    assert rig["other"].get(URL.format(wid=wid)).status_code == 403
    assert c.get(URL.format(wid="aabbccddeeff")).status_code == 404


# -- the one job (R2) -------------------------------------------------------------------------------------------------------

def test_the_one_job_renders_publishes_and_places_with_the_three_rows(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    c, wid = s["c"], s["wid"]
    _ready(monkeypatch)
    before = _rows()
    guide_before = s["guide"].read_bytes()
    r = _start(c, wid, privacy="unlisted", caption="Watch: the User Console tour")
    assert r.status_code == 200, r.text
    pid, job_id, created = r.json()["project_id"], r.json()["job_id"], r.json()["created"]
    assert created is True
    job = _wait_job(c, job_id)
    assert job["status"] == "done" and job["kind"] == "video_to_course" and job["project_id"] == pid, job
    assert job["message"] == f"Done: {LINK} placed in demo/01-first/guide.md. Review and publish it in the Content Editor."
    result = job["result"]
    assert result["placed"] is True and result["link"] == LINK and result["guide"] == "demo/01-first/guide.md"
    assert result["written"] == ["01-first/guide.md", "01-first/manifest.json"] and result["lines"] == [] and result["course"]["slug"] == "01-first"

    # The project: made as Make a video makes it, rendered, and on Vimeo under the walkthrough's name with its transcript.
    record = store.get_project(pid)
    assert record["kind"] == "walkthrough" and record["walkthrough_id"] == wid and record["name"] == "User Console tour"
    assert record["output_video"] and record["vimeo"]["id"] == VIDEO_ID and record["vimeo"]["previous"] == []
    created_body = json.loads(fake.requests[0].content)
    assert created_body["name"] == "User Console tour" and created_body["description"].startswith("# ") and created_body["privacy"]["view"] == "unlisted"
    assert _FakeVideoProcessor.last is not None and _FakeVideoProcessor.last.preview_seconds == 0

    # The guide: the placeholder became the figure with the caption typed (the Watch: once), CRLF kept; the manifest's key.
    raw = s["guide"].read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") == guide_before.count(b"\r\n") + 2
    assert f"![Walkthrough]({LINK})".encode() in raw and b'<figcaption class="pcm-video-caption">Watch: the User Console tour</figcaption>' in raw
    assert b"Walkthrough: coming soon" not in raw
    assert b'"hasVideo": true' in s["manifest"].read_bytes()

    # Exactly the three rows, by their phases, and no project.generate.
    added = _rows()[len(before):]
    assert [(a, e) for a, e, _i, _d in added] == [("walkthrough.make_video", "walkthrough"), ("project.vimeo_publish", "project"), ("walkthrough.place_video", "walkthrough")]
    assert added[0][2] == wid and added[0][3] == f"User Console tour -> {pid}: {s['steps']} steps"
    assert added[1][2] == pid and added[1][3] == f"User Console tour -> vimeo {VIDEO_ID} (unlisted)"
    assert added[2][2] == wid and added[2][3] == f"demo/01-first: vimeo {VIDEO_ID}"
    assert TOKEN not in json.dumps(auth_store.list_audit(limit=200)) and TOKEN not in json.dumps(job)

    # The dialog now says so: the project, its video, its Vimeo record and that the guide holds the link.
    d = c.get(URL.format(wid=wid)).json()
    assert d["project"]["id"] == pid and d["project"]["has_video"] is True and d["project"]["vimeo"]["link"] == LINK
    assert d["project"]["placed"] is True and d["project"]["guide"] == "demo/01-first/guide.md" and d["place"]["placed"] is True
    assert c.get(f"/api/projects/{pid}/vimeo").json()["vimeo"]["id"] == VIDEO_ID, "the project page's panel shows the result"
    assert c.get(f"/api/projects/{pid}/job").json()["active_job"] is None


def test_the_progress_maps_the_three_phases_into_one_line():
    rp, pp = video_to_course.render_progress, video_to_course.publish_progress
    assert rp(0.0, "") == (0.0, "Rendering the video…")
    assert rp(0.5, "Narrating slide 3/10") == (0.275, "Rendering the video… Narrating slide 3/10")
    assert rp(1.0, "Complete") == (0.55, "Rendering the video… Complete")
    assert pp(0.0, "Creating the video on Vimeo…") == (0.55, "Creating the video on Vimeo…")
    f, text = pp(0.02 + 0.88 * 0.5, "Uploading… 150 of 400 MB")
    assert text == "Uploading to Vimeo… 150 of 400 MB" and 0.55 < f < 0.85 and f == pytest.approx(0.55 + 0.30 * (0.46 / 0.90))
    assert pp(0.90, "Uploading… 400 of 400 MB") == (0.85, "Uploading to Vimeo… 400 of 400 MB")
    assert pp(0.92, "Vimeo is processing the video…") == (0.90, "Vimeo is processing the video…")
    assert pp(1.0, f"Published: {LINK}") == (0.95, f"Published: {LINK}")
    assert video_to_course.failed("Uploading to Vimeo", "boom") == "Uploading to Vimeo failed: boom"
    assert video_to_course.done_line(LINK, course=False, allowed=True, guide=None, written=False) == f"Done: {LINK} is on Vimeo."
    assert "was not placed" in video_to_course.done_line(LINK, course=True, allowed=False, guide=None, written=False)
    assert video_to_course.done_line(LINK, course=True, allowed=True, guide="d/g", written=False) == "Done: https://vimeo.com/123456789/abcdef1234 is already in d/g. Review and publish it in the Content Editor."


# -- run again (R3) ---------------------------------------------------------------------------------------------------------

def test_run_again_renders_a_new_video_keeps_the_old_id_and_replaces_the_link_and_just_place_again_rewrites_a_reverted_guide(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    c, wid = s["c"], s["wid"]
    _ready(monkeypatch)
    r = _start(c, wid)
    pid = r.json()["project_id"]
    assert _wait_job(c, r.json()["job_id"])["status"] == "done"
    first_render = _FakeVideoProcessor.last

    # Again: the SAME project, a new render, a NEW Vimeo video, the guide's link replaced.
    monkeypatch.setattr(vimeo_tests, "VIDEO_ID", "987654321")
    monkeypatch.setattr(fake, "link", "https://vimeo.com/987654321/newhash99")
    r = _start(c, wid, name="User Console tour, take 2")
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == pid and r.json()["created"] is False
    job = _wait_job(c, r.json()["job_id"])
    assert job["status"] == "done", job
    assert _FakeVideoProcessor.last is not first_render, "a new render"
    record = store.get_project(pid)
    assert record["name"] == "User Console tour, take 2"
    assert record["vimeo"]["id"] == "987654321" and record["vimeo"]["previous"] == [VIDEO_ID]
    assert fake.deleted == [], "the old video stays on Vimeo"
    raw = s["guide"].read_bytes()
    assert b"https://vimeo.com/987654321/newhash99" in raw and LINK.encode() not in raw and raw.count(b"![Walkthrough]") == 1
    assert len([p for p in store.list_projects() if p.get("walkthrough_id") == wid]) == 1, "one project per walkthrough for this flow"
    snapshot = json.loads((store.PROJECTS_DIR / pid / record["source_filename"]).read_text(encoding="utf-8"))
    assert snapshot["name"] == "User Console tour, take 2", "the snapshot is the walkthrough as it is now"

    # The guide reverted (an editor undid it): Just place the existing video again - no render, no upload.
    s["guide"].write_bytes(_crlf(PLACEHOLDER_GUIDE).encode("utf-8"))
    d = c.get(URL.format(wid=wid)).json()
    assert d["project"]["placed"] is False and d["place"]["how"] == "placeholder"
    renders, requests = _FakeVideoProcessor.last, len(fake.requests)
    rows_before = len(_rows())
    r = _start(c, wid, mode="place")
    assert r.status_code == 200, r.text
    job = _wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["placed"] is True, job
    assert job["message"].startswith("Done: https://vimeo.com/987654321/newhash99 placed in demo/01-first/guide.md")
    assert _FakeVideoProcessor.last is renders and len(fake.requests) == requests, "nothing rendered, nothing sent to Vimeo"
    assert b"https://vimeo.com/987654321/newhash99" in s["guide"].read_bytes()
    assert [a for a, *_ in _rows()[rows_before:]] == ["walkthrough.place_video"]
    assert c.get(URL.format(wid=wid)).json()["project"]["placed"] is True

    # Placed already: "just place again" writes nothing and says so.
    job = _wait_job(c, _start(c, wid, mode="place").json()["job_id"])
    assert job["status"] == "done" and job["message"].startswith("Done: https://vimeo.com/987654321/newhash99 is already in demo/01-first/guide.md")
    assert job["result"]["written"] == []


def test_just_place_again_needs_a_video_on_vimeo_and_a_course(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    r = _start(s["c"], s["wid"], mode="place")
    assert r.status_code == 400 and r.json()["detail"] == video_to_course.PLACE_NEEDS_VIDEO_MESSAGE
    assert store.list_projects() == []
    p = _plain(rig, monkeypatch)
    _ready(monkeypatch)
    r = _start(p["c"], p["wid"])
    assert r.status_code == 200, r.text
    assert _wait_job(p["c"], r.json()["job_id"])["status"] == "done"
    r = _start(p["c"], p["wid"], mode="place")
    assert r.status_code == 400 and r.json()["detail"] == course_video.NOT_FROM_COURSE_MESSAGE


# -- no course, no token, the setting off ------------------------------------------------------------------------------------

def test_a_walkthrough_from_no_course_is_rendered_and_published_and_the_job_says_so(rig, monkeypatch, fake):
    p = _plain(rig, monkeypatch)
    _ready(monkeypatch)
    before = len(_rows())
    r = _start(p["c"], p["wid"], name="From stills")
    assert r.status_code == 200, r.text
    job = _wait_job(p["c"], r.json()["job_id"])
    assert job["status"] == "done" and job["message"] == f"Done: {LINK} is on Vimeo.", job
    assert job["result"]["course"] is None and job["result"]["placed"] is False and job["result"]["lines"] == []
    assert store.get_project(r.json()["project_id"])["vimeo"]["id"] == VIDEO_ID
    assert [a for a, *_ in _rows()[before:]] == ["walkthrough.make_video", "project.vimeo_publish"]


def test_no_token_is_refused_before_anything_is_made(rig, monkeypatch):
    s = _imported(rig, monkeypatch)
    _ready(monkeypatch, token=False)
    before = len(_rows())
    r = _start(s["c"], s["wid"])
    assert r.status_code == 400 and r.json()["detail"] == vimeo.NO_TOKEN_MESSAGE
    assert store.list_projects() == [] and len(_rows()) == before and _FakeVideoProcessor.last is None
    assert jobs.active_for("") is None
    empty = _new(s["c"], "Empty")
    config._config["vimeo_token"] = TOKEN
    r = _start(s["c"], empty["id"])
    assert r.status_code == 400 and r.json()["detail"] == store.NO_STEPS_MESSAGE and store.list_projects() == []


def test_the_setting_off_renders_and_publishes_and_answers_the_lines_to_paste(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    _ready(monkeypatch, allowed=False)
    guide_before = s["guide"].read_bytes()
    before = len(_rows())
    r = _start(s["c"], s["wid"], caption="")
    job = _wait_job(s["c"], r.json()["job_id"])
    assert job["status"] == "done", job
    assert job["message"] == f"Done: {LINK} is on Vimeo. Allow export into the Courses folder is off, so the link was not placed: copy the lines into the guide."
    assert job["result"]["placed"] is False and job["result"]["allowed"] is False
    assert job["result"]["lines"] == ["<figure>", "", f"![Walkthrough]({LINK})", "", f'<figcaption class="pcm-video-caption">Watch: {s["name"]}</figcaption>', "", "</figure>"]
    assert s["guide"].read_bytes() == guide_before
    assert [a for a, *_ in _rows()[before:]] == ["walkthrough.make_video", "project.vimeo_publish"]


# -- each phase's failure --------------------------------------------------------------------------------------------------------

class _FailingProcessor(_FakeVideoProcessor):
    def process_files(self, files, output_dir, progress=None, preview_seconds=0):
        raise RuntimeError("ffmpeg was not found")


def test_a_render_failure_names_the_phase_and_keeps_the_project_without_a_video(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    _ready(monkeypatch)
    monkeypatch.setattr(processing, "VideoProcessor", _FailingProcessor)
    before = len(_rows())
    r = _start(s["c"], s["wid"])
    pid = r.json()["project_id"]
    job = _wait_job(s["c"], r.json()["job_id"])
    assert job["status"] == "error" and job["error"] == "Rendering the video failed: ffmpeg was not found", job
    record = store.get_project(pid)
    assert record is not None and "output_video" not in record and "vimeo" not in record
    assert fake.requests == [] and [a for a, *_ in _rows()[before:]] == ["walkthrough.make_video"]


def test_a_publish_failure_names_the_phase_and_keeps_the_rendered_video(rig, monkeypatch):
    s = _imported(rig, monkeypatch)
    _ready(monkeypatch)
    f = FakeVimeo(create_status=401, create_body={"error": f"Bad token {TOKEN}"})
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    guide_before = s["guide"].read_bytes()
    before = len(_rows())
    r = _start(s["c"], s["wid"])
    pid = r.json()["project_id"]
    job = _wait_job(s["c"], r.json()["job_id"])
    assert job["status"] == "error" and job["error"].startswith("Uploading to Vimeo failed: Vimeo refused the access token (401)"), job
    assert TOKEN not in json.dumps(job)
    record = store.get_project(pid)
    assert record["output_video"] and "vimeo" not in record, "the rendered video stays: Publish to Vimeo… from the project page"
    assert s["guide"].read_bytes() == guide_before
    assert [a for a, *_ in _rows()[before:]] == ["walkthrough.make_video"]
    # From here the project page's own publish works as before.
    monkeypatch.setattr(vimeo, "TRANSPORT", FakeVimeo().transport)
    r = s["c"].post(f"/api/projects/{pid}/vimeo/publish", json={"title": "T", "description": "", "privacy": "unlisted"})
    assert _wait_job(s["c"], r.json()["job_id"])["status"] == "done"


def test_a_place_failure_names_the_phase_and_keeps_the_vimeo_record(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    _ready(monkeypatch)
    s["guide"].unlink()
    before = len(_rows())
    r = _start(s["c"], s["wid"])
    pid = r.json()["project_id"]
    job = _wait_job(s["c"], r.json()["job_id"])
    assert job["status"] == "error" and job["error"].startswith("Placing the link in the course failed: "), job
    assert "01-first" in job["error"], "the workshop without its guide is named"
    record = store.get_project(pid)
    assert record["output_video"] and record["vimeo"]["id"] == VIDEO_ID, "the Vimeo record stays: Place in the course… as before"
    assert [a for a, *_ in _rows()[before:]] == ["walkthrough.make_video", "project.vimeo_publish"]


# -- cancel in each phase ------------------------------------------------------------------------------------------------------

class _CancelAwareProcessor(_FakeVideoProcessor):
    """Honours the job's cancel before writing anything, as the real processor does at its export stage."""

    def process_files(self, files, output_dir, progress=None, preview_seconds=0):
        if self.cancel_check and self.cancel_check():
            self.cancelled = True
            self.cancel_stage = processing.RENDER_STAGE_EXPORT
            return 0
        return super().process_files(files, output_dir, progress=progress, preview_seconds=preview_seconds)


class _CancelAfterRender(_FakeVideoProcessor):
    """Writes the video, then the user presses Cancel: the upload phase sees it before its first chunk."""

    def process_files(self, files, output_dir, progress=None, preview_seconds=0):
        out = super().process_files(files, output_dir, progress=progress, preview_seconds=preview_seconds)
        jobs.cancel(jobs.current_job_id())
        return out


def test_a_cancel_in_each_phase_leaves_nothing_half_done(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    c, wid = s["c"], s["wid"]
    _ready(monkeypatch)
    guide_before = s["guide"].read_bytes()

    # 1. During the render: the render's own cancel; no video, nothing sent to Vimeo.
    monkeypatch.setattr(processing, "VideoProcessor", _CancelAwareProcessor)
    real_check = jobs.cancel_check_here

    def cancel_at_once():
        check = real_check()
        jobs.cancel(jobs.current_job_id())
        return check

    monkeypatch.setattr(jobs, "cancel_check_here", cancel_at_once)
    r = _start(c, wid)
    pid = r.json()["project_id"]
    job = _wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["cancelled"] is True and job["result"]["phase"] == "render", job
    assert "cancelled" in job["message"].lower()
    assert "output_video" not in store.get_project(pid) and fake.requests == []
    monkeypatch.setattr(jobs, "cancel_check_here", real_check)

    # 2. After the render, before the upload: the unfinished video is removed from Vimeo; the rendered video stays.
    monkeypatch.setattr(processing, "VideoProcessor", _CancelAfterRender)
    r = _start(c, wid)
    job = _wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["phase"] == "publish" and job["message"] == vimeo.CANCELLED_MESSAGE, job
    record = store.get_project(pid)
    assert record["output_video"] and "vimeo" not in record and fake.deleted == [VIDEO_ID]

    # 3. Before the place: the video is on Vimeo, the guide untouched.
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    real_compute = course_video.compute

    def cancel_then_compute(record, **kw):
        jobs.cancel(jobs.current_job_id())
        return real_compute(record, **kw)

    monkeypatch.setattr(course_video, "compute", cancel_then_compute)
    fake.deleted.clear()
    r = _start(c, wid)
    job = _wait_job(c, r.json()["job_id"])
    assert job["status"] == "done" and job["result"]["phase"] == "place", job
    assert job["message"] == f"Cancelled before the link was placed; the video is on Vimeo ({LINK})."
    assert store.get_project(pid)["vimeo"]["id"] == VIDEO_ID and fake.deleted == []
    assert s["guide"].read_bytes() == guide_before
    assert [a for a, *_ in _rows("walkthrough.place_video")] == []


# -- ownership, bodies, a busy project ------------------------------------------------------------------------------------------

def test_the_owner_or_an_administrator_bad_bodies_and_a_busy_project(rig, monkeypatch, fake):
    s = _imported(rig, monkeypatch)
    c, wid = s["c"], s["wid"]
    _ready(monkeypatch)
    assert _start(rig["other"], wid).status_code == 403
    assert _start(c, "aabbccddeeff").status_code == 404
    for bad in ({}, {"name": ""}, {"name": "x", "privacy": "password"}, {"name": "x", "mode": "half"}, {"name": "x", "caption": "c" * 201},
                {"name": "x", "welcome": "yes"}, {"name": "x", "extra": 1}):
        assert c.post(URL.format(wid=wid), json=bad).status_code == 422, bad
    assert store.list_projects() == [] and fake.requests == []

    r = _start(rig["admin"], wid, name="By admin")
    assert r.status_code == 200, r.text
    pid = r.json()["project_id"]
    assert _wait_job(rig["admin"], r.json()["job_id"])["status"] == "done"
    assert store.get_project(pid)["owner_id"] == rig["admin"].get("/api/auth/me").json()["user"]["id"]

    # The project's job holds it: 409 for a run again and for a place again alike, nothing refreshed.
    import threading

    gate = threading.Event()
    jobs.start("generate", lambda progress: gate.wait(5), project_id=pid, user_id="x")
    try:
        name_before = store.get_project(pid)["name"]
        r = _start(c, wid, name="Blocked")
        assert r.status_code == 409 and "generate" in r.json()["detail"]
        assert store.get_project(pid)["name"] == name_before, "a busy project is not refreshed"
        assert _start(c, wid, mode="place").status_code == 409
    finally:
        gate.set()


# -- the pure rules ----------------------------------------------------------------------------------------------------------------

def test_which_walkthrough_a_workshop_has_and_which_project_a_walkthrough_has():
    src = {"course": "demo", "slug": "01-first", "title": "Demo", "version": "1"}
    older = {"id": "a" * 12, "source": src, "created_at": "2026-10-01T00:00:00Z"}
    newer = {"id": "b" * 12, "source": {**src, "workshops": [{"slug": "01-first", "title": "F"}, {"slug": "02-next", "title": "N"}]}, "created_at": "2026-10-02T00:00:00Z"}
    other = {"id": "c" * 12, "source": {**src, "slug": "02-next"}, "created_at": "2026-10-03T00:00:00Z"}
    plain = {"id": "d" * 12, "source": None, "created_at": "2026-10-04T00:00:00Z"}
    assert video_to_course.walkthrough_for([older, newer, other, plain], "demo", "01-first") is newer, "the most recent; a multi-workshop record by its FIRST workshop"
    assert video_to_course.walkthrough_for([older, newer, other, plain], "demo", "02-next") is other
    assert video_to_course.walkthrough_for([older, newer, other, plain], "other", "01-first") is None
    p1 = {"id": "p1", "kind": "walkthrough", "walkthrough_id": "a" * 12, "created_at": "2026-10-01T00:00:00Z"}
    p2 = {"id": "p2", "kind": "walkthrough", "walkthrough_id": "a" * 12, "created_at": "2026-10-05T00:00:00Z"}
    deck = {"id": "p3", "kind": "deck", "created_at": "2026-10-09T00:00:00Z"}
    assert video_to_course.project_for([p1, p2, deck], "a" * 12) is p2 and video_to_course.project_for([p1, deck], "b" * 12) is None
    assert video_to_course.vimeo_view({"vimeo": None}) is None
    assert video_to_course.vimeo_view({"vimeo": {"id": "1", "hash": "h", "privacy": "unlisted", "published_at": "t", "previous": ["0"]}}) == {
        "id": "1", "link": "https://vimeo.com/1/h", "privacy": "unlisted", "published_at": "t", "previous": ["0"]}
    view = video_to_course.project_view({"id": "p1", "name": "N", "created_at": "c", "rendered_at": "r", "output_video": "v.mp4"}, {"placed": True, "guide": "d/g"})
    assert view == {"id": "p1", "name": "N", "created_at": "c", "rendered_at": "r", "has_video": True, "vimeo": None, "placed": True, "guide": "d/g"}
    assert FIGURE_GUIDE  # the guide with a figure of ours is the "replaced" case the plan reads; exercised through the GET above
