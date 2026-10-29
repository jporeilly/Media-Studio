"""Publish to Vimeo (V1-V3): the token as a setting, the client over a faked transport, the publish job, the
record, and the link placed in the course workshop's guide.

- **V1, the settings.** ``vimeo_token`` is write-only: the PUT takes it, the GET answers ``vimeo_token_set``
  and never the token; "" clears it; ``MEDIA_STUDIO_VIMEO_TOKEN`` in the environment wins over the stored one
  and is never written to the config; ``vimeo_privacy`` is one of unlisted (the default), private, anybody; the
  audit row names the key, never the value; admin-only.
- **V2, the client and the job.** Every call over ``httpx.MockTransport`` - never the network: the create with
  the tus approach, the size, the name, the description and the privacy (``privacy.embed`` public); the chunked
  PATCHes with ``Upload-Offset`` and the tus headers; a failed chunk resumed from the offset ``HEAD`` answers,
  up to 3 tries; the poll until ``transcode.status`` is complete; 401, 403/400 on the privacy, 429 with
  Retry-After, a transcode error, the wait bound; a cancel between chunks deletes the video. The route: 400 with
  no video or no token, 403 not yours, 409 busy; the record's ``vimeo`` (the hash from an Unlisted link), Publish
  again keeps the old id in ``previous``; the audit row; the token in no answer, log line or error.
- **V3, the place.** The Walkthrough placeholder (the course standard's exact form) becomes the video figure,
  CRLF and LF guides alike; an existing figure of ours has its link replaced; a guide with neither says so and
  offers the insertion after the first section heading; ``manifest.json`` gets ``hasVideo: true`` byte for byte;
  the Welcome tour goes into ``course.json``; the plan writes nothing; the write is behind ``courses_export``
  (403), checks the sha256 (409), keeps one journal, and leaves the audit row.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest

from api import store as auth_store
from services import course_export, course_video, processing, studio_settings, vimeo
from services import projects as store
from test_generate import _FakeVideoProcessor, _wait_job
from test_walkthroughs import _hotspot, _still
from test_workshops import _course, _png, _workshop, rig  # noqa: F401 - the rig fixture
from utils.config import config

TOKEN = "tok_SECRET_0123456789abcdef"
VIDEO_ID = "123456789"
HASH = "abcdef1234"
UPLOAD_LINK = "https://files.tus.vimeo.com/files/vimeo-prod/abc123"
LIMITS_MD = Path(__file__).resolve().parent.parent / "docs" / "reference" / "limits.md"


# -- the faked Vimeo -----------------------------------------------------------------------------------------------

class FakeVimeo:
    """Vimeo over an ``httpx.MockTransport``: the create, the tus upload, the poll, the delete - and the failures a
    test injects. Every request is kept."""

    def __init__(self, *, privacy_view=None, link=None, transcode=("in_progress", "complete"), create_status=200,
                 create_body=None, fail_chunks=(), chunk_fail_status=500, rate_limit_first=0, retry_after="2", hash_in_link=True):
        self.requests: list[httpx.Request] = []
        self.chunks: list[tuple[int, bytes]] = []
        self.received = b""
        self.offset = 0
        self.deleted: list[str] = []
        self.privacy_view = privacy_view
        self.pinned_view = privacy_view is not None
        self.link = link or (f"https://vimeo.com/{VIDEO_ID}/{HASH}" if hash_in_link else f"https://vimeo.com/{VIDEO_ID}")
        self.transcode = list(transcode)
        self.create_status = create_status
        self.create_body = create_body
        self.fail_chunks = set(fail_chunks)  # the 1-based PATCH numbers that fail (once each)
        self.chunk_fail_status = chunk_fail_status
        self.rate_limit_first = rate_limit_first  # the first N requests answer 429
        self.retry_after = retry_after
        self.sleeps: list[float] = []
        self.failed: set[int] = set()
        self.patches = 0

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.rate_limit_first > 0:
            self.rate_limit_first -= 1
            return httpx.Response(429, headers={"Retry-After": self.retry_after}, json={"error": "Too many requests"})
        url = str(request.url)
        if request.method == "POST" and url == "https://api.vimeo.com/me/videos":
            if self.create_status != 200:
                return httpx.Response(self.create_status, json=self.create_body or {"error": "refused", "developer_message": "nope"})
            # Vimeo answers the privacy it was asked for (a test may pin another to see it mapped back).
            if not self.pinned_view:
                self.privacy_view = json.loads(request.content)["privacy"]["view"]
            return httpx.Response(200, json={
                "uri": f"/videos/{VIDEO_ID}", "link": self.link, "player_embed_url": f"https://player.vimeo.com/video/{VIDEO_ID}?h={HASH}",
                "upload": {"approach": "tus", "size": json.loads(request.content)["upload"]["size"], "upload_link": UPLOAD_LINK},
                "privacy": {"view": self.privacy_view, "embed": "public"},
            })
        if url == UPLOAD_LINK and request.method == "PATCH":
            self.patches += 1
            sent = int(request.headers["Upload-Offset"])
            if self.patches in self.fail_chunks and self.patches not in self.failed:
                self.failed.add(self.patches)
                return httpx.Response(self.chunk_fail_status, text="chunk lost")
            if sent != self.offset:
                return httpx.Response(409, text=f"offset mismatch: {sent} != {self.offset}")
            self.chunks.append((sent, request.content))
            self.received += request.content
            self.offset += len(request.content)
            return httpx.Response(204, headers={"Upload-Offset": str(self.offset)})
        if url == UPLOAD_LINK and request.method == "HEAD":
            return httpx.Response(200, headers={"Upload-Offset": str(self.offset), "Upload-Length": "0"})
        if request.method == "GET" and url.startswith(f"https://api.vimeo.com/videos/{VIDEO_ID}"):
            status = self.transcode.pop(0) if len(self.transcode) > 1 else self.transcode[0]
            return httpx.Response(200, json={
                "uri": f"/videos/{VIDEO_ID}", "link": self.link, "transcode": {"status": status},
                "privacy": {"view": self.privacy_view, "embed": "public"}, "player_embed_url": f"https://player.vimeo.com/video/{VIDEO_ID}?h={HASH}",
            })
        if request.method == "DELETE" and url == f"https://api.vimeo.com/videos/{VIDEO_ID}":
            self.deleted.append(VIDEO_ID)
            return httpx.Response(204)
        return httpx.Response(404, text=f"unexpected {request.method} {url}")


@pytest.fixture
def fake(monkeypatch):
    """The module's transport and clock pointed at the fake: no call ever leaves the process."""
    f = FakeVimeo()
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    monkeypatch.setattr(vimeo, "VIMEO_CHUNK_BYTES", 10)
    return f


