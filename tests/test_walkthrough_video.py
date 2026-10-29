"""Make a video (W3): a walkthrough compiled into a narrated video.

What these tests hold in place:

- **The narration (V4).** ``spoken_text`` is the ONE rule that turns a step's
  Markdown into spoken text: fenced code blocks dropped, inline code kept
  without its backticks, bold, emphasis, headings, list markers and links
  reduced to their text, lines joined; ``step_narration`` reads the step's
  narration (``narration_of``) through it and, with notes, the step's note as
  a second paragraph; a step with nothing to say is "" (a silent slide).
- **The stills (V3).** A picture is fitted into the 1920 x 1080 frame on the
  player's stage colour - scaled DOWN when larger, never up, centred - the
  scaling written once (``fit_in_frame``) and the hotspot carried through it
  (``focus_in_frame``); the hotspot ring is drawn into the still (4 px of the
  accent colour, a 1 px darker outline outside it) with or without the zoom;
  a transparent still composes onto the colour; a step without a picture is a
  text card on the colour; the focus is None without a hotspot or with Zoom
  off.
- **The project (V1).** ``create_walkthrough_project`` makes a project of kind
  ``walkthrough`` that is self-contained: a snapshot of the record and the
  pictures inside it, the inner engine project created at once with every
  slide's still, narration, focus and chapter (the steps before the first
  chapter are one chapter named after the walkthrough), so a generate never
  reaches PowerPoint; deleting the walkthrough afterwards changes nothing;
  a missing inner project is rebuilt from the snapshot; the slide editor
  works on it; the list and the detail answer ``walkthrough_id``.
- **The route (V2).** ``POST /api/walkthroughs/{wid}/video`` creates the
  project AND starts its generate job with the studio's defaults - the very
  request an empty-body generate makes - answering 201 ``{project_id,
  job_id}``; the owner or an administrator; a walkthrough with no steps is
  refused with 400 and nothing is made; the audit rows.
- **The zoom (V5).** ``focus_viewport`` at the start (the full frame), during
  the zoom, on the plateau (1.8 x on the focus), at the end (full again) and
  clamped inside the frame; a short slide holds still; the chunk graph's
  motion chain pinned; ``deck_fps`` rises for a run with motion; the focus and
  the chapter travel from the slide state to the clip and the segment.
- **The chapters (V6).** ``chapter_groups`` makes the walkthrough's chapters
  from the slides' spans; the deck's one-per-slide stays.
- **One real render**, on every real ffmpeg on the machine (the deck render's
  own gate): a two-step walkthrough at 320 x 180 - a step with a focus and one
  without - read back with ffprobe: the length, 24 fps, two chapters; and the
  plateau frame zoomed where the first and the last are full.

The TTS is never called: the route tests fake the processor as the generate
tests do, and the real render narrates with pink noise, as the deck's does.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

import utils.config as config_module
from api import store as auth_store
from core import video_creator
from core.project_manager import ProjectManager, SlideRenderState
from core.video_creator import DeckSegment, SlideClipInfo, VideoCreator
from services import ai_slides, file_item as file_item_module
from services import processing, slides, walkthrough_video, walkthroughs
from services import projects as store
from utils.config import config

from test_chapters import _ffprobe_beside, _probed_chapters
from test_deck_render import _noise
from test_generate import _FakeFileItem, _FakeVideoProcessor, _wait_job
from test_generation_options import REAL_FFMPEGS, needs_ffmpeg
from test_walkthroughs import _add, _body, _hotspot, _new, _put, _still, rig  # noqa: F401 - the rig fixture

ON_EVERY_FFMPEG = pytest.mark.parametrize("ffmpeg", REAL_FFMPEGS, ids=lambda p: Path(p).parent.parent.name or "ffmpeg")

FRAME = walkthrough_video.VIDEO_FRAME
STAGE = walkthrough_video.STAGE_COLOUR
RING = walkthrough_video.HOTSPOT_RING


# ── V4: the narration ─────────────────────────────────────────────────────────

def test_spoken_text_drops_fences_keeps_inline_code_and_strips_the_marks():
    spoken = walkthroughs.spoken_text
    assert spoken("Open **Spoon** and _wait_.") == "Open Spoon and wait."
    assert spoken("Type `SELECT 1` now.") == "Type SELECT 1 now."
    assert spoken("See [the guide](https://example.test/guide) first.") == "See the guide first."
    assert spoken("Run this:\n```sql\nSELECT * FROM orders;\n```\nThen continue.") == "Run this: Then continue."
    assert spoken("Before\n~~~\nhidden\n~~~\nafter") == "Before after"
    assert spoken("Unclosed:\n```\nnever spoken") == "Unclosed:"
    assert spoken("## Heading\n- first\n- second\n1. third\n> quoted") == "Heading first second third quoted"
    assert spoken("line one\r\nline two\n\n\nline three") == "line one line two line three"
    assert spoken("keep win_preview.ktr and 2*3 as they are") == "keep win_preview.ktr and 2*3 as they are"
    assert spoken("  ") == "" and spoken("") == "" and spoken(None) == ""


def test_step_narration_is_the_spoken_narration_with_the_note_as_a_second_paragraph():
    step = {"instruction": "Drag the **Text file input** onto the canvas.", "text": "Drag it", "note": "The step reads `files/*.csv`."}
    assert walkthroughs.step_narration(step, notes=False) == "Drag the Text file input onto the canvas."
    assert walkthroughs.step_narration(step, notes=True) == "Drag the Text file input onto the canvas.\n\nThe step reads files/*.csv."
    # The text when there is no instruction; nothing when there is neither (a silent slide); a blank note adds nothing.
    assert walkthroughs.step_narration({"instruction": "  ", "text": "Click *Next*", "note": "  "}, notes=True) == "Click Next"
    assert walkthroughs.step_narration({"instruction": "", "text": "", "note": "Only a note"}, notes=True) == "Only a note"
    assert walkthroughs.step_narration({"instruction": "", "text": "", "note": ""}, notes=True) == ""
    assert "[" not in walkthroughs.step_narration({"instruction": "a", "text": "", "note": "b"}, notes=True), "no bracket tags"


# ── V3: the stills ────────────────────────────────────────────────────────────

def test_fit_in_frame_scales_down_never_up_and_centres():
    fit = walkthrough_video.fit_in_frame
    assert fit(400, 300, (1920, 1080)) == (1.0, 760, 390, 400, 300), "smaller: never scaled up, centred"
    assert fit(3840, 2160, (1920, 1080)) == (0.5, 0, 0, 1920, 1080), "larger of the same aspect: fills"
    assert fit(1920, 1080, (1920, 1080)) == (1.0, 0, 0, 1920, 1080)
    scale, x, y, w, h = fit(4000, 1000, (1920, 1080))
    assert scale == pytest.approx(0.48) and (w, h) == (1920, 480) and (x, y) == (0, 300), "wider: bars above and below"
    scale, x, y, w, h = fit(1000, 4000, (1920, 1080))
    assert scale == pytest.approx(0.27) and (w, h) == (270, 1080) and (x, y) == (825, 0), "taller: bars at the sides"


def test_focus_in_frame_scales_and_offsets_the_hotspot():
    focus = walkthrough_video.focus_in_frame
    assert focus({"x": 100, "y": 100, "width": 50, "height": 30}, 400, 300, (1920, 1080)) == {"x": 860, "y": 490, "width": 50, "height": 30}
    assert focus({"x": 100, "y": 100, "width": 50, "height": 30}, 3840, 2160, (1920, 1080)) == {"x": 50, "y": 50, "width": 25, "height": 15}
    assert focus(None, 400, 300, (1920, 1080)) is None
    # Translation alone (the guided capture's frame) is the same maths at scale 1.
    assert focus({"x": 0, "y": 0, "width": 8, "height": 8}, 1920, 1080, (1920, 1080)) == {"x": 0, "y": 0, "width": 8, "height": 8}


def _picture(path: Path, size=(400, 300), colour=(0, 0, 255), mode="RGB") -> Path:
    Image.new(mode, size, colour).save(path)
    return path


def test_compose_still_fits_the_picture_onto_the_stage_and_draws_the_ring(tmp_path):
    picture = _picture(tmp_path / "p.png")
    step = {"hotspot": {"x": 100, "y": 100, "width": 50, "height": 30}, "text": "t", "chapter": ""}
    still, focus = walkthrough_video.compose_still(picture, step, "Tour", zoom=True)
    assert still.size == FRAME and still.mode == "RGB"
    assert focus == {"x": 860, "y": 490, "width": 50, "height": 30}
    assert still.getpixel((10, 10)) == STAGE, "the stage colour round the picture"
    assert still.getpixel((760, 390)) == (0, 0, 255) and still.getpixel((1159, 689)) == (0, 0, 255), "the picture centred, not scaled"
    assert still.getpixel((759, 390)) == STAGE
    # The ring: 4 px of the accent colour just outside the hotspot, a 1 px darker outline outside that, the hotspot untouched.
    assert still.getpixel((860 - 2, 490 + 15)) == RING.colour
    assert still.getpixel((860 - 4, 490 + 15)) == RING.colour and still.getpixel((860 - 1, 490 + 15)) == RING.colour
    assert still.getpixel((860 - 5, 490 + 15)) == RING.outline
    assert still.getpixel((860 - 6, 490 + 15)) == (0, 0, 255), "nothing beyond the outline"
    assert still.getpixel((860 + 25, 490 + 15)) == (0, 0, 255), "inside the hotspot the picture shows"
    assert still.getpixel((860 + 25, 490 + 30 + 2)) == RING.colour, "the ring below too"
    # With Zoom off the ring is still there, and there is no focus.
    still_off, focus_off = walkthrough_video.compose_still(picture, step, "Tour", zoom=False)
    assert focus_off is None and still_off.getpixel((860 - 2, 490 + 15)) == RING.colour
    # No hotspot: no ring, no focus.
    plain, none = walkthrough_video.compose_still(picture, {"hotspot": None, "text": "", "chapter": ""}, "Tour", zoom=True)
    assert none is None and plain.getpixel((860 - 2, 490 + 15)) == (0, 0, 255)


def test_compose_still_scales_a_large_picture_down_and_a_transparent_one_onto_the_colour(tmp_path):
    big = _picture(tmp_path / "big.png", (3840, 2160), (0, 200, 0))
    still, focus = walkthrough_video.compose_still(big, {"hotspot": {"x": 200, "y": 200, "width": 100, "height": 60}}, "Tour", zoom=True)
    assert still.size == FRAME and still.getpixel((5, 5)) == (0, 200, 0), "fills the frame"
    assert focus == {"x": 100, "y": 100, "width": 50, "height": 30}
    assert still.getpixel((100 - 2, 100 + 15)) == RING.colour, "the ring round the SCALED hotspot"
    clear = _picture(tmp_path / "clear.png", (400, 300), (255, 0, 0, 0), mode="RGBA")
    still, _ = walkthrough_video.compose_still(clear, {"hotspot": None}, "Tour", zoom=True)
    assert still.getpixel((960, 540)) == STAGE, "a transparent still composes onto the stage colour"
    # A smaller frame (the real render test's) scales the same way.
    small, focus = walkthrough_video.compose_still(big, {"hotspot": {"x": 0, "y": 0, "width": 960, "height": 540}}, "Tour", frame=(320, 180), zoom=True)
    assert small.size == (320, 180) and focus == {"x": 0, "y": 0, "width": 80, "height": 45}


def _dark(image: Image.Image, box) -> int:
    """How many pixels of ``box`` are not the stage colour."""
    region = image.crop(box)
    return sum(1 for px in region.getdata() if px != STAGE)


def test_a_step_without_a_picture_is_a_text_card_on_the_stage_colour(tmp_path):
    card, focus = walkthrough_video.compose_still(None, {"hotspot": None, "text": "Open the file", "chapter": "Start", "instruction": ""}, "Tour", zoom=True)
    assert card.size == FRAME and focus is None
    assert card.getpixel((5, 5)) == STAGE and card.getpixel((1914, 1074)) == STAGE
    w, h = FRAME
    assert _dark(card, (0, int(h * 0.4), w, int(h * 0.6))) > 500, "the text, large, in the middle"
    assert _dark(card, (0, int(h * 0.2), w, int(h * 0.4))) > 50, "the chapter, small, above it"
    assert _dark(card, (0, int(h * 0.6), w, int(h * 0.85))) > 50, "the walkthrough's name, small, below it"
    # The same card from text_card itself, and a step with no text at all still says which step it is.
    assert walkthrough_video.text_card("Open the file", "Start", "Tour").size == FRAME
    bare, _ = walkthrough_video.compose_still(None, {"hotspot": None, "text": "", "chapter": "", "instruction": ""}, "Tour", zoom=True, number=3)
    assert _dark(bare, (0, int(h * 0.4), w, int(h * 0.6))) > 100
    # A picture that is not there is a card too, not an error.
    missing, _ = walkthrough_video.compose_still(tmp_path / "gone.png", {"hotspot": None, "text": "x", "chapter": ""}, "Tour", zoom=True)
    assert missing.size == FRAME and missing.getpixel((5, 5)) == STAGE


# ── V1: the project ───────────────────────────────────────────────────────────

def _no_picture_step(sid: str, text: str = "", instruction: str = "", chapter: str = "", note: str = "") -> dict:
    return {"id": sid, "image": None, "width": 0, "height": 0, "hotspot": None, "kind": "tooltip", "text": text,
            "instruction": instruction, "placement": "auto", "chapter": chapter, "note": note, "source": None}


def _walkthrough(rig, monkeypatch, *, chapters=True, who="admin") -> dict:
    """Three steps: A with a picture, a hotspot, a chapter, a Markdown instruction and a note; B with a
    picture and a text only; C without a picture, with a text."""
    c = rig[who]
    cid = _still(rig, monkeypatch, 400, 300, colour=(0, 0, 255), who=who)
    w = _new(c, "Your First Win")
    rec = _add(c, w["id"], cid)
    rec = _add(c, w["id"], cid)
    a, b = rec["steps"]
    a.update({"hotspot": _hotspot(100, 100, 50, 30), "chapter": "Start" if chapters else "",
              "instruction": "Open **Spoon** and run:\n```\nspoon.bat\n```", "text": "Open Spoon", "note": "It reads `kettle.properties`."})
    b.update({"text": "Click *Next*", "instruction": "", "chapter": ""})
    steps = [a, b, _no_picture_step("cccccccccccc", text="Watch the grid", chapter="Finish" if chapters else "")]
    r = _put(c, w["id"], _body(rec, steps=steps))
    assert r.status_code == 200, r.text
    return r.json()


def _owner_of(rig, who="admin") -> tuple[str, str]:
    me = rig[who].get("/api/auth/me").json()["user"]
    return me["id"], me.get("display_name") or me["username"]


def _pm(record: dict) -> ProjectManager:
    pm = ProjectManager(store.PROJECTS_DIR / record["id"] / record["engine_project_dir"])
    pm.load()
    return pm


def test_create_walkthrough_project_makes_a_self_contained_project_with_its_stills(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch)
    wid = rec["id"]
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(wid, name="My video", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)
    pid = record["id"]
    assert record["kind"] == "walkthrough" and record["slide_count"] == 3
    assert record["walkthrough_id"] == wid and record["walkthrough_name"] == "Your First Win"
    assert record["name"] == "My video" and record["source_filename"] == "My-video.walkthrough.json"
    assert record["engine_project_dir"] == "My-video.walkthrough_project"
    assert record["images_source"] == "walkthrough" and record["images_rendered_at"]
    assert record["owner_id"] == owner_id and record["owner_name"] == owner_name
    assert record["size_bytes"] > 0 and record["created_at"]
    assert store.get_project(pid) == record, "written as project.json"

    pdir = store.PROJECTS_DIR / pid
    snapshot = json.loads((pdir / record["source_filename"]).read_text(encoding="utf-8"))
    assert snapshot["walkthrough_id"] == wid and snapshot["notes"] is True and snapshot["zoom"] is True
    assert snapshot["record"]["name"] == "Your First Win" and [s["id"] for s in snapshot["record"]["steps"]] == [s["id"] for s in rec["steps"]]
    pictures = sorted(p.name for p in (pdir / walkthrough_video.PICTURES_DIR).iterdir())
    assert pictures == sorted(s["image"] for s in rec["steps"] if s["image"]), "the pictures copied in"

    pm = _pm(record)
    assert [s.index for s in pm.state.slides] == [0, 1, 2]
    assert [Path(s.image_path).name for s in pm.state.slides] == ["step_001.png", "step_002.png", "step_003.png"]
    assert all(Path(s.image_path).is_file() and Path(s.image_path).parent == pm.images_dir for s in pm.state.slides)
    assert pm.all_images_ready(), "every slide has its image before the first generate: PPTXExporter is never reached"
    with Image.open(pm.state.slides[0].image_path) as still:
        assert still.size == FRAME and still.getpixel((860 - 2, 490 + 15)) == RING.colour, "the ring is in the still"
    assert pm.state.slides[0].focus == {"x": 860, "y": 490, "width": 50, "height": 30}
    assert pm.state.slides[1].focus is None and pm.state.slides[2].focus is None
    assert pm.state.slides[0].speaker_notes == "Open Spoon and run:\n\nIt reads kettle.properties.", "the fence dropped, the note a paragraph"
    assert pm.state.slides[1].speaker_notes == "Click Next"
    assert pm.state.slides[2].speaker_notes == "Watch the grid"
    assert [s.chapter for s in pm.state.slides] == ["Start", None, "Finish"]
    assert all(s.needs_regeneration for s in pm.state.slides)
    # The focus and the chapter survive a save and a load.
    pm.save()
    assert _pm(record).state.slides[0].focus == {"x": 860, "y": 490, "width": 50, "height": 30}
    assert _pm(record).state.slides[2].chapter == "Finish"


def test_the_options_and_the_chapter_rule(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch, chapters=False)
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Plain", notes=False, zoom=False, owner_id=owner_id, owner_name=owner_name)
    pm = _pm(record)
    assert pm.state.slides[0].speaker_notes == "Open Spoon and run:", "no note paragraph"
    assert pm.state.slides[0].focus is None, "Zoom off: no focus"
    with Image.open(pm.state.slides[0].image_path) as still:
        assert still.getpixel((860 - 2, 490 + 15)) == RING.colour, "the ring is there with or without the zoom"
    assert [s.chapter for s in pm.state.slides] == ["Your First Win", None, None], (
        "no step starts a chapter: the whole video is one chapter named after the walkthrough")


def test_the_project_is_self_contained_and_a_missing_inner_project_is_rebuilt(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch)
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Kept", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)
    pid = record["id"]
    before = _pm(record)
    # The walkthrough is edited and then deleted: the project does not move.
    steps = [dict(s, text="CHANGED", chapter="CHANGED") for s in rec["steps"]]
    assert _put(rig["admin"], rec["id"], _body(rec, steps=steps)).status_code == 200
    assert rig["admin"].delete(f"/api/walkthroughs/{rec['id']}").status_code == 204
    assert walkthroughs.get_walkthrough(rec["id"]) is None
    listed = slides.list_slides(pid)
    assert [s["speaker_notes"] for s in listed] == [s.speaker_notes for s in before.state.slides]
    assert all(s["has_image"] for s in listed)
    assert slides.images_ready(pid)
    # The slide editor's own notes rule: the snapshot's narration is the "original" a Reset goes back to.
    assert listed[1]["original_notes"] == "Click Next" and listed[1]["title"] == "Step 2" and listed[1]["body_text"] == "Click *Next*", (
        "the narration spoken; the body the step's text as the viewer reads it")
    edited = slides.update_slide(pid, 1, speaker_notes="My own words")
    assert edited["speaker_notes"] == "My own words" and edited["original_notes"] == "Click Next"
    assert slides.reset_slide(pid, 1)["speaker_notes"] == "Click Next"
    # A lost inner project is rebuilt from the snapshot: the same stills, narration, focus and chapters.
    inner = store.PROJECTS_DIR / pid / record["engine_project_dir"]
    shutil.rmtree(inner)
    assert not inner.exists()
    rebuilt = slides.manager(pid)
    assert [s.speaker_notes for s in rebuilt.state.slides] == [s.speaker_notes for s in before.state.slides]
    assert rebuilt.state.slides[0].focus == before.state.slides[0].focus
    assert [s.chapter for s in rebuilt.state.slides] == ["Start", None, "Finish"]
    assert rebuilt.all_images_ready()
    assert store.get_project(pid)["images_source"] == "walkthrough"
    # ``ensure_images`` answers cached for a project whose stills are all there, and composes a lost one again.
    assert slides.ensure_images(pid)["cached"] is True
    Path(rebuilt.state.slides[1].image_path).unlink()
    assert slides.ensure_images(pid)["cached"] is False
    assert slides.images_ready(pid)


def test_a_walkthrough_with_no_steps_or_none_at_all_is_refused(rig):
    owner_id, owner_name = _owner_of(rig)
    empty = _new(rig["admin"], "Empty")
    with pytest.raises(ValueError, match="This walkthrough has no steps yet."):
        store.create_walkthrough_project(empty["id"], name="x", notes=False, zoom=True, owner_id=owner_id, owner_name=owner_name)
    with pytest.raises(LookupError):
        store.create_walkthrough_project("aabbccddeeff", name="x", notes=False, zoom=True, owner_id=owner_id, owner_name=owner_name)
    assert store.list_projects() == [], "nothing was made"


def test_every_kind_site_admits_the_new_kind(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch)
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Sites", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)
    pid = record["id"]
    c = rig["admin"]
    assert "walkthrough" in slides.SLIDE_KINDS and "walkthrough" in slides.IMAGE_SOURCES
    assert store.kind_for_suffix(".walkthrough.json") is None, "the kind is given, never read from a suffix"
    listed = c.get("/api/projects").json()["projects"]
    assert [(p["id"], p["kind"], p["walkthrough_id"], p["walkthrough_name"]) for p in listed] == [(pid, "walkthrough", rec["id"], "Your First Win")]
    detail = c.get(f"/api/projects/{pid}").json()
    assert detail["walkthrough_id"] == rec["id"] and detail["walkthrough_name"] == "Your First Win"
    payload = c.get(f"/api/projects/{pid}/slides").json()
    assert len(payload["slides"]) == 3 and payload["images_source"] == "walkthrough" and payload["slides_ready"] is True
    assert c.get(f"/api/projects/{pid}/slides/0/image").status_code == 200
    assert c.patch(f"/api/projects/{pid}/slides/0", json={"speaker_notes": "Edited", "pause_override": 1.5}).status_code == 200
    assert c.post(f"/api/projects/{pid}/slides/0/undo").status_code == 200
    # Decks only, still: a still cannot be added and there is no deck to export.
    with pytest.raises(ValueError):
        slides.export_notes_pptx(pid)
    # The generate guard admits it (the job runs on a fake processor; the file item is the real one).
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = c.post(f"/api/projects/{pid}/generate", json={})
    assert r.status_code == 200, r.text
    assert _wait_job(c, r.json()["job_id"])["status"] == "done"
    assert store.get_project(pid)["output_video"] == "Sites.walkthrough.mp4"
    assert store.get_project(pid)["images_source"] == "walkthrough"
    # Deleting the project deletes the project alone.
    assert c.delete(f"/api/projects/{pid}").status_code == 204
    assert walkthroughs.get_walkthrough(rec["id"]) is not None


def test_the_generate_job_loads_a_walkthrough_project_through_load_walkthrough(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch)
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Loaded", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)

    class _Spy:
        loaded = None

        def __init__(self, path, projects_base=None):
            self.path = path

        def load(self):
            _Spy.loaded = "deck"
            return True

        def load_pdf(self):
            _Spy.loaded = "pdf"
            return True

        def load_walkthrough(self):
            _Spy.loaded = "walkthrough"
            return True

    monkeypatch.setattr(file_item_module, "FileItem", _Spy)
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = rig["admin"].post(f"/api/projects/{record['id']}/generate", json={})
    assert r.status_code == 200, r.text
    assert _wait_job(rig["admin"], r.json()["job_id"])["status"] == "done"
    assert _Spy.loaded == "walkthrough"


def test_load_walkthrough_keeps_the_edited_project_and_composes_only_what_is_missing(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch)
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Edit", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)
    pdir = store.PROJECTS_DIR / record["id"]
    source = pdir / record["source_filename"]
    pm = _pm(record)
    pm.update_slide_notes(1, "Edited by hand")
    pm.update_slide_pause(1, 2.0)
    first = pm.state.slides[0].image_path
    stamp = Path(first).stat().st_mtime_ns
    item = file_item_module.FileItem(source, projects_base=pdir)
    assert item.load_walkthrough() is True
    assert item.slide_count == 3 and item.has_project and item.project_manager is not None
    state = item.project_manager.state
    assert state.slides[1].speaker_notes == "Edited by hand" and state.slides[1].pause_override == 2.0, "the edit is kept"
    assert Path(first).stat().st_mtime_ns == stamp, "a still that is there is not composed again"
    Path(state.slides[2].image_path).unlink()
    assert file_item_module.FileItem(source, projects_base=pdir).load_walkthrough() is True
    assert Path(state.slides[2].image_path).is_file(), "a missing still is composed again"


# ── V2: the route ─────────────────────────────────────────────────────────────

def _video(c, wid: str, body: dict):
    return c.post(f"/api/walkthroughs/{wid}/video", json=body)


def test_make_video_creates_the_project_and_starts_its_generate_job(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch, who="owner")
    owner = rig["owner"]
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    _FakeVideoProcessor.last = None
    r = _video(owner, rec["id"], {"name": " Your First Win ", "notes": True, "zoom": True})
    assert r.status_code == 201, r.text
    pid, job_id = r.json()["project_id"], r.json()["job_id"]
    assert set(r.json()) == {"project_id", "job_id"}
    job = _wait_job(owner, job_id)
    assert job["status"] == "done" and job["project_id"] == pid
    record = store.get_project(pid)
    assert record["kind"] == "walkthrough" and record["name"] == "Your First Win" and record["walkthrough_id"] == rec["id"]
    assert record["owner_id"] == _owner_of(rig, "owner")[0], "owned by whoever made it"
    assert record["output_video"] == "Your-First-Win.walkthrough.mp4" and record["outputs"] == {"srt": "Your-First-Win.walkthrough.srt"}
    made = _FakeVideoProcessor.last
    assert made is not None and callable(made.cancel_check) and made.preview_seconds == 0

    # The studio's defaults, exactly as an empty-body generate resolves them.
    r = owner.post(f"/api/projects/{pid}/generate", json={})
    assert r.status_code == 200, r.text
    assert _wait_job(owner, r.json()["job_id"])["status"] == "done"
    again = _FakeVideoProcessor.last
    assert (made.provider, made.voice_id, made.speed, made.resolution, made.video_bitrate, made.x264_preset, made.h264_profile,
            made.audio_bitrate) == (again.provider, again.voice_id, again.speed, again.resolution, again.video_bitrate,
                                    again.x264_preset, again.h264_profile, again.audio_bitrate)
    assert made.kwargs == again.kwargs
    assert made.provider == "edge_tts" and made.speed == 1.0 and made.resolution == (1920, 1080)

    # The page the builder navigates to answers the running job's shape (the project page adopts it).
    assert owner.get(f"/api/projects/{pid}/job").json()["active_job"] is None, "finished by now"
    assert owner.get(f"/api/projects/{pid}/video").status_code == 200


def test_make_video_is_the_owners_or_an_administrators_and_refuses_an_empty_walkthrough(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch, who="owner")
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = _video(rig["other"], rec["id"], {"name": "x"})
    assert r.status_code == 403 and r.json()["detail"] == "This walkthrough is not yours."
    assert store.list_projects() == []
    r = _video(rig["admin"], rec["id"], {"name": "By admin"})
    assert r.status_code == 201, r.text
    assert _wait_job(rig["admin"], r.json()["job_id"])["status"] == "done"
    empty = _new(rig["owner"], "Empty")
    r = _video(rig["owner"], empty["id"], {"name": "Empty"})
    assert r.status_code == 400 and r.json()["detail"] == "This walkthrough has no steps yet."
    assert len(store.list_projects()) == 1, "nothing was made for the empty walkthrough"
    assert _video(rig["owner"], "aabbccddeeff", {"name": "x"}).status_code == 404
    for bad in ({"name": ""}, {"name": "   "}, {"name": "x", "notes": "yes"}, {"name": "x", "zoom": 1}, {"name": "x", "extra": 1}, {}):
        assert _video(rig["owner"], rec["id"], bad).status_code == 422, bad
    assert len(store.list_projects()) == 1


def test_make_video_defaults_and_the_audit_rows(rig, monkeypatch):
    rec = _walkthrough(rig, monkeypatch)
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = _video(rig["admin"], rec["id"], {"name": "Audited"})
    assert r.status_code == 201, r.text
    pid = r.json()["project_id"]
    assert _wait_job(rig["admin"], r.json()["job_id"])["status"] == "done"
    snapshot = json.loads((store.PROJECTS_DIR / pid / "Audited.walkthrough.json").read_text(encoding="utf-8"))
    assert snapshot["notes"] is False and snapshot["zoom"] is True, "notes off and zoom on when the body leaves them out"
    rows = auth_store.list_audit(limit=100)
    walk = [(x["action"], x["entity"], x["entity_id"], x["detail"]) for x in rows if x["action"] == "walkthrough.make_video"]
    assert walk == [("walkthrough.make_video", "walkthrough", rec["id"], f"Audited -> {pid}: 3 steps")]
    gen = [(x["action"], x["entity"], x["entity_id"]) for x in rows if x["action"] == "project.generate"]
    assert gen == [("project.generate", "project", pid)], "the generate job is recorded as any render is"
    assert "SECRET" not in json.dumps(rows)


# ── V5: the zoom ──────────────────────────────────────────────────────────────

FOCUS = {"x": 900, "y": 500, "width": 120, "height": 80}   # centre (960, 540): the frame's centre


def test_the_zoom_constants_are_the_rulings():
    assert (video_creator.ZOOM_AT_S, video_creator.ZOOM_IN_S, video_creator.ZOOM_OUT_S, video_creator.ZOOM_FACTOR) == (0.6, 0.8, 0.6, 1.8)
    assert video_creator.ZOOM_MOTION_S == pytest.approx(2.0)
    assert 0 < video_creator.ZOOM_SETTLE_S < video_creator.ZOOM_OUT_S


def test_focus_viewport_at_the_start_the_plateau_the_end_and_in_between():
    vp = video_creator.focus_viewport
    frame = (1920, 1080)
    assert vp(frame, FOCUS, 0.0, 10.0) == (0, 0, 1920, 1080), "the full frame from the start"
    assert vp(frame, FOCUS, 0.6, 10.0) == (0, 0, 1920, 1080), "until the zoom begins"
    x, y, w, h = vp(frame, FOCUS, 1.0, 10.0)
    assert 1920 / 1.8 < w < 1920 and 1080 / 1.8 < h < 1080 and x > 0 and y > 0, "half way in: between the two"
    assert x + w / 2 == pytest.approx(960) and y + h / 2 == pytest.approx(540), "centred on the focus"
    plateau = vp(frame, FOCUS, 1.4, 10.0)
    assert plateau == pytest.approx((960 - 1920 / 3.6, 540 - 1080 / 3.6, 1920 / 1.8, 1080 / 1.8)), "1.8 x on the focus"
    assert vp(frame, FOCUS, 5.0, 10.0) == pytest.approx(plateau) and vp(frame, FOCUS, 9.4, 10.0) == pytest.approx(plateau), "held"
    x, y, w, h = vp(frame, FOCUS, 9.7, 10.0)
    assert 1920 / 1.8 < w < 1920, "zooming back out"
    assert vp(frame, FOCUS, 9.95, 10.0) == (0, 0, 1920, 1080) and vp(frame, FOCUS, 10.0, 10.0) == (0, 0, 1920, 1080), (
        "the full frame before the end, so the cut is between full frames")
    # The easing is smooth: in at 1.0 s and out at 9.7 s are at the same place, and the motion never steps back.
    before = vp(frame, FOCUS, 0.0, 10.0)[2]
    for k in range(1, 200):
        width = vp(frame, FOCUS, 2.0 * k / 200, 10.0)[2]
        assert width <= before + 1e-9
        before = width


def test_focus_viewport_is_clamped_inside_the_frame_and_a_short_slide_holds_still():
    vp = video_creator.focus_viewport
    frame = (1920, 1080)
    corner = {"x": 0, "y": 0, "width": 40, "height": 40}
    assert vp(frame, corner, 1.4, 10.0) == pytest.approx((0, 0, 1920 / 1.8, 1080 / 1.8)), "clamped to the top-left"
    far = {"x": 1900, "y": 1060, "width": 20, "height": 20}
    x, y, w, h = vp(frame, far, 1.4, 10.0)
    assert x + w == pytest.approx(1920) and y + h == pytest.approx(1080), "clamped to the bottom-right"
    for t in (0.0, 0.5, 1.0, 1.5, 1.99):
        assert vp(frame, FOCUS, t, 1.99) == (0, 0, 1920, 1080), "shorter than the motion: no motion at all"
    assert vp(frame, FOCUS, 1.4, 2.0) != (0, 0, 1920, 1080), "exactly the motion's length moves"
    assert vp(frame, None, 1.4, 10.0) == (0, 0, 1920, 1080)
    assert video_creator.wants_motion(FOCUS, 2.0) and not video_creator.wants_motion(FOCUS, 1.99) and not video_creator.wants_motion(None, 10.0)
    assert vp((320, 180), {"x": 0, "y": 0, "width": 40, "height": 20}, 1.4, 5.0) == pytest.approx((0, 0, 320 / 1.8, 180 / 1.8))


def test_deck_fps_rises_for_a_run_with_motion():
    assert video_creator.deck_fps("none", 0.5) == video_creator.STATIC_FPS
    assert video_creator.deck_fps("none", 0.5, motion=True) == video_creator.TRANSITION_FPS
    assert video_creator.deck_fps("none", 0.5, animated=False, motion=False) == video_creator.STATIC_FPS


def test_the_chunk_graph_draws_the_zoom_on_a_focus_slide_and_leaves_the_others_still():
    seg = DeckSegment(4.0, image="s0.png", focus=FOCUS)
    still = DeckSegment(4.0, image="s1.png")
    short = DeckSegment(1.5, image="s2.png", focus=FOCUS)
    segments = [seg, DeckSegment(0.5, kind="pause"), still, DeckSegment(0.5, kind="pause"), short]
    bounds = video_creator.frame_boundaries([s.seconds for s in segments], 24)
    starts = [math.fsum(s.seconds for s in segments[:k]) for k in range(len(segments))]
    inputs, graph, count = video_creator.deck_chunk_graph(segments, bounds, starts, 0, len(segments), 24, (1920, 1080))
    assert inputs == ["-i", "s0.png", "-i", "s1.png", "-i", "s2.png"] and count == 3
    motion = video_creator._focus_filter(seg, (1920, 1080), 1.0)
    assert motion in graph, "the focus chain is in the graph"
    assert graph.count("eval=frame") == 1, "one moving slide; the still and the short one loop a single frame"
    assert "[s0p0]loop=loop=95:size=1:start=0,settb=AVTB,setpts=(N+0.000000)/24/TB+1/TB," + motion in graph, (
        "the whole slide is one part on the shifted clock")
    # The chain: a per-frame scale by the factor, the crop toward the focus centre, clamped inside the scaled frame.
    assert motion.startswith("scale=w='trunc(1920*(1+0.8*")
    assert "clip(((t-1)-0.6)/0.8,0,1)" in motion, "the zoom begins at 0.6 s over 0.8 s"
    assert f"clip(({4.0 - video_creator.ZOOM_SETTLE_S:.6f}-(t-1))/{video_creator.ZOOM_OUT_S - video_creator.ZOOM_SETTLE_S:g},0,1)" in motion, (
        "the zoom-out ends a settle before the end")
    assert ":eval=frame:flags=lanczos,crop=w=1920:h=1080:x='clip(trunc(960.0*(1+0.8*" in motion
    assert "-960),0,trunc(1920*(1+0.8*" in motion and ":exact=1" in motion
    # The labels count every segment, the pauses too: the still is s2, the short slide s4.
    assert "[s4p0]format=yuv420p,loop=loop=35:size=1:start=0" in graph, "the short slide holds still"
    assert "[s2p0]format=yuv420p,loop=loop=95:size=1:start=0" in graph, "the still slide loops one frame"


def test_a_transition_still_draws_on_a_focus_slide():
    """A fade on a moving slide is drawn after the motion; the clock is the slide's own, shifted."""
    creator = VideoCreator(resolution=(640, 360), fps=24, slide_transition="fade-to-black", transition_duration=0.5)
    clips = [SlideClipInfo(slide_index=i, image_path=Path(f"s{i}.png"), focus=FOCUS if i == 1 else None) for i in range(3)]
    segments = creator.deck_segments(clips, [3.0, 4.0, 3.0], [0.5, 0.5, 0.0])
    assert [s.focus for s in segments if s.kind == "slide"] == [None, FOCUS, None], "the focus rides on the segment"
    bounds = video_creator.frame_boundaries([s.seconds for s in segments], 24)
    starts = [math.fsum(s.seconds for s in segments[:k]) for k in range(len(segments))]
    _, graph, _ = video_creator.deck_chunk_graph(segments, bounds, starts, 0, len(segments), 24, (640, 360))
    moving = [chain for chain in graph.split(";\n") if "eval=frame" in chain]
    assert len(moving) == 1
    assert "fade=t=in:st=1.000000:d=0.500000" in moving[0] and "fade=t=out:st=4.500000:d=0.500000" in moving[0]
    assert moving[0].index("eval=frame") < moving[0].index("fade=t=in"), "the fade after the motion"