def _progress():
    rows: list[tuple[float, str]] = []
    return rows, (lambda fraction, message="": rows.append((fraction, message)))


# -- V1: the settings ------------------------------------------------------------------------------------------------

def test_the_token_is_write_only_and_the_privacy_has_its_three_values(rig, monkeypatch):
    monkeypatch.delenv("MEDIA_STUDIO_VIMEO_TOKEN", raising=False)
    admin = rig["admin"]
    before = admin.get("/api/settings/studio").json()
    assert before["settings"]["vimeo_token_set"] is False and before["settings"]["vimeo_privacy"] == "unlisted"
    assert "vimeo_token" not in before["settings"]
    assert [o["value"] for o in before["options"]["vimeo_privacy"]["options"]] == ["unlisted", "private", "anybody"]
    assert all("Plus" in o["note"] for o in before["options"]["vimeo_privacy"]["options"] if o["value"] != "anybody")

    r = admin.put("/api/settings/studio", json={"vimeo_token": f"  {TOKEN} ", "vimeo_privacy": "anybody"})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["vimeo_token_set"] is True and r.json()["settings"]["vimeo_privacy"] == "anybody"
    assert TOKEN not in r.text, "the token never comes back"
    assert config._config["vimeo_token"] == TOKEN, "stored stripped, in the config like the other settings"
    assert studio_settings.vimeo_token() == TOKEN
    assert TOKEN not in admin.get("/api/settings/studio").text
    rows = auth_store.list_audit(action="settings.update")
    assert rows[0]["detail"] == "vimeo_privacy, vimeo_token" and TOKEN not in json.dumps(rows), "key names only"

    r = admin.put("/api/settings/studio", json={"vimeo_token": ""})
    assert r.status_code == 200 and r.json()["settings"]["vimeo_token_set"] is False, "an empty string clears it"
    assert studio_settings.vimeo_token() == ""

    assert rig["other"].put("/api/settings/studio", json={"vimeo_token": "x"}).status_code == 403
    assert admin.put("/api/settings/studio", json={"vimeo_privacy": "password"}).status_code == 400
    assert admin.put("/api/settings/studio", json={"vimeo_token": 5}).status_code == 422
    assert admin.put("/api/settings/studio", json={"vimeo_token": "has a space"}).status_code == 400
    assert config._config.get("vimeo_privacy") == "anybody", "a refused save changes nothing"


def test_the_environment_token_wins_and_is_never_written_to_the_config(rig, monkeypatch):
    config._config["vimeo_token"] = "stored-one"
    monkeypatch.setenv("MEDIA_STUDIO_VIMEO_TOKEN", "env-one")
    assert studio_settings.vimeo_token() == "env-one"
    s = rig["admin"].get("/api/settings/studio").json()["settings"]
    assert s["vimeo_token_set"] is True and s["vimeo_token_env"] is True
    assert "env-one" not in json.dumps(s) and "stored-one" not in json.dumps(s)
    rig["admin"].put("/api/settings/studio", json={"vimeo_privacy": "private"})
    assert config._config["vimeo_token"] == "stored-one" and "env-one" not in json.dumps(config._config)
    monkeypatch.delenv("MEDIA_STUDIO_VIMEO_TOKEN")
    assert studio_settings.vimeo_token() == "stored-one"
    assert rig["admin"].get("/api/settings/studio").json()["settings"]["vimeo_token_env"] is False


# -- V2: the client ---------------------------------------------------------------------------------------------------

def test_publish_creates_uploads_in_chunks_polls_and_answers_the_record(tmp_path, fake):
    path = tmp_path / "video.mp4"
    path.write_bytes(bytes(range(25)))  # 3 chunks of 10 at the test's chunk size
    rows, progress = _progress()
    rec = vimeo.publish(path, token=TOKEN, title="Your First Win", description="What it shows", privacy="unlisted",
                        progress=progress, cancel_check=lambda: False)

    create = fake.requests[0]
    assert create.method == "POST" and str(create.url) == "https://api.vimeo.com/me/videos"
    assert create.headers["Authorization"] == f"bearer {TOKEN}"
    assert create.headers["Accept"] == "application/vnd.vimeo.*+json;version=3.4"
    assert json.loads(create.content) == {
        "upload": {"approach": "tus", "size": "25"}, "name": "Your First Win", "description": "What it shows",
        "privacy": {"view": "unlisted", "embed": "public"},
    }
    patches = [r for r in fake.requests if r.method == "PATCH"]
    assert [int(r.headers["Upload-Offset"]) for r in patches] == [0, 10, 20]
    assert all(r.headers["Tus-Resumable"] == "1.0.0" and r.headers["Content-Type"] == "application/offset+octet-stream" for r in patches)
    assert fake.received == bytes(range(25)), "the file, chunk by chunk, in order"
    polls = [r for r in fake.requests if r.method == "GET"]
    assert len(polls) == 2 and polls[0].url.params["fields"] == "uri,link,transcode.status,privacy,player_embed_url"
    assert fake.sleeps == [vimeo.VIMEO_TRANSCODE_POLL_S], "one wait between the two polls"
    assert fake.deleted == []

    assert rec["id"] == VIDEO_ID and rec["link"] == f"https://vimeo.com/{VIDEO_ID}/{HASH}" and rec["hash"] == HASH
    assert rec["player_url"] == f"https://player.vimeo.com/video/{VIDEO_ID}?h={HASH}"
    assert rec["privacy"] == "unlisted" and rec["title"] == "Your First Win" and rec["published_at"]
    assert set(rec) == {"id", "link", "hash", "player_url", "privacy", "published_at", "title"}
    assert rows[0] == (0.0, "Creating the video on Vimeo…")
    assert [m for f, m in rows if m.startswith("Uploading")] and rows[-1][0] == 1.0
    assert all(0.0 <= f <= 1.0 for f, _m in rows) and [f for f, _m in rows] == sorted(f for f, _m in rows), "progress never goes back"