def test_the_slide_state_carries_the_focus_and_the_chapter_into_the_clip(tmp_path, monkeypatch):
    """``_build_video`` hands each slide's focus and chapter to the creator, asks 24 fps for a run with
    any focus, and passes the chapters beside the titles."""
    seen: dict = {}

    class _FakeCreator:
        def __init__(self, **kwargs):
            seen["fps"] = kwargs["fps"]

        def create_video(self, **kwargs):
            seen.update(kwargs)
            return True

    monkeypatch.setattr(processing, "VideoCreator", _FakeCreator)
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    pm = ProjectManager(tmp_path / "proj")
    pm.create_project(pptx_path=tmp_path / "x.walkthrough.json", slide_notes=["one", "two"], voice_id="")
    pm.state.slides[0].focus = dict(FOCUS)
    pm.state.slides[0].chapter = "Tour"
    pm.state.slides[1].chapter = None
    for s in pm.state.slides:
        s.audio_duration = 3.0
    processor = processing.VideoProcessor(provider="edge_tts")
    assert processor._build_video(pm, tmp_path / "out.mp4") is True
    clips = seen["slide_clips"]
    assert [c.focus for c in clips] == [FOCUS, None] and [c.chapter for c in clips] == ["Tour", None]
    assert seen["slide_chapters"] == ["Tour", None] and seen["slide_titles"] == ["one", "two"]
    assert seen["fps"] == video_creator.TRANSITION_FPS, "a run with a focus renders at the transition rate"
    # A deck's slides carry neither, and keep 2 fps.
    deck = ProjectManager(tmp_path / "deck")
    deck.create_project(pptx_path=tmp_path / "d.pptx", slide_notes=["a"], voice_id="")
    deck.state.slides[0].audio_duration = 3.0
    assert processor._build_video(deck, tmp_path / "deck.mp4") is True
    assert seen["slide_clips"][0].focus is None and seen["slide_clips"][0].chapter is None
    assert seen["slide_chapters"] == [None] and seen["fps"] == video_creator.STATIC_FPS


def test_the_slide_state_round_trips_the_new_fields_and_old_records_read_without_them(tmp_path):
    pm = ProjectManager(tmp_path / "p")
    pm.create_project(pptx_path=tmp_path / "x.json", slide_notes=["a"], voice_id="")
    pm.state.slides[0].focus = dict(FOCUS)
    pm.state.slides[0].chapter = "Start"
    pm.save()
    again = ProjectManager(tmp_path / "p")
    again.load()
    assert again.state.slides[0].focus == FOCUS and again.state.slides[0].chapter == "Start"
    data = json.loads((tmp_path / "p" / "project.json").read_text())
    for s in data["slides"]:
        s.pop("focus")
        s.pop("chapter")
    (tmp_path / "p" / "project.json").write_text(json.dumps(data))
    old = ProjectManager(tmp_path / "p")
    old.load()
    assert old.state.slides[0].focus is None and old.state.slides[0].chapter is None
    assert SlideRenderState(index=0, speaker_notes="").focus is None


# ── V6: the chapters ──────────────────────────────────────────────────────────

def test_chapter_groups_span_each_chapters_slides_and_fall_back_per_slide():
    spans = [(0, 3000), (3500, 6000), (6500, 9000), (9500, 12000)]
    titles = ["One", "Two", "Three", "Four"]
    assert video_creator.chapter_groups(spans, ["Start", None, "Finish", None], titles) == [(0, 6000, "Start"), (6500, 12000, "Finish")]
    assert video_creator.chapter_groups(spans, ["Tour", None, None, None], titles) == [(0, 12000, "Tour")]
    assert video_creator.chapter_groups(spans, ["A", "B", "C", "D"], titles) == [(0, 3000, "A"), (3500, 6000, "B"), (6500, 9000, "C"), (9500, 12000, "D")]
    # Slides before the first named one are chapters of their own, titled as a deck's are.
    assert video_creator.chapter_groups(spans, [None, None, "Late", None], titles) == [(0, 3000, "One"), (3500, 6000, "Two"), (6500, 12000, "Late")]
    assert video_creator.chapter_groups(spans, [None] * 4, titles) == [(s, e, t) for (s, e), t in zip(spans, titles)]
    assert video_creator.chapter_groups([], [], []) == []