def test_a_private_video_maps_to_vimeos_nobody_and_an_anybody_link_has_no_hash(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x" * 5)
    f = FakeVimeo(privacy_view="nobody")
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    rec = vimeo.publish(path, token=TOKEN, title="P", description="", privacy="private", progress=lambda *a: None, cancel_check=lambda: False)
    assert json.loads(f.requests[0].content)["privacy"] == {"view": "nobody", "embed": "public"}
    assert rec["privacy"] == "private", "our word, not Vimeo's"
    f2 = FakeVimeo(privacy_view="anybody", hash_in_link=False)
    monkeypatch.setattr(vimeo, "TRANSPORT", f2.transport)
    rec2 = vimeo.publish(path, token=TOKEN, title="A", description="", privacy="anybody", progress=lambda *a: None, cancel_check=lambda: False)
    assert rec2["link"] == f"https://vimeo.com/{VIDEO_ID}" and rec2["hash"] is None and rec2["privacy"] == "anybody"
    assert course_video.video_link(rec) == f"https://vimeo.com/{VIDEO_ID}/{HASH}" and course_video.video_link(rec2) == f"https://vimeo.com/{VIDEO_ID}"


def test_the_name_and_the_description_are_cut_to_vimeos_limits(tmp_path, fake):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    vimeo.publish(path, token=TOKEN, title="T" * 300, description="D" * 6000, privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    body = json.loads(fake.requests[0].content)
    assert len(body["name"]) == vimeo.VIMEO_NAME_MAX == 128 and len(body["description"]) == vimeo.VIMEO_DESCRIPTION_MAX == 5000
    assert vimeo.cut_description("word " * 2000).endswith("…") and len(vimeo.cut_description("word " * 2000)) <= 5000
    assert vimeo.cut_description("short") == "short"


def test_a_failed_chunk_is_resumed_from_the_offset_head_answers_up_to_three_tries(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(bytes(range(25)))
    f = FakeVimeo(fail_chunks=(2,))
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    monkeypatch.setattr(vimeo, "VIMEO_CHUNK_BYTES", 10)
    rec = vimeo.publish(path, token=TOKEN, title="R", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert rec["id"] == VIDEO_ID and f.received == bytes(range(25))
    kinds = [(r.method, str(r.url) == UPLOAD_LINK) for r in f.requests if str(r.url) == UPLOAD_LINK]
    assert kinds == [("PATCH", True), ("PATCH", True), ("HEAD", True), ("PATCH", True), ("PATCH", True)], "the lost chunk: HEAD, then again from the offset"

    # Every try fails: the job fails naming the chunk, and the video is not left half-uploaded on Vimeo.
    g = FakeVimeo()

    def always_fail(request):
        if request.method == "PATCH":
            g.requests.append(request)
            return httpx.Response(500, text="lost")
        return g.handler(request)

    monkeypatch.setattr(vimeo, "TRANSPORT", httpx.MockTransport(always_fail))
    with pytest.raises(vimeo.VimeoError) as exc:
        vimeo.publish(path, token=TOKEN, title="R", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert "3 tries" in str(exc.value) and "500" in str(exc.value)
    assert sum(1 for r in g.requests if r.method == "PATCH") == vimeo.VIMEO_CHUNK_TRIES == 3
    assert g.deleted == [VIDEO_ID], "the unfinished video is removed"


@pytest.mark.parametrize("status, privacy, expect", [
    (401, "unlisted", "refused the access token (401)"),
    (403, "unlisted", "Unlisted and Private videos need Vimeo Plus or higher"),
    (400, "private", "Unlisted and Private videos need Vimeo Plus or higher"),
    (403, "anybody", "Vimeo refused the video (403)"),
])
def test_the_create_refusals_say_what_to_do(tmp_path, monkeypatch, status, privacy, expect):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    f = FakeVimeo(create_status=status, create_body={"error": "No", "developer_message": f"the words of {status}"})
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    with pytest.raises(vimeo.VimeoError) as exc:
        vimeo.publish(path, token=TOKEN, title="T", description="", privacy=privacy, progress=lambda *a: None, cancel_check=lambda: False)
    assert expect in str(exc.value) and exc.value.status == status
    if status != 401:
        assert f"the words of {status}" in str(exc.value), "Vimeo's words"
    assert TOKEN not in str(exc.value)


def test_a_429_is_retried_after_retry_after_and_given_up_after_three(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    f = FakeVimeo(rate_limit_first=2, retry_after="7")
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    rec = vimeo.publish(path, token=TOKEN, title="T", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert rec["id"] == VIDEO_ID
    assert f.sleeps[:2] == [7.0, 7.0], "waited as Retry-After said, twice"
    assert sum(1 for r in f.requests if r.method == "POST") == 3

    g = FakeVimeo(rate_limit_first=99, retry_after="9999")
    monkeypatch.setattr(vimeo, "TRANSPORT", g.transport)
    monkeypatch.setattr(vimeo, "SLEEP", g.sleep)
    with pytest.raises(vimeo.VimeoError) as exc:
        vimeo.publish(path, token=TOKEN, title="T", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert "rate-limiting" in str(exc.value) and exc.value.status == 429
    assert g.sleeps == [vimeo.VIMEO_RETRY_AFTER_MAX_S] * (vimeo.VIMEO_RATE_TRIES - 1), "a wait is bounded"


def test_a_transcode_error_fails_with_vimeos_words_and_the_wait_is_bounded(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    f = FakeVimeo(transcode=("in_progress", "error"))
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    with pytest.raises(vimeo.VimeoError) as exc:
        vimeo.publish(path, token=TOKEN, title="T", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert "could not process the video" in str(exc.value) and "error" in str(exc.value)
    assert f.deleted == [VIDEO_ID], "a video Vimeo could not process is removed"

    g = FakeVimeo(transcode=("in_progress",))
    clock = {"now": 0.0}
    monkeypatch.setattr(vimeo, "TRANSPORT", g.transport)
    monkeypatch.setattr(vimeo, "SLEEP", lambda s: clock.__setitem__("now", clock["now"] + s))
    monkeypatch.setattr(vimeo, "CLOCK", lambda: clock["now"])
    with pytest.raises(vimeo.VimeoError) as exc:
        vimeo.publish(path, token=TOKEN, title="T", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert "did not finish processing" in str(exc.value) and f"https://vimeo.com/{VIDEO_ID}/{HASH}" in str(exc.value)
    assert isinstance(exc.value, vimeo.VimeoTimeout) and g.deleted == [], "the whole upload stays on Vimeo: the message names it"
    assert clock["now"] >= vimeo.VIMEO_TRANSCODE_WAIT_S == 1800
    assert sum(1 for r in g.requests if r.method == "GET") == vimeo.VIMEO_TRANSCODE_WAIT_S // vimeo.VIMEO_TRANSCODE_POLL_S + 1


def test_a_cancel_between_chunks_deletes_the_video_and_answers_none(tmp_path, fake):
    path = tmp_path / "v.mp4"
    path.write_bytes(bytes(range(25)))
    calls = {"n": 0}

    def cancel_after_one_chunk():
        calls["n"] += 1
        return calls["n"] > 2  # checked before each chunk: the third check says stop

    rows, progress = _progress()
    out = vimeo.publish(path, token=TOKEN, title="C", description="", privacy="unlisted", progress=progress, cancel_check=cancel_after_one_chunk)
    assert out is None
    assert len(fake.chunks) == 2 and fake.deleted == [VIDEO_ID]
    assert not [r for r in fake.requests if r.method == "GET"], "no poll for a cancelled upload"


def test_a_cancel_while_vimeo_processes_deletes_the_video_too(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    f = FakeVimeo(transcode=("in_progress",))
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    polls = {"n": 0}

    def cancel_after_two_polls():
        polls["n"] += 1
        return polls["n"] > 3

    assert vimeo.publish(path, token=TOKEN, title="C", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=cancel_after_two_polls) is None
    assert f.deleted == [VIDEO_ID]


def test_a_network_failure_is_a_plain_line_without_the_token(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")

    def down(request):
        raise httpx.ConnectError("name resolution failed", request=request)

    monkeypatch.setattr(vimeo, "TRANSPORT", httpx.MockTransport(down))
    monkeypatch.setattr(vimeo, "SLEEP", lambda s: None)
    with pytest.raises(vimeo.VimeoError) as exc:
        vimeo.publish(path, token=TOKEN, title="T", description="", privacy="unlisted", progress=lambda *a: None, cancel_check=lambda: False)
    assert str(exc.value).startswith("Could not reach Vimeo") and "name resolution failed" in str(exc.value) and TOKEN not in str(exc.value)
    assert vimeo.scrub(f"bearer {TOKEN} leaked", TOKEN) == "bearer *** leaked"


# -- V2: the route ----------------------------------------------------------------------------------------------------

def _owner_id(rig, who):
    return rig[who].get("/api/auth/me").json()["user"]["id"]


def _rendered_deck(rig, who="owner", name="Deck One") -> dict:
    """A deck project with a rendered video (the bytes stand in for the MP4), owned by ``who``."""
    rec = store.import_upload(f"{name}.pptx", b"pptx", owner_id=_owner_id(rig, who), owner_name=who)
    (store.PROJECTS_DIR / rec["id"] / f"{name}.mp4").write_bytes(bytes(range(25)))
    rec["output_video"] = f"{name}.mp4"
    store.save_project(rec)
    return rec


def test_publish_runs_as_a_job_and_the_record_gains_vimeo(rig, fake):
    config._config["vimeo_token"] = TOKEN
    owner = rig["owner"]
    rec = _rendered_deck(rig)
    pid = rec["id"]
    defaults = owner.get(f"/api/projects/{pid}/vimeo").json()
    assert defaults["title"] == "Deck One" and defaults["description"] == "" and defaults["privacy"] == "unlisted"
    assert defaults["token_set"] is True and defaults["vimeo"] is None and defaults["course"] is None
    assert defaults["name_max"] == 128 and defaults["description_max"] == 5000
    assert [o["value"] for o in defaults["privacies"]] == ["unlisted", "private", "anybody"]

    r = owner.post(f"/api/projects/{pid}/vimeo/publish", json={"title": "Deck One", "description": "Shows the deck", "privacy": "unlisted"})
    assert r.status_code == 200, r.text
    job = _wait_job(owner, r.json()["job_id"])
    assert job["status"] == "done" and job["kind"] == "vimeo_publish" and job["project_id"] == pid, job
    assert job["message"] == f"Published: https://vimeo.com/{VIDEO_ID}/{HASH}"
    assert json.loads(fake.requests[0].content)["description"] == "Shows the deck"
    record = store.get_project(pid)
    v = record["vimeo"]
    assert v["id"] == VIDEO_ID and v["link"] == f"https://vimeo.com/{VIDEO_ID}/{HASH}" and v["hash"] == HASH
    assert v["privacy"] == "unlisted" and v["title"] == "Deck One" and v["previous"] == [] and v["published_at"]
    assert job["result"]["vimeo"] == v
    assert owner.get(f"/api/projects/{pid}").json()["vimeo"] == v, "the page reads it from the record"
    rows = [x for x in auth_store.list_audit(limit=50) if x["action"] == "project.vimeo_publish"]
    assert [(x["entity"], x["entity_id"], x["detail"]) for x in rows] == [("project", pid, f"Deck One -> vimeo {VIDEO_ID} (unlisted)")]
    assert TOKEN not in json.dumps(auth_store.list_audit(limit=50)) and TOKEN not in json.dumps(job)

    # Publish again: a NEW video; the old id kept in ``previous``, nothing deleted on Vimeo.
    r = owner.post(f"/api/projects/{pid}/vimeo/publish", json={"title": "Deck One (take 2)", "description": "", "privacy": "anybody"})
    assert r.status_code == 200, r.text
    assert _wait_job(owner, r.json()["job_id"])["status"] == "done"
    again = store.get_project(pid)["vimeo"]
    assert again["previous"] == [VIDEO_ID] and again["title"] == "Deck One (take 2)" and again["privacy"] == "anybody"
    assert fake.deleted == []
    assert owner.get(f"/api/projects/{pid}/vimeo").json()["vimeo"] == again


def test_publish_refusals_no_video_no_token_not_yours_busy_and_bad_bodies(rig, fake, monkeypatch):
    owner = rig["owner"]
    rec = _rendered_deck(rig)
    pid = rec["id"]
    body = {"title": "T", "description": "", "privacy": "unlisted"}
    r = owner.post(f"/api/projects/{pid}/vimeo/publish", json=body)
    assert r.status_code == 400 and r.json()["detail"] == vimeo.NO_TOKEN_MESSAGE
    assert owner.get(f"/api/projects/{pid}/vimeo").json()["token_set"] is False

    config._config["vimeo_token"] = TOKEN
    plain = store.import_upload("Unrendered.pptx", b"pptx", owner_id=_owner_id(rig, "owner"), owner_name="owner")
    r = owner.post(f"/api/projects/{plain['id']}/vimeo/publish", json=body)
    assert r.status_code == 400 and r.json()["detail"] == vimeo.NO_VIDEO_MESSAGE
    assert rig["other"].post(f"/api/projects/{pid}/vimeo/publish", json=body).status_code == 403
    assert rig["other"].get(f"/api/projects/{pid}/vimeo").status_code == 403
    assert owner.post("/api/projects/aabbccddeeff/vimeo/publish", json=body).status_code == 404
    for bad in ({}, {**body, "privacy": "password"}, {**body, "title": ""}, {**body, "extra": 1}, {**body, "description": 5}):
        assert owner.post(f"/api/projects/{pid}/vimeo/publish", json=bad).status_code == 422, bad
    assert not fake.requests, "nothing reached Vimeo"

    # One job per project: a publish while a job holds the project is 409, like any other job.
    import threading

    gate = threading.Event()
    from services import jobs

    jobs.start("generate", lambda progress: gate.wait(5), project_id=pid, user_id=_owner_id(rig, "owner"))
    try:
        r = owner.post(f"/api/projects/{pid}/vimeo/publish", json=body)
        assert r.status_code == 409 and "generate" in r.json()["detail"]
    finally:
        gate.set()


def test_a_failed_publish_leaves_the_record_as_it_was_and_the_token_out_of_every_line(rig, monkeypatch, caplog):
    config._config["vimeo_token"] = TOKEN
    owner = rig["owner"]
    rec = _rendered_deck(rig)
    f = FakeVimeo(create_status=401, create_body={"error": f"Bad token {TOKEN}"})
    monkeypatch.setattr(vimeo, "TRANSPORT", f.transport)
    monkeypatch.setattr(vimeo, "SLEEP", f.sleep)
    with caplog.at_level(logging.DEBUG):
        r = owner.post(f"/api/projects/{rec['id']}/vimeo/publish", json={"title": "T", "description": "", "privacy": "unlisted"})
        assert r.status_code == 200, r.text
        job = _wait_job(owner, r.json()["job_id"])
    assert job["status"] == "error" and "refused the access token (401)" in job["error"]
    assert TOKEN not in json.dumps(job) and TOKEN not in caplog.text
    assert "vimeo" not in store.get_project(rec["id"])


def test_a_cancelled_publish_is_a_done_job_with_the_cancel_line(rig, fake, monkeypatch):
    config._config["vimeo_token"] = TOKEN
    owner = rig["owner"]
    rec = _rendered_deck(rig)
    from services import jobs

    real_check = jobs.cancel_check_here

    def cancel_at_once():
        check = real_check()
        job_id = jobs.current_job_id()
        jobs.cancel(job_id)
        return check

    monkeypatch.setattr(jobs, "cancel_check_here", cancel_at_once)
    r = owner.post(f"/api/projects/{rec['id']}/vimeo/publish", json={"title": "T", "description": "", "privacy": "unlisted"})
    job = _wait_job(owner, r.json()["job_id"])
    assert job["status"] == "done" and job["result"] == {"cancelled": True} and job["message"] == vimeo.CANCELLED_MESSAGE
    assert fake.deleted == [VIDEO_ID] and "vimeo" not in store.get_project(rec["id"])


# -- V3: a walkthrough project from a course workshop ---------------------------------------------------------------

#: The course standard's Walkthrough placeholder, as the template's author notes show it: a figure holding only the caption.
PLACEHOLDER_GUIDE = (
    "# User Console\n\n"
    "> **Note:** Intro.\n\n"
    "## 1. Open it\n\n"
    "1. Right-click **Valid rows** and select **Preview**.\n"
    "2. Select **Quick Launch**.\n\n"
    "***\n\n"
    "<figure>\n\n"
    '<figcaption class="pcm-video-caption">Walkthrough: coming soon</figcaption>\n\n'
    "</figure>\n\n"
    "## 2. Look inside\n\n"
    "1. Double-click **Read the file**.\n"
)
FIGURE_GUIDE = (
    "# User Console\n\n"
    "## 1. Open it\n\n"
    "1. Right-click **Valid rows** and select **Preview**.\n\n"
    "<figure>\n\n"
    "![Walkthrough](https://vimeo.com/111/oldhash1)\n\n"
    '<figcaption class="pcm-video-caption">Watch: the old one</figcaption>\n\n'
    "</figure>\n\n"
    "## 2. Look inside\n\n"
    "1. Double-click **Read the file**.\n"
)
BARE_GUIDE = (
    "# User Console\n\n"
    "> **Note:** Intro.\n\n"
    "## 1. Open it\n\n"
    "1. Right-click **Valid rows** and select **Preview**.\n\n"
    "## 2. Look inside\n\n"
    "1. Double-click **Read the file**.\n"
)
MANIFEST = '{\n  "title": "User Console",\n  "order": 3,\n  "kind": "workshop",\n  "stepCount": 4,\n  "estimatedMinutes": 25,\n  "hasVideo": false,\n  "checks": []\n}\n'
COURSE_JSON_NULL_WELCOME = '{\n  "id": "demo",\n  "title": "Demo course",\n  "version": "1.2.3",\n  "launchers": {"spoon": {"label": "Pentaho Data Integration", "action": "Start"}},\n  "welcome": null,\n  "theme": {\n    "accent": "#b91c1c"\n  }\n}\n'
COURSE_JSON_WELCOME = ('{\n  "id": "demo",\n  "title": "Demo course",\n  "version": "1.2.3",\n'
                       '  "launchers": {"spoon": {"label": "Pentaho Data Integration", "action": "Start"}},\n'
                       '  "welcome": { "eyebrow": "Try-It Lab", "caption": "Welcome Tour: coming soon" },\n'
                       '  "theme": {\n    "accent": "#2e7d32"\n  }\n}\n')


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _course_project(rig, monkeypatch, guide=PLACEHOLDER_GUIDE, *, crlf=True, manifest=MANIFEST, course_json=None, who="owner") -> dict:
    """The demo course with one workshop holding ``guide`` (CRLF by default), imported, its first step pictured, made
    into a video (a fake render) and published to the faked Vimeo: the project record with ``vimeo``."""
    folder = _course(rig["courses"], "demo")
    if course_json is not None:
        (folder / "course.json").write_bytes((_crlf(course_json) if crlf else course_json).encode("utf-8"))
    _workshop(folder, "01-first", "", manifest=None, images={"authored.png": _png((40, 30), (0, 128, 0))})
    (folder / "01-first" / "guide.md").write_bytes((_crlf(guide) if crlf else guide).encode("utf-8"))
    if manifest is not None:
        (folder / "01-first" / "manifest.json").write_bytes((_crlf(manifest) if crlf else manifest).encode("utf-8"))
    c = rig[who]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    rec = r.json()
    cid = _still(rig, monkeypatch, 400, 300, who=who)
    r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{rec['steps'][0]['id']}/picture", json={"capture_id": cid, "hotspot": _hotspot(10, 10, 50, 30)})
    assert r.status_code == 200, r.text
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = c.post(f"/api/walkthroughs/{rec['id']}/video", json={"name": "User Console tour", "notes": False, "zoom": True})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    assert _wait_job(c, r.json()["job_id"])["status"] == "done"
    config._config["vimeo_token"] = TOKEN
    r = c.post(f"/api/projects/{pid}/vimeo/publish", json={"title": "User Console tour", "description": "x", "privacy": "unlisted"})
    assert r.status_code == 200, r.text
    assert _wait_job(c, r.json()["job_id"])["status"] == "done"
    record = store.get_project(pid)
    assert record["vimeo"]["id"] == VIDEO_ID
    return {"record": record, "pid": pid, "folder": folder, "guide": folder / "01-first" / "guide.md",
            "manifest": folder / "01-first" / "manifest.json", "course_json": folder / "course.json", "c": c, "wid": rec["id"]}


def _plan(c, pid, **body):
    r = c.post(f"/api/projects/{pid}/vimeo/place/plan", json={"caption": "", "welcome": False, "insert": False, **body})
    assert r.status_code == 200, r.text
    return r.json()


def _place(c, pid, plan, **body):
    return c.post(f"/api/projects/{pid}/vimeo/place", json={
        "caption": plan["caption"], "welcome": body.pop("welcome", False), "insert": body.pop("insert", False),
        "guide_sha256": plan["guide_sha256"], "manifest_sha256": plan.get("manifest_sha256"), "course_sha256": plan.get("course_sha256"), **body,
    })


def test_the_defaults_of_a_walkthrough_project_carry_the_transcript_and_the_course(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch)
    d = s["c"].get(f"/api/projects/{s['pid']}/vimeo").json()
    assert d["course"] == {"course": "demo", "slug": "01-first", "title": "Demo course"}
    assert d["title"] == "User Console tour"
    assert d["description"].startswith("# User Console\n") and "1. Right-click Valid rows and select Preview." in d["description"]
    assert d["vimeo"]["id"] == VIDEO_ID


def test_the_placeholder_becomes_the_figure_crlf_kept_the_manifest_byte_for_byte_and_the_plan_writes_nothing(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch)
    c, pid = s["c"], s["pid"]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in (s["guide"], s["manifest"], s["course_json"])}
    name = s["record"]["walkthrough_name"]
    plan = _plan(c, pid)
    assert plan["how"] == "placeholder" and plan["course"] == "demo" and plan["slug"] == "01-first" and plan["changed"] is True
    assert plan["caption"] == name, "the default caption is the walkthrough's name"
    assert plan["link"] == f"https://vimeo.com/{VIDEO_ID}/{HASH}" and plan["allowed"] is False
    assert plan["guide_sha256"] == _sha(s["guide"]) and plan["manifest_sha256"] == _sha(s["manifest"])
    assert [f["path"] for f in plan["files"]] == ["01-first/guide.md", "01-first/manifest.json"]
    assert all(f["action"] == "write" for f in plan["files"])
    assert "+![Walkthrough](https://vimeo.com/123456789/abcdef1234)" in plan["diff"]
    assert f'+<figcaption class="pcm-video-caption">Watch: {name}</figcaption>' in plan["diff"]
    assert plan["lines"] == ["<figure>", "", f"![Walkthrough](https://vimeo.com/{VIDEO_ID}/{HASH})", "", f'<figcaption class="pcm-video-caption">Watch: {name}</figcaption>', "", "</figure>"]
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before, "the plan writes nothing"

    r = _place(c, pid, plan)
    assert r.status_code == 403 and r.json()["detail"] == course_video.PLACE_OFF_MESSAGE
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before

    config._config["courses_export"] = True
    plan = _plan(c, pid, caption="the User Console tour")
    r = _place(c, pid, plan)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["written"] == ["01-first/guide.md", "01-first/manifest.json"] and out["welcome"] is False
    assert out["message"] == "Placed the video in demo/01-first/guide.md. Review and publish it in the Content Editor."
    raw = s["guide"].read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n") and raw.count(b"\r\n") == before[s["guide"]][0].count(b"\r\n") + 2, "CRLF stays CRLF, the figure's two new lines too"
    expected = PLACEHOLDER_GUIDE.replace(
        "<figure>\n\n<figcaption class=\"pcm-video-caption\">Walkthrough: coming soon</figcaption>\n\n</figure>",
        f"<figure>\n\n![Walkthrough](https://vimeo.com/{VIDEO_ID}/{HASH})\n\n<figcaption class=\"pcm-video-caption\">Watch: the User Console tour</figcaption>\n\n</figure>",
    )
    assert raw == _crlf(expected).encode("utf-8")
    manifest = s["manifest"].read_bytes()
    assert manifest == _crlf(MANIFEST.replace('"hasVideo": false', '"hasVideo": true')).encode("utf-8"), "one key, byte for byte"
    assert s["course_json"].read_bytes() == before[s["course_json"]][0], "no welcome tour asked for"
    rows = [x for x in auth_store.list_audit(limit=50) if x["action"] == "walkthrough.place_video"]
    assert [(x["entity"], x["entity_id"], x["detail"]) for x in rows] == [("walkthrough", s["wid"], f"demo/01-first: vimeo {VIDEO_ID}")]

    # Placed: the plan now finds the figure of OURS and says there is nothing to change.
    plan = _plan(c, pid, caption="the User Console tour")
    assert plan["how"] == "figure" and plan["changed"] is False and plan["existing_caption"] == "the User Console tour"
    assert all(f["action"] == "unchanged" for f in plan["files"])


def test_an_lf_guide_stays_lf_and_a_guide_without_a_final_newline_keeps_that(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch, guide=PLACEHOLDER_GUIDE.rstrip("\n"), crlf=False, manifest=MANIFEST, course_json=COURSE_JSON_NULL_WELCOME)
    config._config["courses_export"] = True
    plan = _plan(s["c"], s["pid"])
    assert _place(s["c"], s["pid"], plan).status_code == 200
    raw = s["guide"].read_bytes()
    assert raw.count(b"\r\n") == 0 and not raw.endswith(b"\n")
    assert f"![Walkthrough](https://vimeo.com/{VIDEO_ID}/{HASH})\n\n<figcaption".encode() in raw
    manifest = s["manifest"].read_bytes()
    assert manifest.count(b"\r\n") == 0 and b'"hasVideo": true' in manifest


def test_an_existing_figure_of_ours_has_its_link_replaced_publish_again_place_again(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch, guide=FIGURE_GUIDE, manifest=MANIFEST.replace("false", "true"))
    c, pid = s["c"], s["pid"]
    plan = _plan(c, pid)
    assert plan["how"] == "figure" and plan["existing_caption"] == "the old one" and plan["caption"] == "the old one"
    assert plan["changed"] is True
    assert [f for f in plan["files"]] == [{"path": "01-first/guide.md", "action": "write"}, {"path": "01-first/manifest.json", "action": "unchanged"}]
    config._config["courses_export"] = True
    r = _place(c, pid, plan)
    assert r.status_code == 200, r.text
    assert r.json()["written"] == ["01-first/guide.md"]
    raw = s["guide"].read_bytes().decode("utf-8")
    assert f"![Walkthrough](https://vimeo.com/{VIDEO_ID}/{HASH})" in raw and "oldhash1" not in raw
    assert '<figcaption class="pcm-video-caption">Watch: the old one</figcaption>' in raw
    assert raw.count("![Walkthrough]") == 1 and raw.count("<figure>") == 1


def test_a_guide_with_neither_offers_the_insertion_after_the_first_section_heading(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch, guide=BARE_GUIDE)
    c, pid = s["c"], s["pid"]
    config._config["courses_export"] = True
    name = s["record"]["walkthrough_name"]
    plan = _plan(c, pid)
    assert plan["how"] == "none" and plan["changed"] is False and plan["heading"] == "## 1. Open it" and plan["line"] == 5
    assert plan["lines"][2] == f"![Walkthrough](https://vimeo.com/{VIDEO_ID}/{HASH})"
    assert plan["diff"] == ""
    r = _place(c, pid, plan)
    assert r.status_code == 400 and r.json()["detail"] == course_video.NO_PLACEHOLDER_MESSAGE
    assert s["guide"].read_bytes() == _crlf(BARE_GUIDE).encode("utf-8")

    plan = _plan(c, pid, insert=True)
    assert plan["how"] == "insert" and plan["changed"] is True and plan["line"] == 5
    r = _place(c, pid, plan, insert=True)
    assert r.status_code == 200, r.text
    expected = BARE_GUIDE.replace(
        "## 1. Open it\n\n",
        "## 1. Open it\n\n<figure>\n\n" f"![Walkthrough](https://vimeo.com/{VIDEO_ID}/{HASH})\n\n"
        f'<figcaption class="pcm-video-caption">Watch: {name}</figcaption>\n\n</figure>\n\n',
    )
    assert s["guide"].read_bytes() == _crlf(expected).encode("utf-8")


def test_the_welcome_tour_goes_into_course_json_keeping_its_other_keys(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch, course_json=COURSE_JSON_WELCOME)
    c, pid = s["c"], s["pid"]
    config._config["courses_export"] = True
    plan = _plan(c, pid, welcome=True, caption="the tour")
    assert plan["course_sha256"] == _sha(s["course_json"])
    assert [f["path"] for f in plan["files"]] == ["01-first/guide.md", "01-first/manifest.json", "course.json"]
    r = _place(c, pid, plan, welcome=True)
    assert r.status_code == 200, r.text
    assert r.json()["welcome"] is True and r.json()["written"][-1] == "course.json"
    raw = s["course_json"].read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")
    data = json.loads(raw.decode("utf-8"))
    assert data["welcome"] == {"eyebrow": "Try-It Lab", "caption": "Watch: the tour", "video": f"https://vimeo.com/{VIDEO_ID}/{HASH}"}
    assert data["theme"] == {"accent": "#2e7d32"} and data["title"] == "Demo course"
    text = raw.decode("utf-8")
    assert '  "theme": {\r\n    "accent": "#2e7d32"\r\n  }' in text, "the rest of the file byte for byte"
    rows = [x for x in auth_store.list_audit(limit=50) if x["action"] == "walkthrough.place_video"]
    assert rows[0]["detail"] == f"demo/01-first: vimeo {VIDEO_ID}, welcome tour"

    # A null welcome takes the two keys.
    s2_course = s["course_json"]
    s2_course.write_bytes(_crlf(COURSE_JSON_NULL_WELCOME).encode("utf-8"))
    plan = _plan(c, pid, welcome=True, caption="the tour")
    assert _place(c, pid, plan, welcome=True).status_code == 200
    data = json.loads(s2_course.read_bytes().decode("utf-8"))
    assert data["welcome"] == {"video": f"https://vimeo.com/{VIDEO_ID}/{HASH}", "caption": "Watch: the tour"}


def test_a_changed_guide_or_manifest_is_refused_with_409_and_nothing_is_written(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch)
    c, pid = s["c"], s["pid"]
    config._config["courses_export"] = True
    plan = _plan(c, pid)
    s["guide"].write_bytes(s["guide"].read_bytes() + b"\r\nEdited.\r\n")
    before = {p: p.read_bytes() for p in (s["guide"], s["manifest"])}
    r = _place(c, pid, plan)
    assert r.status_code == 409 and r.json()["detail"] == "The guide demo/01-first/guide.md changed since the preview: preview again."
    assert {p: p.read_bytes() for p in before} == before
    plan = _plan(c, pid)
    s["manifest"].write_bytes(s["manifest"].read_bytes().replace(b"25", b"30"))
    r = _place(c, pid, plan)
    assert r.status_code == 409 and r.json()["detail"] == "The file demo/01-first/manifest.json changed since the preview: preview again."
    assert s["guide"].read_bytes() == before[s["guide"]]


def test_a_failure_part_way_rolls_back_the_guide(rig, monkeypatch, fake):
    s = _course_project(rig, monkeypatch, course_json=COURSE_JSON_NULL_WELCOME)
    c, pid = s["c"], s["pid"]
    config._config["courses_export"] = True
    plan = _plan(c, pid, welcome=True)
    before = {p: p.read_bytes() for p in (s["guide"], s["manifest"], s["course_json"])}
    real_write = course_export.write_file

    def fail_on_course_json(folder, name, data):
        if name == "course.json":
            raise OSError(13, "Permission denied")
        return real_write(folder, name, data)

    monkeypatch.setattr(course_export, "write_file", fail_on_course_json)
    r = _place(c, pid, plan, welcome=True)
    assert r.status_code == 400 and r.json()["detail"].startswith("Could not write demo/course.json into the course: Permission denied")
    assert {p: p.read_bytes() for p in before} == before, "the guide and the manifest were put back"
    assert not [p for p in s["folder"].rglob(".*.bak")] and not [p for p in s["folder"].rglob(".*.keep")]
    assert not [x for x in auth_store.list_audit(limit=50) if x["action"] == "walkthrough.place_video"]


def test_place_is_refused_for_a_project_without_a_vimeo_video_or_a_course(rig, monkeypatch, fake):
    config._config["vimeo_token"] = TOKEN
    owner = rig["owner"]
    deck = _rendered_deck(rig)
    r = owner.post(f"/api/projects/{deck['id']}/vimeo/place/plan", json={"caption": "", "welcome": False, "insert": False})
    assert r.status_code == 400 and r.json()["detail"] == course_video.NOT_PUBLISHED_MESSAGE
    r = owner.post(f"/api/projects/{deck['id']}/vimeo/publish", json={"title": "T", "description": "", "privacy": "unlisted"})
    assert _wait_job(owner, r.json()["job_id"])["status"] == "done"
    r = owner.post(f"/api/projects/{deck['id']}/vimeo/place/plan", json={"caption": "", "welcome": False, "insert": False})
    assert r.status_code == 400 and r.json()["detail"] == course_video.NOT_FROM_COURSE_MESSAGE
    assert rig["other"].post(f"/api/projects/{deck['id']}/vimeo/place/plan", json={"caption": "", "welcome": False, "insert": False}).status_code == 403


def test_the_pure_rules_of_the_guide_and_the_json_edits():
    link = f"https://vimeo.com/{VIDEO_ID}/{HASH}"
    assert course_video.caption_text("the tour") == "Watch: the tour"
    assert course_video.caption_text("Watch: the tour") == "Watch: the tour"
    assert course_video.caption_text("  ") == "Watch: Walkthrough"
    # The placeholder is found only in the standard's form: a figure holding the caption alone.
    plain = PLACEHOLDER_GUIDE.split("\n")
    found = course_video.find_placeholder(plain)
    assert found is not None and plain[found.start] == "<figure>" and plain[found.end] == "</figure>"
    busy = [*plain]
    busy.insert(found.start + 1, "![Something](x.png)")
    assert course_video.find_placeholder(busy) is None
    assert course_video.find_figure(FIGURE_GUIDE.split("\n")) == 8
    assert course_video.first_section_heading(BARE_GUIDE.split("\n")) == 4
    assert course_video.first_section_heading(["# Title", "", "text"]) == 0, "no section: after the title"
    # The JSON edits: one key, the file's own spacing, nothing else touched; a missing key is added last.
    assert course_video.set_json_key('{\r\n  "a": 1,\r\n  "hasVideo": false\r\n}\r\n', "hasVideo", True) == '{\r\n  "a": 1,\r\n  "hasVideo": true\r\n}\r\n'
    assert course_video.set_json_key('{"hasVideo":false,"x":[1,2]}', "hasVideo", True) == '{"hasVideo":true,"x":[1,2]}'
    assert course_video.set_json_key('{\n  "a": 1\n}\n', "hasVideo", True) == '{\n  "a": 1,\n  "hasVideo": true\n}\n'
    assert course_video.set_json_key('{\n  "a": {\n    "hasVideo": false\n  }\n}\n', "hasVideo", True) == '{\n  "a": {\n    "hasVideo": false\n  },\n  "hasVideo": true\n}\n', "a nested key is not the top-level one"
    out = course_video.set_json_key(COURSE_JSON_WELCOME, "welcome", {"eyebrow": "Try-It Lab", "caption": "Watch: x", "video": link})
    assert json.loads(out)["welcome"] == {"eyebrow": "Try-It Lab", "caption": "Watch: x", "video": link}
    assert '  "welcome": {\n    "eyebrow": "Try-It Lab",\n    "caption": "Watch: x",\n    "video": "' + link + '"\n  },\n  "theme"' in out
    with pytest.raises(ValueError):
        course_video.set_json_key("[1, 2]", "hasVideo", True)
    with pytest.raises(ValueError):
        course_video.set_json_key('{"a": }', "hasVideo", True)


def test_the_limits_rows_name_the_vimeo_constants():
    text = LIMITS_MD.read_text(encoding="utf-8")
    for name in ("VIMEO_CHUNK_BYTES", "VIMEO_TRANSCODE_WAIT_S", "VIMEO_DESCRIPTION_MAX", "VIMEO_NAME_MAX", "VIMEO_CHUNK_TRIES"):
        assert f"`{name}`" in text, name
    assert vimeo.VIMEO_CHUNK_BYTES == 50 * 1024 * 1024 and vimeo.VIMEO_TRANSCODE_WAIT_S == 1800