def test_embed_chapters_gets_the_grouped_list_for_a_walkthrough_and_one_per_slide_for_a_deck(tmp_path, monkeypatch):
    written: list = []
    monkeypatch.setattr(video_creator, "embed_chapters", lambda path, chapters: written.append(chapters) or True)
    monkeypatch.setattr(video_creator, "_probe_duration", lambda path: 2.0)
    clips = []
    for i in range(3):
        audio = tmp_path / f"a{i}.wav"
        audio.write_bytes(b"x")
        clips.append(SlideClipInfo(slide_index=i, audio_path=audio))
    creator = VideoCreator(transition_pause=0.5, voice_start_delay=0.0)
    creator._embed_chapters(tmp_path / "v.mp4", clips, ["One", "Two", "Three"], slide_chapters=["Start", None, "End"])
    creator._embed_chapters(tmp_path / "v.mp4", clips, ["One", "Two", "Three"])
    creator._embed_chapters(tmp_path / "v.mp4", clips, ["One", "Two", "Three"], slide_chapters=[None, None, None])
    assert written == [
        [(0, 4500, "Start"), (5000, 7000, "End")],
        [(0, 2000, "One"), (2500, 4500, "Two"), (5000, 7000, "Three")],
        [(0, 2000, "One"), (2500, 4500, "Two"), (5000, 7000, "Three")],
    ]


# ── one real render ───────────────────────────────────────────────────────────

W, H, FPS = 320, 180, 24
QUADRANTS = {"tl": (220, 40, 40), "tr": (40, 200, 40), "bl": (40, 40, 220), "br": (230, 220, 40)}


def _quadrants(path: Path) -> Path:
    image = Image.new("RGB", (W, H))
    for name, box in (("tl", (0, 0, 160, 90)), ("tr", (160, 0, 320, 90)), ("bl", (0, 90, 160, 180)), ("br", (160, 90, 320, 180))):
        image.paste(QUADRANTS[name], box)
    image.save(path)
    return path


def _patch(frame, x: int, y: int, half: int = 4):
    """The mean colour of a small patch of a decoded frame (a numpy array) at (x, y)."""
    return frame[y - half:y + half, x - half:x + half].reshape(-1, 3).mean(axis=0)


def _near(colour, want, tolerance=24) -> bool:
    import numpy as np

    return bool(np.abs(np.asarray(colour, dtype=float) - np.asarray(want, dtype=float)).max() <= tolerance)


@needs_ffmpeg
@ON_EVERY_FFMPEG
def test_a_two_step_walkthrough_renders_with_its_zoom_and_its_chapters(tmp_path, monkeypatch, ffmpeg):
    import numpy as np

    ffprobe = _ffprobe_beside(ffmpeg)
    if ffprobe is None:
        pytest.skip(f"no ffprobe beside {ffmpeg}")
    monkeypatch.setattr(config_module, "FFMPEG_PATH", ffmpeg)
    monkeypatch.setattr(config_module, "TEMP_DIR", tmp_path / "temp")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    monkeypatch.setattr(video_creator, "_trim_leading_silence", lambda path, profile=None: path)

    # The stills as the project makes them, at the test's frame: a picture with a hotspot in its top-left
    # quadrant, and a step without a picture.
    picture = _quadrants(tmp_path / "step.png")
    hotspot = {"x": 20, "y": 20, "width": 40, "height": 20}
    first, focus = walkthrough_video.compose_still(picture, {"hotspot": hotspot, "text": "Open", "chapter": "Start"}, "Tour", frame=(W, H), zoom=True)
    second, none = walkthrough_video.compose_still(None, {"hotspot": None, "text": "Watch the grid", "chapter": "Next"}, "Tour", frame=(W, H), zoom=True)
    assert focus == hotspot and none is None
    first.save(tmp_path / "step_001.png")
    second.save(tmp_path / "step_002.png")
    narrations = []
    for i, seconds in enumerate((3.0, 2.5)):
        narration = tmp_path / f"n{i}.wav"
        _noise(ffmpeg, narration, seconds)
        narrations.append(narration)
    clips = [
        SlideClipInfo(slide_index=0, image_path=tmp_path / "step_001.png", audio_path=narrations[0], focus=focus, chapter="Start"),
        SlideClipInfo(slide_index=1, image_path=tmp_path / "step_002.png", audio_path=narrations[1], focus=None, chapter="Next"),
    ]
    fps = video_creator.deck_fps("none", 0.5, motion=any(video_creator.wants_motion(c.focus, 3.0) for c in clips))
    assert fps == FPS
    creator = VideoCreator(resolution=(W, H), fps=fps, transition_pause=0.5, voice_start_delay=0.0, audio_bitrate="192k",
                           pause_colour=walkthrough_video.STAGE_COLOUR_FFMPEG)
    out = tmp_path / "tour.mp4"
    assert creator.create_video(clips, out, slide_titles=["Open", "Watch the grid"], slide_chapters=["Start", "Next"]) is True

    info = json.loads(subprocess.run(
        [str(ffprobe), "-v", "error", "-count_frames", "-show_entries", "format=duration:stream=codec_type,r_frame_rate,nb_read_frames",
         "-of", "json", str(out)], capture_output=True, text=True, timeout=120, check=True).stdout)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert video["r_frame_rate"] == "24/1"
    frames = int(video["nb_read_frames"])
    expected = video_creator.frame_boundaries([3.0, 0.5, 2.5], FPS)[-1]
    assert frames == expected, "3.0 s, a 0.5 s pause and 2.5 s of picture"
    assert abs(float(info["format"]["duration"]) - 6.0) < 0.2
    chapters = _probed_chapters(ffprobe, out)
    # Written as the slides' spans (0-3.0, 3.5-6.0: the fake-embed test pins the list); the MP4 stores a chapter's
    # end as the next chapter's start, so the pause after a step reads back inside the chapter before it.
    assert [(round(s, 2), round(e, 2), t) for s, e, t in chapters] == [(0.0, 3.5, "Start"), (3.5, 6.0, "Next")]

    # The first frame is the full frame, the plateau is zoomed onto the hotspot, the last frame is full again.
    def frame_at(index: int) -> np.ndarray:
        raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(out), "-vf", f"select='eq(n,{index})'", "-fps_mode", "passthrough",
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=120, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.float64)

    start, plateau, last = frame_at(0), frame_at(int(1.5 * FPS)), frame_at(int(3.0 * FPS) - 1)
    assert _near(_patch(start, 150, 110), QUADRANTS["bl"]), "the full frame: below the middle is the bottom-left quadrant"
    assert _near(_patch(start, 150, 70), QUADRANTS["tl"])
    assert _near(_patch(plateau, 150, 110), QUADRANTS["tl"]), "zoomed onto the top-left: the same spot shows the top-left quadrant"
    assert _near(_patch(last, 150, 110), QUADRANTS["bl"]), "full again before the cut"
    # The ring is in the picture: the accent colour just left of the hotspot on the full frame.
    assert _near(_patch(start, 18, 30, half=1), RING.colour, 40)
    # Y5: the pause between two steps is the stage colour, not black (frame 72 is the 0.5 s pause's first frame).
    pause = frame_at(int(3.0 * FPS))
    assert _near(_patch(pause, 160, 90, half=8), STAGE, 10), f"the pause frame is {_patch(pause, 160, 90, half=8)}"
    assert _near(_patch(pause, 10, 10, half=4), STAGE, 10)


# ── the fix round (r1\w3-fix-brief.md) ────────────────────────────────────────

from test_generation_options import _pm as _deck_pm  # noqa: E402 - the deck's ProjectManager stand-in for the SRT


def _srt_blocks(text: str) -> list[list[str]]:
    """The SRT parsed by the blank-line rule every reader uses: blocks of consecutive non-empty lines."""
    blocks, current = [], []
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _is_cue(block: list[str]) -> bool:
    return len(block) >= 3 and block[0].isdigit() and "-->" in block[1]


# -- Y1: the per-slide SRT holds no blank line inside a cue ----------------------------------------------

def test_srt_cue_text_holds_no_blank_line():
    cue = processing.srt_cue_text
    assert cue("Open Spoon and run:\n\nIt reads kettle.properties.") == "Open Spoon and run:\nIt reads kettle.properties."
    assert cue("one\r\n\r\n\r\ntwo\r\n") == "one\ntwo"
    assert cue("  spaced  \n\n  lines  ") == "spaced\nlines"
    assert cue("single") == "single" and cue("\n\n") == "" and cue("") == ""


def test_a_walkthrough_projects_srt_keeps_a_narrated_note_inside_its_steps_cue(rig, monkeypatch, tmp_path):
    rec = _walkthrough(rig, monkeypatch)
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Subtitled", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)
    pm = _pm_of(record, (3.0, 2.0, 2.5))
    assert "\n\n" in pm.state.slides[0].speaker_notes, "the narrated note is a paragraph of its own in the narration"
    srt = processing._generate_srt(pm, tmp_path / "out.mp4", transition_pause=0.5)
    text = srt.read_text(encoding="utf-8")
    blocks = _srt_blocks(text)
    assert len(blocks) == 3, f"one cue per step, nothing adrift: {blocks}"
    assert all(_is_cue(block) for block in blocks), blocks
    assert [block[0] for block in blocks] == ["1", "2", "3"]
    assert blocks[0][1] == "00:00:00,000 --> 00:00:03,000"
    assert blocks[0][2:] == ["Open Spoon and run:", "It reads kettle.properties."], "the note inside its step's cue, as a line"
    assert blocks[1][1] == "00:00:03,500 --> 00:00:05,500" and blocks[1][2:] == ["Click Next"]
    assert blocks[2][2:] == ["Watch the grid"]
    assert "\n\n\n" not in text and text.count("-->") == 3


def test_a_decks_srt_with_a_blank_line_in_its_notes_gets_the_same_cure(tmp_path):
    pm = _deck_pm(("Para one.\n\nPara two.", 2.0), ("Second slide.", 1.0))
    text = processing._generate_srt(pm, tmp_path / "deck.mp4", transition_pause=1.0).read_text(encoding="utf-8")
    blocks = _srt_blocks(text)
    assert len(blocks) == 2 and all(_is_cue(block) for block in blocks), blocks
    assert blocks[0][2:] == ["Para one.", "Para two."]
    assert blocks[1][2:] == ["Second slide."]


def _pm_of(record: dict, durations) -> ProjectManager:
    """The project's inner state with a narration length per slide, as the render leaves it."""
    pm = ProjectManager(store.PROJECTS_DIR / record["id"] / record["engine_project_dir"])
    pm.load()
    for slide, seconds in zip(pm.state.slides, durations):
        slide.audio_duration = seconds
    return pm


# -- Y2: the card's long text stays in its band ----------------------------------------------------------

def _rows_with(image: Image.Image, colour) -> set[int]:
    """The rows holding at least one pixel of exactly ``colour``."""
    pixels = image.load()
    width, height = image.size
    rows = set()
    for y in range(height):
        for x in range(width):
            if pixels[x, y] == colour:
                rows.add(y)
                break
    return rows


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@pytest.mark.parametrize("length", [140, 300, 500, 1000])
def test_the_cards_text_never_shares_a_row_with_its_chapter_or_its_name(length):
    words = ("The learner opens the dialog, picks the file, confirms the delimiter and watches the grid fill. ") * 20
    text = words[:length].strip()
    assert len(text) >= length - 1
    card = walkthrough_video.text_card(text, "Chapter one", "Your First Win")
    big = _rows_with(card, _hex_rgb(walkthrough_video.CARD_TEXT_COLOUR))
    small = _rows_with(card, _hex_rgb(walkthrough_video.CARD_SMALL_COLOUR))
    assert big and small, "the text and the small lines are both drawn"
    assert not (big & small), f"{len(big & small)} rows carry both the text's and the small lines' colours at {length} characters"
    # The chapter above the text, the name below it: the small lines keep their bands.
    middle = (min(big) + max(big)) / 2
    above = {row for row in small if row < middle}
    below = {row for row in small if row > middle}
    assert above and below, "a chapter line above and a name line below"
    assert max(above) < min(big) < max(big) < min(below), "the text sits between the chapter and the name"


def test_fit_text_shrinks_then_cuts_at_a_word_with_an_ellipsis():
    font = walkthrough_video._card_font()
    short, size, cut = walkthrough_video.fit_text("Open the file", font, 1536, 298, 56, 28)
    assert size == 56 and cut is False and short.height <= 298
    long_text = ("The learner opens the dialog, picks the file, confirms the delimiter and watches the grid fill. ") * 12
    fitted, size, cut = walkthrough_video.fit_text(long_text, font, 1536, 298, 56, 28)
    assert fitted.height <= 298
    assert size == 28 and cut is True, "past every size the text is cut"
    # A text that fits at a smaller size is shrunk, not cut (300 characters fit at full size; 500 do not).
    same, size, cut = walkthrough_video.fit_text(long_text[:300], font, 1536, 298, 56, 28)
    assert size == 56 and cut is False and same.height <= 298
    mid, size, cut = walkthrough_video.fit_text(long_text[:500], font, 1536, 298, 56, 28)
    assert 28 <= size < 56 and cut is False and mid.height <= 298
    assert walkthrough_video.cut_to_fit("one two three four", 9) == "one two…"
    assert walkthrough_video.cut_to_fit("one two", 20) == "one two"
    assert walkthrough_video.cut_to_fit("supercalifragilistic", 5) == "supe…"


# -- Y3: Analyze on a walkthrough project ------------------------------------------------------------------

def test_analyze_works_on_a_walkthrough_project_from_its_snapshot(rig, monkeypatch):
    from core import ollama_client, slide_analyzer

    rec = _walkthrough(rig, monkeypatch)
    # A fourth step with nothing to say, so the rules have an empty narration to point at.
    steps = [dict(s) for s in rec["steps"]] + [_no_picture_step("dddddddddddd")]
    r = _put(rig["admin"], rec["id"], _body(rec, steps=steps))
    assert r.status_code == 200, r.text
    owner_id, owner_name = _owner_of(rig)
    record = store.create_walkthrough_project(rec["id"], name="Analysed", notes=True, zoom=True, owner_id=owner_id, owner_name=owner_name)
    monkeypatch.setattr(ollama_client, "check_connection", lambda base_url=None, timeout=None: False)
    monkeypatch.setattr(slide_analyzer, "_get_ai_suggestions", lambda *a, **k: pytest.fail("no model offline"))
    r = rig["admin"].post(f"/api/projects/{record['id']}/ai/analyze")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "PDF" not in r.text and "deck" not in r.text.lower().replace("deck", "", 0)
    assert [s["index"] for s in body["slides"]] == [0, 1, 2, 3]
    assert body["ai"] is False and body["summary"] and 1 <= body["overall_score"] <= 10
    assert any("No speaker notes" in issue for issue in body["slides"][3]["issues"]), "the step with nothing to say"
    assert not any("No speaker notes" in issue for issue in body["slides"][0]["issues"]), "the narration counts as the notes"
    # The slide data the analysis is built from: the step's text as the slide text, its picture as the visual.
    data = ai_slides._walkthrough_slide_data(record["id"])
    assert [d["index"] for d in data] == [0, 1, 2, 3]
    assert data[1]["text"] == "Click *Next*" and data[1]["has_images"] is True
    assert data[2]["has_images"] is False and data[3]["text"] == "" and data[3]["word_count"] == 0


# -- Y4: spoken_text and HTML --------------------------------------------------------------------------------

def test_spoken_text_strips_tags_unescapes_entities_and_honours_escaped_marks():
    spoken = walkthroughs.spoken_text
    assert spoken("line<br>next and <b>bold</b>") == "line next and bold"
    assert spoken("line<br/>next<BR />last") == "line next last"
    assert spoken("Fish &amp; chips &lt;tag&gt; &quot;quoted&quot;") == 'Fish & chips <tag> "quoted"'
    assert spoken("a\\*b\\*") == "a*b*"
    assert spoken("snake\\_case and \\# not a heading") == "snake_case and # not a heading"
    assert spoken("<p>Para</p><div>two</div>") == "Para two"
    assert spoken("~~gone~~ kept") == "gone kept"
    assert spoken("<img src=\"x.png\" alt=\"pic\"> after") == "after"


# -- Y5: the pause's colour ----------------------------------------------------------------------------------

def test_the_pause_takes_the_creators_colour_unless_the_transition_fades():
    clips = [SlideClipInfo(slide_index=i, image_path=Path(f"s{i}.png")) for i in range(3)]
    stage = walkthrough_video.STAGE_COLOUR_FFMPEG
    assert stage == "0xcbd5e1", "the stage colour as ffmpeg's color source takes it"
    pauses = lambda creator: [s.color for s in creator.deck_segments(clips, [2.0, 2.0, 2.0], [0.5, 0.5, 0.0]) if s.kind == "pause"]  # noqa: E731
    assert pauses(VideoCreator(pause_colour=stage)) == [stage, stage], "a walkthrough's pause is the stage colour"
    assert pauses(VideoCreator()) == ["black", "black"], "a deck's pause is black"
    assert pauses(VideoCreator(slide_transition="fade-to-white", transition_duration=0.5)) == ["white", "white"]
    assert pauses(VideoCreator(pause_colour=stage, slide_transition="fade-to-white", transition_duration=0.5)) == ["white", "white"], (
        "a fade's pause is the fade's colour, whatever the kind")
    assert pauses(VideoCreator(pause_colour=stage, slide_transition="fade-to-black", transition_duration=0.5)) == ["black", "black"]
    assert pauses(VideoCreator(pause_colour=stage, slide_transition="slide-left", transition_duration=0.5)) == [stage, stage]


def test_the_pause_colour_travels_from_the_route_through_the_processor_to_the_creator(rig, monkeypatch, tmp_path):
    real_processor = processing.VideoProcessor
    rec = _walkthrough(rig, monkeypatch, who="owner")
    monkeypatch.setattr(processing, "VideoProcessor", _FakeVideoProcessor)
    r = _video(rig["owner"], rec["id"], {"name": "Paused"})
    assert r.status_code == 201, r.text
    assert _wait_job(rig["owner"], r.json()["job_id"])["status"] == "done"
    assert _FakeVideoProcessor.last.kwargs.get("pause_colour") == walkthrough_video.STAGE_COLOUR_FFMPEG
    deck = store.import_upload("deck.pptx", b"pptx-bytes", owner_id=_owner_of(rig, "owner")[0])
    monkeypatch.setattr(file_item_module, "FileItem", _FakeFileItem)
    r = rig["owner"].post(f"/api/projects/{deck['id']}/generate", json={})
    assert r.status_code == 200, r.text
    assert _wait_job(rig["owner"], r.json()["job_id"])["status"] == "done"
    assert "pause_colour" not in _FakeVideoProcessor.last.kwargs, "a deck's processor is built exactly as before"
    # The processor hands it to the creator.
    seen: dict = {}

    class _FakeCreator:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def create_video(self, **kwargs):
            return True

    monkeypatch.setattr(processing, "VideoCreator", _FakeCreator)
    monkeypatch.setattr(processing, "TEMP_DIR", tmp_path / "temp")
    pm = ProjectManager(tmp_path / "proj")
    pm.create_project(pptx_path=tmp_path / "x.walkthrough.json", slide_notes=["one"], voice_id="")
    assert real_processor(provider="edge_tts", pause_colour="0xcbd5e1")._build_video(pm, tmp_path / "out.mp4") is True
    assert seen["pause_colour"] == "0xcbd5e1"
    assert real_processor(provider="edge_tts")._build_video(pm, tmp_path / "out2.mp4") is True
    assert seen["pause_colour"] is None


# -- Y6: the route makes the project, starts the job, and only then audits -------------------------------

def test_a_refused_start_removes_the_project_and_audits_nothing(rig, monkeypatch):
    from fastapi import HTTPException

    import api.routers.walkthroughs as walkthrough_routes

    rec = _walkthrough(rig, monkeypatch, who="owner")

    def refuse(record, body, user):
        raise HTTPException(status_code=400, detail="The narration provider is not available.")

    monkeypatch.setattr(walkthrough_routes, "start_generate", refuse)
    before = len(auth_store.list_audit(limit=200))
    r = _video(rig["owner"], rec["id"], {"name": "Refused"})
    assert r.status_code == 400 and r.json()["detail"] == "The narration provider is not available."
    assert store.list_projects() == [], "the project made for the job is removed again"
    assert not any((store.PROJECTS_DIR).iterdir()) if store.PROJECTS_DIR.exists() else True, "nothing on disk either"
    rows = auth_store.list_audit(limit=200)
    assert len(rows) == before, "nothing audited for a video that was not started"
    assert not any(x["action"] in ("walkthrough.make_video", "project.generate") for x in rows)
    assert walkthroughs.get_walkthrough(rec["id"]) is not None, "the walkthrough is untouched"

    # Any other failure of the start is the same: removed, re-raised to the app's handlers.
    def explode(record, body, user):
        raise RuntimeError("boom")

    monkeypatch.setattr(walkthrough_routes, "start_generate", explode)
    with pytest.raises(RuntimeError, match="boom"):
        _video(rig["owner"], rec["id"], {"name": "Exploded"})
    assert store.list_projects() == []
