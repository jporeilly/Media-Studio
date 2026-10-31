"""Export to course (W2c): a walkthrough imported from a course workshop written back into that workshop.

Every course here is a COPY under the test's own folder (the rig's courses folder, or the ``pdi-2hr-lab`` course
copied by the ``lab`` fixture): nothing reads or writes the content manager's ``courses`` folder itself.

- C1, the anchor: every step of the eight lab guides has its span (inside the guide, ordered, disjoint, from its
  item's line to the last line that is not blank before the next stopper) and its unique ``{key, ordinal}``; the
  span rules on an inline guide; the anchor survives Replace picture (the workshop source kept, the capture merged
  in), the builder's save (which does not send it) and a duplicate; the LCS alignment of a walkthrough imported
  before W2c against a guide edited since (one step inserted, one reworded) places the rest and reports the two;
- C2, the plan: it writes nothing (the course folder byte for byte and time for time), answers the placed, the
  not placed, the skipped, the files, the sha256 and the diff;
- C3, the figure: its exact text (the golden file, shared with the frontend's renderer-contract test), the
  escaping, the percent maths, the plain figure without a hotspot, the width cap;
- C4, idempotent: a second export writes nothing; a changed step is rewritten in place; a step no longer
  pictured (or gone) loses its block and its picture; another walkthrough's block, an authored figure and an
  unpaired marker are never touched;
- C5, the write: 403 with the setting off (the plan and the zips still work), the 409 of a guide changed since
  the plan, CRLF kept (and LF, and no final newline), a junction or link refused with nothing written outside the
  course, the audit row, ownership; the setting itself (admin-only, a real boolean);
- C6/C7, the zips: the course zip's layout, and the player bundle opened OFFLINE from the disk (jsdom, file://)
  playing the steps; the player written beside the guide with its link.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from api import store as auth_store
from services import course_export, walkthroughs, workshops
from test_workshops import (  # noqa: F401 - the fixtures are used by name
    GUIDES, LAB, _course, _link_directory, _png, _still, _workshop, lab, rig,
)
from utils.config import config

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "course_export" / "figure.golden.md"
PLAYER_CHECK = REPO_ROOT / "tests" / "player_bundle_check.mjs"

#: The inline workshop most tests export: a launcher (no picture), two items, the second with an AUTHORED house
#: figure (its picture is the guide's own), a hint, a second section of two items, and Lab Files.
GUIDE = (
    "# Demo workshop\n\n"
    "> **Warning:** Intro.\n\n"
    "## 1. Open it\n\n"
    '<button data-launch="spoon" data-path="files/win.ktr">Open in PDI</button>\n\n'
    "1. Right-click **Valid rows** and select **Preview**.\n"
    "2. Select **Quick Launch**.\n\n"
    '<div align="center">\n<figure>\n<img src="../_assets/images/authored.png" alt="An authored figure" width="400">\n'
    "<figcaption>Authored</figcaption>\n</figure>\n</div>\n\n"
    "A grid appears with the rows.\n\n"
    "> **Under the hood:**\n>\n> Why.\n\n"
    "## 2. Look inside\n\n"
    "1. Double-click **Read the file**.\n"
    "2. Select **Cancel**.\n\n"
    "## Lab Files\n\n"
    "[win.ktr](./files/win.ktr)\n"
)
HOTSPOT = {"x": 100, "y": 40, "width": 160, "height": 60}
#: The byte-order mark, as a character (written as an escape so the source shows it).
BOM = chr(0xFEFF)


def _crlf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


def _setup(rig, monkeypatch, guide: str = GUIDE, *, crlf: bool = True, picture=(2, 4, 5), hotspots=(2, 4), who="admin") -> dict:
    """The demo course with ``guide`` (CRLF by default, as the content manager's guides are), imported by ``who``;
    the steps numbered in ``picture`` given a still of 400 x 300 (a hotspot for those in ``hotspots``)."""
    folder = _course(rig["courses"], "demo")
    raw = (_crlf(guide) if crlf else guide).encode("utf-8")
    _workshop(folder, "01-first", "", images={"authored.png": _png((40, 30), (0, 128, 0))})
    (folder / "01-first" / "guide.md").write_bytes(raw)
    c = rig[who]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    rec = r.json()
    for number in picture:
        sid = rec["steps"][number - 1]["id"]
        cid = _still(rig, monkeypatch, 400, 300, who=who)
        body = {"capture_id": cid, "hotspot": HOTSPOT if number in hotspots else None}
        r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json=body)
        assert r.status_code == 200, r.text
        rec = r.json()
    return {"rec": rec, "folder": folder, "guide": folder / "01-first" / "guide.md", "images": folder / "_assets" / "images", "c": c}


def _plan(c, wid, player=False, caption=None):
    body = {"player": player} if caption is None else {"player": player, "caption": caption}
    r = c.post(f"/api/walkthroughs/{wid}/export/course/plan", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _write(c, wid, sha, player=False, caption=None):
    body = {"guide_sha256": sha, "player": player}
    if caption is not None:
        body["caption"] = caption
    return c.post(f"/api/walkthroughs/{wid}/export/course", json=body)


def _snapshot(folder: Path) -> dict:
    """Every file and folder under ``folder``: its bytes' sha256 and its modification time (folders: the time)."""
    out = {}
    for path in sorted(folder.rglob("*")):
        stat = path.stat()
        out[path.relative_to(folder).as_posix()] = (hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "dir", stat.st_mtime_ns)
    return out


def _without_blocks(text: str) -> str:
    """The guide with every Media Studio block taken out the way an export put it in: the blank line before the
    marker through the closing marker."""
    return re.sub(r"(\r?\n)\r?\n<!-- media-studio: walkthrough [0-9a-f]{12} (?:step [0-9a-f]{12}|player) -->\r?\n.*?<!-- /media-studio -->", "",
                  text, flags=re.S)


def _without_step_blocks(text: str) -> str:
    """The guide with the STEP blocks taken out and the player's block left (B3 reads where that one went)."""
    return re.sub(r"(\r?\n)\r?\n<!-- media-studio: walkthrough [0-9a-f]{12} step [0-9a-f]{12} -->\r?\n.*?<!-- /media-studio -->", "", text, flags=re.S)


# -- C1: the spans and the anchors ----------------------------------------------------------------------------

@pytest.mark.parametrize("slug", list(GUIDES))
def test_every_step_of_the_eight_lab_guides_has_its_span_and_its_anchor(lab, slug):
    course, _, text, _ = workshops.load_workshop(lab, LAB, slug)
    parsed = workshops.parse_guide(text, course["launchers"])
    lines = workshops.guide_lines(text)
    assert len(parsed.steps) == GUIDES[slug][0]
    item = re.compile(r"^ {0,3}\d+[.)]\s")
    previous_end = 0
    for step in parsed.steps:
        assert 1 <= step.start_line <= step.end_line <= len(lines), (step.start_line, step.end_line)
        assert step.start_line > previous_end, "spans are ordered and disjoint"
        first = lines[step.start_line - 1]
        assert item.match(first) or "data-launch" in first, f"a span starts at its item or launcher: {first!r}"
        assert lines[step.end_line - 1].strip(), "a span ends on a line that is not blank"
        inside, fenced = [], False
        for line in lines[step.start_line:step.end_line]:
            if line.lstrip().startswith(("```", "~~~")):
                fenced = not fenced
            elif not fenced:
                inside.append(line)
        assert not any(item.match(line) or line.lstrip().startswith((">", "#", ":::")) for line in inside), "no other step, hint or heading inside"
        assert step.key and len(step.key) <= walkthroughs.MAX_ANCHOR_KEY_CHARS and step.ordinal >= 1
        previous_end = step.end_line
    keys = [(s.key, s.ordinal) for s in parsed.steps]
    assert len(set(keys)) == len(keys), "an anchor names one step of the guide"


def test_first_win_spans_and_keys_spelled_out(lab):
    course, _w, text, _f = workshops.load_workshop(lab, LAB, "01-first-win")
    steps = workshops.parse_guide(text, course["launchers"]).steps
    spans = [(s.start_line, s.end_line) for s in steps]
    # The launcher alone (the item follows); the first item through its house figure; one-line items in a list; an
    # item through the prose after its figure, ending before the hint; the last item of the tab before the Note.
    assert spans == [(44, 44), (46, 62), (66, 66), (67, 79), (109, 109), (110, 116), (130, 133), (135, 135), (136, 138), (151, 151)]
    assert steps[0].key == "open the pipeline :: open win_preview.ktr in pentaho data integration."
    # "Select Cancel to close the dialog" twice in one section: one key, two ordinals.
    assert (steps[7].key, steps[7].ordinal) == (steps[9].key, 1) and steps[9].ordinal == 2


def test_the_span_rules_on_an_inline_guide():
    guide = (
        "## 3. Section\n\n"
        "1. One.\n   continued.\n\n   still one (loose).\n\n"  # a loose item: its indented lines are its own
        "A paragraph.\n\n<figure>\n<img src=\"../_assets/images/a.png\" alt=\"A\">\n</figure>\n\n"
        "```\n2. not an item\n```\n\n"
        "---\n\n"  # a rule ends the span
        "More after the rule.\n\n"
        "2. Two.\n\n<details>\n<summary>Why</summary>\n\n1. not a step\n</details>\n\n"
        ":::: tabs\n\n### Tab\n\n1. Three.\n\nTail.\n\n::::\n\n"
        "1. Four.\n"
    )
    steps = workshops.parse_guide(guide).steps
    lines = workshops.guide_lines(guide)
    got = [(s.text, lines[s.start_line - 1], lines[s.end_line - 1]) for s in steps]
    assert got == [
        ("One.", "1. One.", "```"),  # through the loose lines, the prose, the figure and the code block; before the rule
        ("Two.", "2. Two.", "2. Two."),  # before the <details>
        ("Three.", "1. Three.", "Tail."),  # before the closing ::: fence of the tabs
        ("Four.", "1. Four.", "1. Four."),  # to the end of the guide (its final blank line is no line of the span)
    ]
    assert [s.key for s in steps] == ["section :: one.", "section :: two.", "tab :: three.", "tab :: four."], "the section's number is left out"
    assert workshops.anchor_key("12) The   Part", "  Do   IT.") == "the part :: do it."
    assert workshops.anchor_key("", "x") == " :: x"
    assert len(workshops.anchor_key("s" * 500, "t" * 500)) <= walkthroughs.MAX_ANCHOR_KEY_CHARS
    assert walkthroughs.MAX_ANCHOR_ORDINAL == workshops.MAX_PARSE_STEPS, "an ordinal is at most the steps a parse reads"


def test_the_anchor_survives_replace_picture_a_save_and_a_duplicate(rig, monkeypatch):
    s = _setup(rig, monkeypatch, picture=(2,), hotspots=(2,))
    c, rec = s["c"], s["rec"]
    anchors = [st.get("anchor") for st in rec["steps"]]
    assert anchors[1] == {"key": "open it :: right-click valid rows and select preview.", "ordinal": 1}
    assert all(anchors), "every imported step has its anchor"
    pictured = rec["steps"][1]
    cid = pictured["source"]["capture_id"]
    assert pictured["source"] == {"course": "demo", "slug": "01-first", "image": None, "alt": "", "launcher": None, "capture_id": cid, "annotated": False}
    # The builder's save sends no anchor (normaliseStep keeps none): the record's own are kept.
    steps = [{k: v for k, v in st.items() if k != "anchor"} for st in rec["steps"]]
    r = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": steps})
    assert r.status_code == 200, r.text
    assert [st.get("anchor") for st in r.json()["steps"]] == anchors
    assert r.json()["steps"][1]["source"]["course"] == "demo", "the merged source round-trips through the save"
    # A document naming another anchor for a step the record holds: the record's wins.
    forged = [dict(st) for st in r.json()["steps"]]
    forged[1]["anchor"] = {"key": "elsewhere :: x.", "ordinal": 3}
    r = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": r.json()["revision"], "steps": forged})
    assert r.status_code == 200 and r.json()["steps"][1]["anchor"] == anchors[1]
    # The schema bounds it.
    revision = r.json()["revision"]
    for bad in ({"key": "", "ordinal": 1}, {"key": "a\nb", "ordinal": 1}, {"key": "k", "ordinal": 0},
                {"key": "k", "ordinal": walkthroughs.MAX_ANCHOR_ORDINAL + 1}, {"key": "k" * (walkthroughs.MAX_ANCHOR_KEY_CHARS + 1), "ordinal": 1},
                {"key": "k", "ordinal": 1, "extra": 1}, {"key": "k", "ordinal": "1"}):
        broken = [dict(st) for st in r.json()["steps"]]
        broken[0]["anchor"] = bad
        bad_r = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": revision, "steps": broken})
        assert bad_r.status_code == 400 and "anchor" in bad_r.json()["detail"], (bad, bad_r.text)
    # Another picture on the same step: still the anchor, still the workshop.
    cid2 = _still(rig, monkeypatch, 200, 100)
    r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{pictured['id']}/picture", json={"capture_id": cid2})
    assert r.json()["steps"][1]["anchor"] == anchors[1] and r.json()["steps"][1]["source"]["capture_id"] == cid2
    assert r.json()["steps"][1]["source"]["slug"] == "01-first"
    # A copy keeps them.
    copy = c.post(f"/api/walkthroughs/{rec['id']}/duplicate").json()
    assert [st.get("anchor") for st in copy["steps"]] == anchors
    # A step added from a still has none; a still replacing a non-workshop step's picture is the capture's own source.
    added = c.post(f"/api/walkthroughs/{rec['id']}/steps", json={"capture_id": cid2}).json()["steps"][-1]
    assert "anchor" not in added and added["source"] == {"capture_id": cid2, "annotated": False}
    assert walkthroughs.captured_source({"capture_id": "x"}, cid2, True) == {"capture_id": cid2, "annotated": True}


def test_alignment_of_a_walkthrough_imported_before_w2c_against_an_edited_guide(rig, monkeypatch):
    """A record from 0.17/0.18: no anchors, and a captured step's source is the capture's only. The guide edited
    since: one item inserted, one reworded. The rest is placed by its text, in order; the two are reported."""
    s = _setup(rig, monkeypatch, picture=(2, 4, 5), hotspots=(2,))
    c, rec = s["c"], s["rec"]
    stored_path = rig["tmp"] / "walkthroughs" / rec["id"] / "walkthrough.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    for step in stored["steps"]:
        step.pop("anchor", None)
        if step["source"].get("capture_id"):
            step["source"] = {"capture_id": step["source"]["capture_id"], "annotated": False}
    stored_path.write_text(json.dumps(stored), encoding="utf-8")
    edited = GUIDE.replace("1. Double-click **Read the file**.\n", "1. Open **Settings** first.\n2. Double-click **Read the file**.\n")
    edited = edited.replace("2. Select **Cancel**.", "3. Press **Cancel** to leave.")
    s["guide"].write_bytes(_crlf(edited).encode("utf-8"))
    plan = _plan(c, rec["id"])
    placed = {p["number"]: p for p in plan["placed"]}
    assert sorted(placed) == [2, 4], "the rest placed by their text"
    assert placed[4]["text"] == "Double-click Read the file.", "after the inserted step, at its own item"
    # Before W2c a captured step lost its workshop source and has no anchor: told apart from a step added in the
    # builder it cannot be, so its reason names both (K8 n2).
    assert [(n["number"], n["reason"]) for n in plan["not_placed"]] == [(5, course_export.ADDED_REASON)], "the reworded one reported"
    assert [n["text"] for n in plan["new_in_guide"]] == ["Open Settings first.", "Press Cancel to leave."], "the inserted and the reworded items"
    assert [(k["number"], k["reason"]) for k in plan["skipped"]] == [(1, course_export.NO_PICTURE_REASON), (3, course_export.GUIDE_PICTURE_REASON)]


def test_anchors_place_what_the_text_alone_cannot():
    """The pure rule: walkthrough steps reordered by their owner. By anchor both are placed; by text alone (a
    walkthrough before W2c) the longest common subsequence keeps the order and reports the one out of it."""
    guide = "## A\n\n1. Alpha.\n2. Beta.\n3. Gamma.\n"
    parsed = workshops.parse_guide(guide).steps
    def step(text, anchor=None):
        return {"text": text, "instruction": text, **({"anchor": anchor} if anchor else {})}

    anchored = [step("Gamma.", {"key": "a :: gamma.", "ordinal": 1}), step("Alpha.", {"key": "a :: alpha.", "ordinal": 1})]
    assert course_export.align(anchored, parsed) == ({0: 2, 1: 0}, {})
    by_text = [step("Gamma."), step("Alpha.")]
    placed, why = course_export.align(by_text, parsed)
    assert len(placed) == 1 and list(why.values()) == [course_export.ADDED_REASON], "no anchor, no workshop source: added"
    # An anchor the guide no longer has falls back to the text; a step with no text says so.
    assert course_export.align([step("Beta.", {"key": "gone :: beta.", "ordinal": 1}), {"text": "", "instruction": ""}], parsed) == (
        {0: 1}, {1: course_export.NO_TEXT_REASON})
    # Who is what, unplaced (K8 n2): an imported step (an anchor, or the workshop's source) whose item went is "not in
    # the guide any more"; a step added in the builder (neither) is "added in the walkthrough, not in the guide".
    workshop = {"course": "c", "slug": "s", "image": None, "alt": "", "launcher": None, "capture_id": "aaaaaaaaaaaa"}
    lost = [step("Delta.", {"key": "a :: delta.", "ordinal": 1}), {**step("Epsilon."), "source": workshop},
            {**step("Zeta."), "source": {"capture_id": "aaaaaaaaaaaa", "annotated": False}}]
    assert course_export.align(lost, parsed)[1] == {0: course_export.NOT_FOUND_REASON, 1: course_export.NOT_FOUND_REASON, 2: course_export.ADDED_REASON}
    assert course_export.lcs_pairs(["a", "b", "c", ""], ["b", "", "c", "a"]) in ([(1, 0), (2, 2)],)


# -- C2: the plan ------------------------------------------------------------------------------------------------

def test_the_plan_writes_nothing_and_answers_the_placement(rig, monkeypatch):
    s = _setup(rig, monkeypatch)
    c, rec = s["c"], s["rec"]
    before = _snapshot(s["folder"])
    audit_before = len(auth_store.list_audit(action="walkthrough.export_course"))
    plan = _plan(c, rec["id"])
    _needs_player()
    plan_with_player = _plan(c, rec["id"], player=True)
    assert c.get(f"/api/walkthroughs/{rec['id']}/export/course.zip").status_code == 200
    assert c.get(f"/api/walkthroughs/{rec['id']}/export/course.zip?player=1").status_code == 200
    assert c.get(f"/api/walkthroughs/{rec['id']}/export/player.zip").status_code == 200
    assert _snapshot(s["folder"]) == before, "the plan and the zips write nothing, not even a folder"
    assert len(auth_store.list_audit(action="walkthrough.export_course")) == audit_before
    steps = rec["steps"]
    assert plan["guide"] == "demo/01-first/guide.md"
    assert plan["guide_sha256"] == hashlib.sha256(s["guide"].read_bytes()).hexdigest()
    wid = rec["id"]
    assert [(p["number"], p["text"], p["image"], p["hotspot"], p["how"]) for p in plan["placed"]] == [
        (2, "Right-click Valid rows and select Preview.", f"wt-01-first-{wid}-{steps[1]['id']}.png", {"x": "45.0", "y": "23.3"}, "new"),
        (4, "Double-click Read the file.", f"wt-01-first-{wid}-{steps[3]['id']}.png", {"x": "45.0", "y": "23.3"}, "new"),
        (5, "Select Cancel.", f"wt-01-first-{wid}-{steps[4]['id']}.png", None, "new"),
    ]
    assert plan["not_placed"] == [] and plan["removed"] == [] and plan["new_in_guide"] == [] and plan["kept"] == []
    assert [(k["number"], k["reason"]) for k in plan["skipped"]] == [(1, course_export.NO_PICTURE_REASON), (3, course_export.GUIDE_PICTURE_REASON)]
    assert plan["files"] == [{"path": "01-first/guide.md", "action": "write"}] + [
        {"path": f"_assets/images/{p['image']}", "action": "write"} for p in plan["placed"]]
    assert plan["changed"] is True and plan["figures"] == 3 and plan["allowed"] is False and plan["player"] is False
    assert plan["diff"].startswith("--- a/01-first/guide.md\n+++ b/01-first/guide.md\n@@ ")
    assert plan["diff"].count(f"+<!-- media-studio: walkthrough {rec['id']} step ") == 3 and "\n-" not in plan["diff"], "only lines added"
    # Every step with a picture - the guide's own too: the player shows the whole walkthrough.
    assert {f["path"] for f in plan_with_player["files"] if "walkthrough/" in f["path"]} == {
        "01-first/walkthrough/index.html", "01-first/walkthrough/walkthrough.json",
        *(f"01-first/walkthrough/step-{st['id']}.png" for st in steps if st["image"])}
    assert f"+<!-- media-studio: walkthrough {rec['id']} player -->" in plan_with_player["diff"]


def test_who_may_export_and_what_cannot_be(rig, monkeypatch):
    s = _setup(rig, monkeypatch, who="owner")
    rec, owner, other, admin = s["rec"], rig["owner"], rig["other"], rig["admin"]
    for method, url, body in (("post", "export/course/plan", {}), ("post", "export/course", {"guide_sha256": "0" * 64}),
                              ("get", "export/course.zip", None), ("get", "export/player.zip", None)):
        call = getattr(other, method)
        r = call(f"/api/walkthroughs/{rec['id']}/{url}", **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (url, r.status_code)
    assert admin.post(f"/api/walkthroughs/{rec['id']}/export/course/plan", json={}).status_code == 200
    assert owner.post(f"/api/walkthroughs/{rec['id']}/export/course/plan").status_code == 200, "no body: the player off"
    assert owner.post("/api/walkthroughs/aabbccddeeff/export/course/plan", json={}).status_code == 404
    assert owner.post(f"/api/walkthroughs/{rec['id']}/export/course/plan", json={"player": "yes"}).status_code == 422
    assert owner.post(f"/api/walkthroughs/{rec['id']}/export/course", json={"guide_sha256": "nope"}).status_code == 422
    # Not imported: nothing to place it in - but its player can be exported.
    plain = owner.post("/api/walkthroughs", json={"name": "Plain"}).json()
    r = owner.post(f"/api/walkthroughs/{plain['id']}/export/course/plan", json={})
    assert r.status_code == 400 and r.json()["detail"] == course_export.NOT_IMPORTED_MESSAGE
    assert owner.get(f"/api/walkthroughs/{plain['id']}/export/player.zip").status_code == 200
    # The workshop gone from the course: 404; the courses folder unset: 400 saying where to set it.
    shutil.rmtree(s["folder"] / "01-first")
    assert owner.post(f"/api/walkthroughs/{rec['id']}/export/course/plan", json={}).status_code == 404
    config._config["courses_folder"] = ""
    r = owner.post(f"/api/walkthroughs/{rec['id']}/export/course/plan", json={})
    assert r.status_code == 400 and r.json()["detail"] == workshops.NOT_SET_MESSAGE


# -- C3: the figure --------------------------------------------------------------------------------------------

def test_the_figure_is_the_golden_text_escaped_with_the_percent_maths():
    step = {"id": "bbbbbbbbbbbb", "width": 1600, "height": 900, "hotspot": {"x": 100, "y": 50, "width": 160, "height": 60},
            "text": "Click \"Run\" &  <wait>\n for O'Brien's [FIELD] rows.", "instruction": "ignored"}
    lines = course_export.figure_lines("aaaaaaaaaaaa", step, 7, course_export.image_name("01-first", "aaaaaaaaaaaa", "bbbbbbbbbbbb"), "3. Run it & check")
    golden = GOLDEN.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert "\n".join(lines) + "\n" == golden
    # 180 / 1600 = 11.25 % -> 11.3 (half up, in whole numbers); 80 / 900 = 8.888 % -> 8.9; the width capped at 800.
    assert course_export.centre_percent(100, 160, 1600) == "11.3" and course_export.centre_percent(50, 60, 900) == "8.9"
    assert [course_export.centre_percent(*a) for a in ((0, 1, 200), (0, 1, 80), (0, 400, 400), (0, 0, 400), (390, 20, 400), (5, 10, 0))] == [
        "0.3", "0.6", "50.0", "0.0", "100.0", "0.0"]
    # Without a hotspot: the plain house figure, no <ol>; a step without text says its narration, else "Step N".
    plain = course_export.figure_lines("aaaaaaaaaaaa", {**step, "hotspot": None, "width": 640, "text": ""}, 2, "x.png", "")
    assert plain[3:7] == ["<figure>", '<img src="../_assets/images/x.png" alt="ignored" width="640">', "<figcaption>Step 2</figcaption>", "</figure>"]
    assert "<ol" not in "\n".join(plain)
    assert course_export.figure_text({"text": "", "instruction": ""}, 9) == "Step 9"


# -- C4 + C5: the write, idempotent and guarded ----------------------------------------------------------------

def test_the_write_then_a_second_export_writes_nothing_and_changes_are_made_in_place(rig, monkeypatch):
    s = _setup(rig, monkeypatch)
    c, rec, guide, images = s["c"], s["rec"], s["guide"], s["images"]
    config._config["courses_export"] = True
    original = guide.read_bytes()
    plan = _plan(c, rec["id"])
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["message"] == "Wrote 3 figures into demo/01-first/guide.md. Review and publish it in the Content Editor."
    assert result["figures"] == 3 and result["images"] == 3 and result["guide_written"] is True
    written = guide.read_bytes()
    assert written == plan_bytes(plan, original), "the guide is what the plan's diff said"
    assert _without_blocks(written.decode("utf-8")) == original.decode("utf-8"), "the rest of the guide byte for byte"
    for p in plan["placed"]:
        assert (images / p["image"]).read_bytes() == (rig["tmp"] / "walkthroughs" / rec["id"] / f"step-{p['step_id']}.png").read_bytes()
    rows = auth_store.list_audit(action="walkthrough.export_course")
    assert [(row["entity_id"], row["detail"]) for row in rows] == [(rec["id"], "demo/01-first: 3 figures, 3 images")]
    # The exported guide still parses to the same steps: a figure is no step, and its picture is the step's.
    again = workshops.parse_guide(written.decode("utf-8"), {"spoon": "Pentaho Data Integration"})
    assert [st.text for st in again.steps] == [st["text"] for st in rec["steps"]]
    assert [st.image for st in again.steps][1] == f"../_assets/images/{plan['placed'][0]['image']}"
    # A second export: nothing to write, byte for byte and time for time.
    snap = _snapshot(s["folder"])
    second = _plan(c, rec["id"])
    assert second["changed"] is False and second["diff"] == "" and {f["action"] for f in second["files"]} == {"unchanged"}
    assert [p["how"] for p in second["placed"]] == ["unchanged"] * 3
    r = _write(c, rec["id"], second["guide_sha256"])
    assert r.status_code == 200 and r.json()["written"] == [] and r.json()["removed"] == []
    assert r.json()["message"] == "Nothing to write: demo/01-first/guide.md already holds 3 figures of this walkthrough."
    assert _snapshot(s["folder"]) == snap
    # A changed hotspot: that block rewritten IN PLACE, the others untouched.
    rec = c.get(f"/api/walkthroughs/{rec['id']}").json()
    steps = [dict(st) for st in rec["steps"]]
    steps[1]["hotspot"] = {"x": 0, "y": 0, "width": 40, "height": 30}
    rec = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": steps}).json()
    third = _plan(c, rec["id"])
    assert [p["how"] for p in third["placed"]] == ["replaced", "unchanged", "unchanged"]
    # A new block's line is the guide line it goes after; an existing block's, its marker's (one blank line on).
    assert third["placed"][0]["hotspot"] == {"x": "5.0", "y": "5.0"} and third["placed"][0]["line"] == plan["placed"][0]["line"] + 2
    assert _write(c, rec["id"], third["guide_sha256"]).status_code == 200
    text = guide.read_bytes().decode("utf-8")
    assert '<li data-x="5.0" data-y="5.0">' in text and _without_blocks(text) == original.decode("utf-8")
    # Steps reordered (the numbers move) and one deleted: its block and its picture go; the others keep their pictures'
    # names (the number is not in them, K1) - the step that moved under another chapter gets that caption, in place.
    wid, gone = rec["id"], steps[4]["id"]
    order = [rec["steps"][i] for i in (0, 3, 1, 2)]
    rec = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": order}).json()
    fourth = _plan(c, rec["id"])
    assert [(p["number"], p["image"], p["how"]) for p in fourth["placed"]] == [
        (2, f"wt-01-first-{wid}-{steps[3]['id']}.png", "unchanged"), (3, f"wt-01-first-{wid}-{steps[1]['id']}.png", "replaced")]
    assert [(rm["step_id"], rm["reason"]) for rm in fourth["removed"]] == [(gone, "The step is no longer in the walkthrough.")]
    assert {f["path"] for f in fourth["files"] if f["action"] == "remove"} == {f"_assets/images/wt-01-first-{wid}-{gone}.png"}
    assert {f["path"] for f in fourth["files"] if f["action"] == "write"} == {"01-first/guide.md"}, "no picture rewritten"
    r = _write(c, rec["id"], fourth["guide_sha256"])
    assert r.status_code == 200 and len(r.json()["removed"]) == 1
    assert sorted(p.name for p in images.iterdir()) == sorted(["authored.png", f"wt-01-first-{wid}-{steps[3]['id']}.png", f"wt-01-first-{wid}-{steps[1]['id']}.png"])
    text = guide.read_bytes().decode("utf-8")
    assert gone not in text and _without_blocks(text) == original.decode("utf-8")
    moved = re.search(rf"step {steps[1]['id']} -->(.*?)<!-- /media-studio -->", text, re.S).group(1)
    assert "<figcaption>2. Look inside</figcaption>" in moved, "the moved step's caption is its new chapter"
    # A step whose picture is the guide's own once more (a new import) never gets a block; its old block would go.
    assert walkthroughs.has_guide_picture({"image": "x.png", "source": {"course": "d", "slug": "s", "image": "_assets/images/a.png"}})
    assert not walkthroughs.has_guide_picture({"image": "x.png", "source": {"course": "d", "slug": "s", "image": None, "capture_id": "c"}})


def plan_bytes(plan: dict, original: bytes) -> bytes:
    """The guide the plan's unified diff describes, applied to ``original`` with its own line ending (a small
    patcher: the diff is the plan's only promise of what the write makes)."""
    eol = "\r\n" if b"\r\n" in original else "\n"
    old = original.decode("utf-8").replace("\r\n", "\n").split("\n")
    out, pos = [], 0
    for hunk in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@\n((?:[ +\-].*\n?)*)", plan["diff"] + "\n", re.M):
        start = int(hunk.group(1)) - (0 if hunk.group(2) == "0" else 1)
        out += old[pos:start]
        pos = start
        for line in hunk.group(3).rstrip("\n").split("\n"):
            if line.startswith(" "):
                out.append(old[pos])
                pos += 1
            elif line.startswith("-"):
                pos += 1
            elif line.startswith("+"):
                out.append(line[1:])
    out += old[pos:]
    return eol.join(out).encode("utf-8")


def test_another_walkthroughs_block_an_authored_figure_and_an_unpaired_marker_are_never_touched(rig, monkeypatch):
    s = _setup(rig, monkeypatch, picture=(2, 4), hotspots=(2,))
    c, a, guide = s["c"], s["rec"], s["guide"]
    config._config["courses_export"] = True
    b = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first", "name": "Second"}).json()
    sid = b["steps"][1]["id"]
    cid = _still(rig, monkeypatch, 300, 200)
    b = c.post(f"/api/walkthroughs/{b['id']}/steps/{sid}/picture", json={"capture_id": cid, "hotspot": HOTSPOT}).json()
    assert _write(c, a["id"], _plan(c, a["id"])["guide_sha256"]).status_code == 200
    after_a = guide.read_bytes().decode("utf-8")
    # An unpaired marker an author left behind, and a hand edit inside an authored figure's caption.
    after_a = after_a.replace("<figcaption>Authored</figcaption>", "<figcaption>Authored, edited</figcaption>")
    after_a = after_a.replace("A grid appears", f"<!-- media-studio: walkthrough {a['id']} step {a['steps'][2]['id']} -->\r\n\r\nA grid appears")
    guide.write_bytes(after_a.encode("utf-8"))
    plan_b = _plan(c, b["id"])
    assert [p["how"] for p in plan_b["placed"]] == ["new"]
    assert _write(c, b["id"], plan_b["guide_sha256"]).status_code == 200
    after_b = guide.read_bytes().decode("utf-8")

    def blocks(text: str, wid: str) -> list[str]:
        """The text of each well-formed block of ``wid`` (an unpaired marker is none)."""
        lines = workshops.guide_lines(text)
        return ["\n".join(lines[m.start:m.end + 1]) for m in course_export.marker_blocks(lines) if m.wid == wid]

    assert blocks(after_b, a["id"]) == blocks(after_a, a["id"]) and len(blocks(after_a, a["id"])) == 2
    assert len(blocks(after_b, b["id"])) == 1
    assert after_b.index(blocks(after_b, a["id"])[0].replace("\n", "\r\n")) < after_b.index(blocks(after_b, b["id"])[0].replace("\n", "\r\n")) < after_b.index(
        "2. Select **Quick Launch**."), "B's figure after A's, in the same step"
    # A again: its blocks rewritten in place - unchanged - B's, the authored figure and the unpaired marker untouched.
    plan_a = _plan(c, a["id"])
    assert plan_a["changed"] is False and [p["how"] for p in plan_a["placed"]] == ["unchanged", "unchanged"]
    assert "<figcaption>Authored, edited</figcaption>" in after_b and after_b.count(f"step {a['steps'][2]['id']} -->") == 1
    # B with its step's picture gone back to none? A step can lose its picture only by going: B's step deleted.
    b = c.get(f"/api/walkthroughs/{b['id']}").json()
    b = c.put(f"/api/walkthroughs/{b['id']}", json={"version": 1, "name": b["name"], "revision": b["revision"], "steps": [st for st in b["steps"] if st["id"] != sid]}).json()
    plan_b = _plan(c, b["id"])
    assert [rm["step_id"] for rm in plan_b["removed"]] == [sid]
    assert _write(c, b["id"], plan_b["guide_sha256"]).status_code == 200
    assert guide.read_bytes().decode("utf-8") == after_a, "B's block gone, exactly as it came; everything else as A left it"


def test_a_guide_changed_since_the_preview_is_refused_with_409_and_nothing_is_written(rig, monkeypatch):
    s = _setup(rig, monkeypatch)
    c, rec = s["c"], s["rec"]
    config._config["courses_export"] = True
    plan = _plan(c, rec["id"])
    s["guide"].write_bytes(s["guide"].read_bytes() + b"An edit in the Content Editor.\r\n")
    before = _snapshot(s["folder"])
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 409 and r.json()["detail"] == "The guide demo/01-first/guide.md changed since the preview: preview again."
    assert _snapshot(s["folder"]) == before, "nothing written: not a picture, not the guide"
    assert auth_store.list_audit(action="walkthrough.export_course") == []
    # Previewed again, it goes through.
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200


def test_the_guide_keeps_its_line_endings_and_its_last_line(rig, monkeypatch):
    _needs_player()
    player = True  # the player's link is the block an export appends at the very end
    for crlf, trailing, bom in ((True, True, False), (False, True, False), (True, False, False), (True, True, True)):
        if (rig["courses"] / "demo").exists():
            shutil.rmtree(rig["courses"] / "demo")
        guide = GUIDE if trailing else GUIDE.rstrip("\n")
        s = _setup(rig, monkeypatch, (BOM if bom else "") + guide, crlf=crlf)
        config._config["courses_export"] = True
        original = s["guide"].read_bytes()
        assert _write(s["c"], s["rec"]["id"], _plan(s["c"], s["rec"]["id"], player=player)["guide_sha256"], player=player).status_code == 200
        raw = s["guide"].read_bytes()
        crlf_count = raw.count(b"\r\n")
        if crlf:
            assert crlf_count == raw.count(b"\n") and crlf_count > original.count(b"\r\n"), "CRLF stays CRLF, new lines too"
        else:
            assert crlf_count == 0, "LF stays LF"
        assert raw.endswith(b"\n") == trailing, "the last line as it was"
        assert _without_blocks(raw.decode("utf-8")) == original.decode("utf-8")
        assert raw.startswith(b"\xef\xbb\xbf") == bom, "the byte-order mark as it was"


def test_the_limits_rows_name_the_export_constants():
    """The two rows Export to course adds (``tests/test_docs.py`` checks every row's line references)."""
    text = (REPO_ROOT / "docs" / "reference" / "limits.md").read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith(("| Walkthrough anchor", "| Export to course"))}
    expected = {
        "Walkthrough anchor": [(f"{walkthroughs.MAX_ANCHOR_KEY_CHARS} characters", "MAX_ANCHOR_KEY_CHARS"), (f"1 to {walkthroughs.MAX_ANCHOR_ORDINAL}", "MAX_ANCHOR_ORDINAL")],
        "Export to course figure": [(f"{course_export.MAX_FIGURE_WIDTH} pixels", "MAX_FIGURE_WIDTH"), ("2 MB", "MAX_GUIDE_BYTES")],
        # K6: the zips' bound - the walkthrough's own steps times a still's cap.
        "Export to course zip": [(f"{walkthroughs.MAX_STEPS} pictures", "MAX_STEPS"), ("64 MB", "MAX_ANNOTATED_BYTES"), ("32,000 pixels", "MAX_DIMENSION")],
    }
    for what, pairs in expected.items():
        assert what in rows, f"no '{what}' row in docs/reference/limits.md"
        for number, name in pairs:
            assert number in rows[what] and f"`{name}`" in rows[what], (what, number, name)
    assert "behind Export to course's, `MAX_ANCHOR_KEY_CHARS`, `MAX_ANCHOR_ORDINAL`, `MAX_FIGURE_WIDTH` and `MAX_CAPTION_CHARS`" in text
    # B3: the embedded player's row names the caption's cap and the fixed second sentence.
    assert "Export to course player" in rows and f"{course_export.MAX_CAPTION_CHARS} characters" in rows["Export to course player"]
    assert "`MAX_CAPTION_CHARS`" in rows["Export to course player"] and "`PLAYER_CAPTION_TAIL`" in rows["Export to course player"]


def test_nothing_is_written_through_a_junction_or_outside_the_course(rig, monkeypatch, tmp_path):
    s = _setup(rig, monkeypatch)
    c, rec, folder = s["c"], s["rec"], s["folder"]
    config._config["courses_export"] = True
    outside = tmp_path / "outside"
    outside.mkdir()
    original = s["guide"].read_bytes()
    # The images folder a junction to somewhere else: refused before anything is written, anywhere.
    moved = tmp_path / "images-moved"
    shutil.move(str(s["images"]), str(moved))
    if not _link_directory(s["images"], outside):
        pytest.skip("no junction or symlink can be made here")
    before = _snapshot(folder)
    r = _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"])
    assert r.status_code == 400 and "link or a junction" in r.json()["detail"], r.text
    assert list(outside.iterdir()) == [] and _snapshot(folder) == before
    s["images"].rmdir()  # the junction itself; its target is untouched
    shutil.move(str(moved), str(s["images"]))
    # The player's folder a junction: refused, nothing written there, into the images or into the guide.
    _needs_player()
    assert _link_directory(folder / "01-first" / "walkthrough", outside)
    before = _snapshot(folder)
    r = _write(c, rec["id"], _plan(c, rec["id"], player=True)["guide_sha256"], player=True)
    assert r.status_code == 400 and "link or a junction" in r.json()["detail"], r.text
    assert list(outside.iterdir()) == [] and _snapshot(folder) == before
    (folder / "01-first" / "walkthrough").rmdir()
    assert s["guide"].read_bytes() == original
    # The course folder itself a junction elsewhere: not a course at all.
    elsewhere = tmp_path / "elsewhere"
    shutil.copytree(folder, elsewhere / "demo2", symlinks=True, ignore=shutil.ignore_patterns("walkthrough"))
    assert _link_directory(rig["courses"] / "linked", elsewhere / "demo2")
    stored_path = rig["tmp"] / "walkthroughs" / rec["id"] / "walkthrough.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    stored["source"]["course"] = "linked"
    stored_path.write_text(json.dumps(stored), encoding="utf-8")
    assert _write(c, rec["id"], "0" * 64).status_code == 404
    assert c.post(f"/api/walkthroughs/{rec['id']}/export/course/plan", json={}).status_code == 404
    # The one write routine refuses a target that is a link, and writes nothing for the same bytes.
    target = tmp_path / "plain"
    target.mkdir()
    assert course_export.write_file(target, "a.txt", b"x") is True and course_export.write_file(target, "a.txt", b"x") is False
    assert sorted(p.name for p in target.iterdir()) == ["a.txt"], "no temporary file left behind"
    assert course_export.own_folder(target, "new") == target / "new" and not (target / "new").exists()
    (target / "a-file").write_bytes(b"")
    with pytest.raises(course_export.ExportRefused):
        course_export.own_folder(target, "a-file")


def test_the_setting_off_refuses_the_write_and_the_plan_and_the_zips_still_work(rig, monkeypatch):
    s = _setup(rig, monkeypatch)
    c, rec, admin = s["c"], s["rec"], rig["admin"]
    plan = _plan(c, rec["id"])
    assert plan["allowed"] is False
    before = _snapshot(s["folder"])
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 403 and r.json()["detail"] == course_export.EXPORT_OFF_MESSAGE
    assert _snapshot(s["folder"]) == before
    assert c.get(f"/api/walkthroughs/{rec['id']}/export/course.zip").status_code == 200
    # The setting: off by default, an administrator's, a real boolean, audited by its key.
    assert admin.get("/api/settings/studio").json()["settings"]["courses_export"] is False
    assert rig["other"].put("/api/settings/studio", json={"courses_export": True}).status_code == 403
    assert admin.put("/api/settings/studio", json={"courses_export": "yes"}).status_code == 422
    assert admin.put("/api/settings/studio", json={"courses_export": 1}).status_code == 422
    r = admin.put("/api/settings/studio", json={"courses_export": True})
    assert r.status_code == 200 and r.json()["settings"]["courses_export"] is True
    assert auth_store.list_audit(action="settings.update")[0]["detail"] == "courses_export"
    assert _plan(c, rec["id"])["allowed"] is True
    assert _write(c, rec["id"], plan["guide_sha256"]).status_code == 200
    from services import studio_settings
    with pytest.raises(ValueError):
        studio_settings.update_settings({"courses_export": "true"})
    config._config["courses_export"] = "true"
    assert studio_settings.courses_export_allowed() is False, "only a stored True turns it on"


# -- the fix round (K1, K3, K4, K5, K8 n3) ----------------------------------------------------------------------------

def _contents(folder: Path) -> dict:
    """Every file and folder under ``folder`` by its bytes' sha256 ("dir" for a folder) - not its times (a rollback
    puts the bytes back, and a folder's time moves whenever a file comes and goes in it)."""
    return {p.relative_to(folder).as_posix(): (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "dir")
            for p in sorted(folder.rglob("*"))}


def test_a_duplicate_never_writes_over_its_originals_pictures(rig, monkeypatch):
    """K1 (the Reviewer's R3): W exported; its Duplicate D - which keeps W's step ids - given another picture and
    exported into the same workshop. Each picture's name carries its walkthrough's id: W's file is byte-identical
    afterwards, the two figures name two files, and W's next plan has nothing to do."""
    s = _setup(rig, monkeypatch, picture=(2,), hotspots=(2,))
    c, w, images = s["c"], s["rec"], s["images"]
    config._config["courses_export"] = True
    plan_w = _plan(c, w["id"])
    assert _write(c, w["id"], plan_w["guide_sha256"]).status_code == 200
    sid = w["steps"][1]["id"]
    w_name = plan_w["placed"][0]["image"]
    w_bytes = (images / w_name).read_bytes()
    d = c.post(f"/api/walkthroughs/{w['id']}/duplicate").json()
    assert d["steps"][1]["id"] == sid, "a duplicate keeps its original's step ids"
    cid = _still(rig, monkeypatch, 300, 200)  # another picture: another size, other bytes
    d = c.post(f"/api/walkthroughs/{d['id']}/steps/{sid}/picture", json={"capture_id": cid, "hotspot": HOTSPOT}).json()
    plan_d = _plan(c, d["id"])
    d_name = plan_d["placed"][0]["image"]
    assert _write(c, d["id"], plan_d["guide_sha256"]).status_code == 200
    assert (images / w_name).read_bytes() == w_bytes, "the original's picture is untouched by its duplicate's export"
    assert d_name != w_name and (w_name, d_name) == (f"wt-01-first-{w['id']}-{sid}.png", f"wt-01-first-{d['id']}-{sid}.png")
    assert {f["path"]: f["action"] for f in plan_d["files"]}.get(f"_assets/images/{w_name}") is None, "D's plan never names W's file"
    assert (images / d_name).read_bytes() == (rig["tmp"] / "walkthroughs" / d["id"] / f"step-{sid}.png").read_bytes()
    text = s["guide"].read_bytes().decode("utf-8")
    assert f"_assets/images/{w_name}" in text and f"_assets/images/{d_name}" in text
    again = _plan(c, w["id"])
    assert again["changed"] is False and {f["action"] for f in again["files"]} == {"unchanged"}


def test_an_unwritable_course_is_refused_before_a_byte_and_a_failure_midway_rolls_back(rig, monkeypatch):
    """K3: a read-only guide, a read-only picture the export would replace, a folder that takes no new file - each
    refused with 400 naming the file, before a byte is written; a failure met part-way (the guide's replace
    refused after pictures were written - one created, one replaced) rolls everything back: the course folder as it
    was, no temporary or backup file left, no audit row."""
    s = _setup(rig, monkeypatch, picture=(2,), hotspots=(2,))
    c, rec, guide, images, folder = s["c"], s["rec"], s["guide"], s["images"], s["folder"]
    config._config["courses_export"] = True
    message = "Could not write {} into the course: {}. Nothing was changed: download the zip instead."
    # A read-only guide.
    plan = _plan(c, rec["id"])
    before = _contents(folder)
    os.chmod(guide, stat.S_IREAD)
    try:
        r = _write(c, rec["id"], plan["guide_sha256"])
    finally:
        os.chmod(guide, stat.S_IREAD | stat.S_IWRITE)
    assert r.status_code == 400 and r.json()["detail"] == message.format("demo/01-first/guide.md", "it is read-only"), r.text
    assert _contents(folder) == before and auth_store.list_audit(action="walkthrough.export_course") == []
    # A folder that takes no new file (the probe refused): named, nothing written.
    real_probe = course_export._probe

    def refusing(target):
        if target == images:
            raise PermissionError(13, "Access is denied")
        return real_probe(target)

    monkeypatch.setattr(course_export, "_probe", refusing)
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 400 and r.json()["detail"] == message.format("demo/_assets/images", "Access is denied"), r.text
    assert _contents(folder) == before
    monkeypatch.setattr(course_export, "_probe", real_probe)
    # Exported; then a read-only picture the next export would replace.
    assert _write(c, rec["id"], plan["guide_sha256"]).status_code == 200
    sid = rec["steps"][1]["id"]
    name = f"wt-01-first-{rec['id']}-{sid}.png"
    cid = _still(rig, monkeypatch, 300, 200)
    rec = c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json={"capture_id": cid}).json()
    plan = _plan(c, rec["id"])
    before = _contents(folder)
    os.chmod(images / name, stat.S_IREAD)
    try:
        r = _write(c, rec["id"], plan["guide_sha256"])
    finally:
        os.chmod(images / name, stat.S_IREAD | stat.S_IWRITE)
    assert r.status_code == 400 and r.json()["detail"] == message.format(f"demo/_assets/images/{name}", "it is read-only"), r.text
    assert _contents(folder) == before
    # Part-way: the launcher step gets a picture (a new file), step 2's is replaced, then the guide's replace fails.
    launcher = rec["steps"][0]["id"]
    rec = c.post(f"/api/walkthroughs/{rec['id']}/steps/{launcher}/picture", json={"capture_id": _still(rig, monkeypatch, 320, 240), "hotspot": HOTSPOT}).json()
    plan = _plan(c, rec["id"])
    assert {f["path"]: f["action"] for f in plan["files"]} == {
        "01-first/guide.md": "write", f"_assets/images/wt-01-first-{rec['id']}-{launcher}.png": "write", f"_assets/images/{name}": "write"}
    before = _contents(folder)
    real_write = course_export.write_file

    def failing(target_folder, file_name, data):
        if file_name == "guide.md":
            raise PermissionError(13, "Access is denied")
        return real_write(target_folder, file_name, data)

    monkeypatch.setattr(course_export, "write_file", failing)
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 400 and r.json()["detail"] == message.format("demo/01-first/guide.md", "Access is denied"), r.text
    assert _contents(folder) == before, "the new picture removed, the replaced one restored, no .tmp/.keep left"
    assert len(auth_store.list_audit(action="walkthrough.export_course")) == 1, "only the export that succeeded"
    monkeypatch.setattr(course_export, "write_file", real_write)
    assert _write(c, rec["id"], plan["guide_sha256"]).status_code == 200


def test_the_guide_changed_while_the_pictures_are_written_is_refused_and_rolled_back(rig, monkeypatch):
    """K4: the guide's sha256 is checked once more just before it is replaced - somebody saved it while the pictures
    were being written: 409, their guide stands, our pictures are taken back."""
    s = _setup(rig, monkeypatch)
    c, rec, guide, folder = s["c"], s["rec"], s["guide"], s["folder"]
    config._config["courses_export"] = True
    plan = _plan(c, rec["id"])
    before = _contents(folder)
    edited = guide.read_bytes() + b"Saved in the Content Editor meanwhile.\r\n"
    real_write = course_export.write_file

    def save_midway(target_folder, file_name, data):
        wrote = real_write(target_folder, file_name, data)
        if file_name.endswith(".png") and guide.read_bytes() != edited:
            guide.write_bytes(edited)
        return wrote

    monkeypatch.setattr(course_export, "write_file", save_midway)
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 409 and r.json()["detail"] == course_export.changed_message("demo/01-first/guide.md")
    assert guide.read_bytes() == edited, "their save stands"
    assert _contents(folder) == {**before, "01-first/guide.md": hashlib.sha256(edited).hexdigest()}, "our pictures taken back"
    assert auth_store.list_audit(action="walkthrough.export_course") == []


def test_a_second_block_for_one_step_is_taken_out(rig, monkeypatch):
    """K4: a block doubled in the guide (a copy and paste in an editor) - the plan says so, the write keeps one."""
    s = _setup(rig, monkeypatch, picture=(2,), hotspots=(2,))
    c, rec, guide = s["c"], s["rec"], s["guide"]
    config._config["courses_export"] = True
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200
    once = guide.read_bytes()
    text = once.decode("utf-8")
    block = re.search(r"\r\n<!-- media-studio: walkthrough [0-9a-f]{12} step [0-9a-f]{12} -->.*?<!-- /media-studio -->", text, re.S).group(0)
    guide.write_bytes(text.replace(block, block + block, 1).encode("utf-8"))
    plan = _plan(c, rec["id"])
    assert [(rm["step_id"], rm["reason"]) for rm in plan["removed"]] == [(rec["steps"][1]["id"], "A second block for the same step.")]
    assert [p["how"] for p in plan["placed"]] == ["unchanged"]
    assert _write(c, rec["id"], plan["guide_sha256"]).status_code == 200
    assert guide.read_bytes() == once, "one block again, the guide as the first export left it"


def test_a_stale_picture_another_guide_uses_is_kept(rig, monkeypatch):
    """K5 (the Reviewer's R9): W's picture also shown by another workshop's guide of the course; W's step deleted and
    exported again - the block goes, the picture stays, and the plan says why."""
    s = _setup(rig, monkeypatch, picture=(2, 4), hotspots=(2,))
    c, rec, images, folder = s["c"], s["rec"], s["images"], s["folder"]
    config._config["courses_export"] = True
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200
    sid = rec["steps"][3]["id"]
    name = f"wt-01-first-{rec['id']}-{sid}.png"
    (folder / "02-other").mkdir()
    (folder / "02-other" / "guide.md").write_text(f'# Other\n\n<img src="../_assets/images/{name}" alt="Reused">\n', encoding="utf-8")
    rec = c.get(f"/api/walkthroughs/{rec['id']}").json()
    rec = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"],
                                                         "steps": [st for st in rec["steps"] if st["id"] != sid]}).json()
    plan = _plan(c, rec["id"])
    assert [rm["step_id"] for rm in plan["removed"]] == [sid]
    assert plan["kept"] == [{"path": f"_assets/images/{name}", "reason": "still used by 02-other"}]
    assert f"_assets/images/{name}" not in {f["path"] for f in plan["files"] if f["action"] == "remove"}
    r = _write(c, rec["id"], plan["guide_sha256"])
    assert r.status_code == 200 and r.json()["removed"] == []
    assert (images / name).is_file() and sid not in s["guide"].read_bytes().decode("utf-8")


def test_an_unreadable_other_guide_keeps_the_picture(rig, monkeypatch):
    """Recheck NIT-3: another workshop's guide that cannot be read - not UTF-8, or over the 2 MB cap - might name the
    picture, so it counts as using it: kept, and the plan says why. A readable one that names it says so instead."""
    s = _setup(rig, monkeypatch, picture=(2, 4), hotspots=(2,))
    c, rec, images, folder = s["c"], s["rec"], s["images"], s["folder"]
    config._config["courses_export"] = True
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200
    sid = rec["steps"][3]["id"]
    name = f"wt-01-first-{rec['id']}-{sid}.png"
    (folder / "02-bytes").mkdir()
    (folder / "02-bytes" / "guide.md").write_bytes(b"# Not UTF-8 \xff\xfe\n")
    rec = c.get(f"/api/walkthroughs/{rec['id']}").json()
    rec = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"],
                                                         "steps": [st for st in rec["steps"] if st["id"] != sid]}).json()
    plan = _plan(c, rec["id"])
    assert plan["kept"] == [{"path": f"_assets/images/{name}", "reason": "another guide could not be read (02-bytes)"}]
    assert f"_assets/images/{name}" not in {f["path"] for f in plan["files"] if f["action"] == "remove"}
    # Over the cap: the same.
    (folder / "02-bytes" / "guide.md").write_text("# Big\n", encoding="utf-8")
    monkeypatch.setattr(workshops, "MAX_GUIDE_BYTES", 3)
    assert course_export.used_elsewhere(folder, "01-first", [name]) == {name: "another guide could not be read (02-bytes)"}
    monkeypatch.setattr(workshops, "MAX_GUIDE_BYTES", 2 * 1024 * 1024)
    # A readable guide that names it wins the reason; one that does not, and no other, lets it go.
    (folder / "02-bytes" / "guide.md").write_text(f'<img src="../_assets/images/{name}" alt="x">\n', encoding="utf-8")
    assert course_export.used_elsewhere(folder, "01-first", [name]) == {name: "still used by 02-bytes"}
    (folder / "02-bytes" / "guide.md").write_text("# Nothing of it\n", encoding="utf-8")
    assert course_export.used_elsewhere(folder, "01-first", [name]) == {}
    r = _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"])
    assert r.status_code == 200 and r.json()["removed"] == [f"_assets/images/{name}"] and not (images / name).exists()


def test_the_debris_of_an_interrupted_export_is_listed_and_swept(rig, monkeypatch):
    """Recheck NIT-1: what a process killed mid-export leaves in the folders an export writes - its temporary files,
    its backups, a write probe, and a picture of THIS walkthrough no guide names any more - is listed in the plan and
    swept by the next write. Anything else stays: another walkthrough's picture, a dot file of the author's."""
    s = _setup(rig, monkeypatch, picture=(2,), hotspots=(2,))
    c, rec, folder = s["c"], s["rec"], s["folder"]
    config._config["courses_export"] = True
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200
    wid = rec["id"]
    debris = {
        "01-first/.guide.md.1234abcd.keep": "left by an interrupted export",
        "01-first/.ms-write-check-deadbeef": "left by an interrupted export",
        "_assets/images/.wt-01-first-x.png.89abcdef.bak": "left by an interrupted export",
        "_assets/images/.authored.png.0011aabb.tmp": "left by an interrupted export",
        f"_assets/images/wt-01-first-{wid}-0123456789ab.png": "a picture of this walkthrough that no guide names",
    }
    for rel in debris:
        (folder / rel).write_bytes(b"debris")
    stays = ["_assets/images/.gitkeep", f"_assets/images/wt-01-first-aaaaaaaaaaaa-0123456789ab.png", "01-first/.notes.1234abcd.txt"]
    for rel in stays:
        (folder / rel).write_bytes(b"theirs")
    plan = _plan(c, wid)
    assert sorted((d["path"], d["reason"]) for d in plan["debris"]) == sorted(debris.items())
    assert {f["path"] for f in plan["files"] if f["action"] == "remove"} == set(debris) and plan["changed"] is True
    assert {f["path"] for f in plan["files"] if f["action"] == "write"} == set(), "only the sweep to do"
    r = _write(c, wid, plan["guide_sha256"])
    assert r.status_code == 200 and sorted(r.json()["removed"]) == sorted(debris)
    assert not any((folder / rel).exists() for rel in debris) and all((folder / rel).exists() for rel in stays)
    again = _plan(c, wid)
    assert again["debris"] == [] and again["changed"] is False


def test_a_nested_items_figure_stays_inside_its_item(rig, monkeypatch):
    """K8 n3: a step of a nested list item (indented 1-3 spaces) gets its block indented to the item's content
    column, so it stays inside the item; the parse, a second export and the removal are as for any block."""
    guide = "# Nested\n\n## 1. Nested\n\n1. Parent step.\n   1. Child one.\n   2. Child two.\n2. Next step.\n"
    s = _setup(rig, monkeypatch, guide, picture=(2,), hotspots=(2,))
    c, rec, path = s["c"], s["rec"], s["guide"]
    assert [st["text"] for st in rec["steps"]] == ["Parent step.", "Child one.", "Child two.", "Next step."]
    config._config["courses_export"] = True
    original = path.read_bytes()
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200
    lines = path.read_bytes().decode("utf-8").split("\r\n")
    at = next(i for i, line in enumerate(lines) if "media-studio: walkthrough" in line)
    end = next(i for i, line in enumerate(lines) if "/media-studio" in line)
    assert lines[at - 2] == "   1. Child one." and lines[end + 1] == "   2. Child two."
    assert all(line.startswith("      ") and not line.startswith("       ") for line in lines[at:end + 1] if line), "the content column: 3 + '1.' + 1"
    again = workshops.parse_guide("\n".join(lines), {})
    assert [st.text for st in again.steps] == ["Parent step.", "Child one.", "Child two.", "Next step."]
    assert _plan(c, rec["id"])["changed"] is False, "rewritten in place at its own indentation"
    rec = c.get(f"/api/walkthroughs/{rec['id']}").json()
    rec = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"],
                                                         "steps": [st for st in rec["steps"] if st["id"] != rec["steps"][1]["id"]]}).json()
    assert _write(c, rec["id"], _plan(c, rec["id"])["guide_sha256"]).status_code == 200
    assert path.read_bytes() == original, "taken out byte for byte"
    assert course_export.item_indent("   1. Child") == "      " and course_export.item_indent("1. Top") == ""
    assert course_export.item_indent("  12)  Two spaces") == " " * 7 and course_export.item_indent("    1. Code") == ""


# -- C6 / C7: the zips and the player ---------------------------------------------------------------------------

def test_the_course_zip_is_laid_out_like_the_course(rig, monkeypatch, tmp_path):
    s = _setup(rig, monkeypatch)
    c, rec = s["c"], s["rec"]
    # K6: every zip is spooled to a temporary file, streamed, and removed once it is sent.
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(course_export, "ZIP_TEMP_DIR", spool)
    plan = _plan(c, rec["id"])
    r = c.get(f"/api/walkthroughs/{rec['id']}/export/course.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["content-disposition"] == 'attachment; filename="demo-01-first-export.zip"'
    assert list(spool.iterdir()) == [], "the zip's temporary file is removed once it is sent"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == sorted(["01-first/guide.md"] + [f"_assets/images/{p['image']}" for p in plan["placed"]])
    assert zf.read("01-first/guide.md") == plan_bytes(plan, s["guide"].read_bytes())
    for p in plan["placed"]:
        assert zf.read(f"_assets/images/{p['image']}") == (rig["tmp"] / "walkthroughs" / rec["id"] / f"step-{p['step_id']}.png").read_bytes()
    # A PNG is stored as it is (it is compressed already); the guide is deflated.
    assert {i.filename: i.compress_type for i in zf.infolist()} == {
        name: (zipfile.ZIP_STORED if name.endswith(".png") else zipfile.ZIP_DEFLATED) for name in names}
    assert c.get(f"/api/walkthroughs/{rec['id']}/export/course.zip").content == r.content, "the same export, the same zip"
    _needs_player()
    with_player = zipfile.ZipFile(io.BytesIO(c.get(f"/api/walkthroughs/{rec['id']}/export/course.zip?player=1").content))
    assert "01-first/walkthrough/index.html" in with_player.namelist() and "01-first/walkthrough/walkthrough.json" in with_player.namelist()
    assert with_player.getinfo("01-first/walkthrough/index.html").compress_type == zipfile.ZIP_DEFLATED
    player = zipfile.ZipFile(io.BytesIO(c.get(f"/api/walkthroughs/{rec['id']}/export/player.zip").content))
    assert all(i.compress_type == zipfile.ZIP_STORED for i in player.infolist() if i.filename.endswith(".png"))
    assert list(spool.iterdir()) == [], "every zip's temporary file is gone"
    # A zip that fails while it is written leaves no temporary file either.
    with pytest.raises(OSError):
        course_export._zip({"a.png": spool / "no-such-file.png"})
    assert list(spool.iterdir()) == []


def _needs_player():
    """The player bundle is part of the built UI (``frontend/dist`` is committed): its absence is a failure, never a
    skip (K7) - ``tests/test_frontend_dist.py`` says so too."""
    assert course_export.PLAYER_TEMPLATE.is_file(), "frontend/dist/walkthrough-player.html is missing: run npm run build and commit frontend/dist"


def test_the_player_bundle_opens_offline_and_plays_the_steps(rig, monkeypatch, tmp_path):
    _needs_player()
    node = shutil.which("node")
    if node is None or not (REPO_ROOT / "frontend" / "node_modules" / "jsdom").is_dir():
        pytest.skip("node and the frontend's jsdom are needed to open the page")
    s = _setup(rig, monkeypatch, picture=(1, 2), hotspots=(1,))
    c, rec = s["c"], s["rec"]
    r = c.get(f"/api/walkthroughs/{rec['id']}/export/player.zip")
    assert r.status_code == 200 and r.headers["content-disposition"] == 'attachment; filename="First-player.zip"'
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    # Every step with a picture: the two captured and the guide's own (step 3); steps 4 and 5 have none.
    pictured = [st["id"] for st in rec["steps"] if st["image"]]
    assert len(pictured) == 3
    assert sorted(zf.namelist()) == sorted(["walkthrough/index.html", "walkthrough/walkthrough.json"] + [f"walkthrough/step-{sid}.png" for sid in pictured])
    out = tmp_path / "unzipped"
    zf.extractall(out)
    folder = out / "walkthrough"
    page = (folder / "index.html").read_text(encoding="utf-8")
    # No network: no script or stylesheet by address, no import, no font fetched.
    assert not re.search(r"<script[^>]+src=|<link\b|@import|url\((?!\"?data:)", page), "everything inline"
    assert re.search(r"<style>[^<]*\.ms-player\{", page), "the player's styles are inline in the page"
    data = json.loads((folder / "walkthrough.json").read_text(encoding="utf-8"))
    assert set(data) == {"version", "name", "steps"} and "owner" not in json.dumps(data) and "capture_id" not in json.dumps(data)
    assert "note" not in data["steps"][0] and data["steps"][3]["image"] is None and data["steps"][0]["image"] == f"step-{pictured[0]}.png"
    run = subprocess.run([node, str(PLAYER_CHECK), str(folder)], capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout.strip().splitlines()[-1])
    assert seen["unreadable"] is False and seen["title"] == "First"
    assert seen["requests"] == [], "nothing fetched"
    assert seen["errors"] == [], seen["errors"]
    first, second = seen["steps"]
    assert first["progress"] == "Step 1 of 5" and second["progress"] == "Step 2 of 5", "Next plays the next step"
    assert first["picture"] == (folder / f"step-{pictured[0]}.png").as_uri() and (folder / f"step-{pictured[0]}.png").is_file()
    assert first["shown"] == rec["steps"][0]["text"], "a hotspot step: its tooltip"
    assert second["picture"] == (folder / f"step-{pictured[1]}.png").as_uri() and second["shown"].startswith(rec["steps"][1]["text"]), "no hotspot: a card"
    # The data slot holds the walkthrough with "<" escaped: no text can end the script.
    nasty = course_export.player_html(course_export.PLAYER_TEMPLATE.read_text(encoding="utf-8"), {"name": "</script><b>x", "steps": []})
    assert "</script><b>" not in nasty and "<title>&lt;/script&gt;&lt;b&gt;x</title>" in nasty


def test_the_player_written_beside_the_guide_with_its_link(rig, monkeypatch):
    _needs_player()
    s = _setup(rig, monkeypatch)
    c, rec = s["c"], s["rec"]
    config._config["courses_export"] = True
    plan = _plan(c, rec["id"], player=True)
    r = _write(c, rec["id"], plan["guide_sha256"], player=True)
    assert r.status_code == 200 and r.json()["player"] is True
    folder = s["folder"] / "01-first" / "walkthrough"
    pictured = [st["id"] for st in rec["steps"] if st["image"]]
    assert sorted(p.name for p in folder.iterdir()) == sorted(["index.html", "walkthrough.json"] + [f"step-{sid}.png" for sid in pictured])
    text = s["guide"].read_bytes().decode("utf-8")
    # B3: the guide has no placeholder and no video figure, so the figure follows the first section heading.
    assert plan["player_block"] == {"how": "heading", "line": 5, "heading": "## 1. Open it"}
    assert '<figure class="pcm-walkthrough">' in text and '<iframe src="walkthrough/index.html"' in text
    assert text.count(f"<!-- media-studio: walkthrough {rec['id']} player -->") == 1
    assert auth_store.list_audit(action="walkthrough.export_course")[0]["detail"] == "demo/01-first: 3 figures, 3 images, player"
    # Again: nothing changes; the files list says so.
    second = _plan(c, rec["id"], player=True)
    assert second["changed"] is False
    # The lab's own files/ folder is never touched; a step picture the walkthrough no longer has leaves the folder.
    (s["folder"] / "01-first" / "files").mkdir()
    (s["folder"] / "01-first" / "files" / "win.ktr").write_text("<transformation/>", encoding="utf-8")
    rec = c.get(f"/api/walkthroughs/{rec['id']}").json()
    gone = rec["steps"][4]["id"]
    rec = c.put(f"/api/walkthroughs/{rec['id']}", json={"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": rec["steps"][:4]}).json()
    third = _plan(c, rec["id"], player=True)
    assert {"path": f"01-first/walkthrough/step-{gone}.png", "action": "remove"} in third["files"]
    assert _write(c, rec["id"], third["guide_sha256"], player=True).status_code == 200
    assert not (folder / f"step-{gone}.png").exists() and (s["folder"] / "01-first" / "files" / "win.ktr").is_file()
    # Without the player, an export leaves the player's link and folder as they are.
    fourth = _plan(c, rec["id"])
    assert fourth["changed"] is False and (folder / "index.html").is_file()


# -- B3 (the embedded player): the figure the player is embedded by -------------------------------------------

#: The course standard's two placeholders, in the template's form (blank lines around each element), and a video
#: figure of the Vimeo round's.
VIDEO_PLACEHOLDER = '<figure>\n\n<figcaption class="pcm-video-caption">Walkthrough: coming soon</figcaption>\n\n</figure>\n'
INTERACTIVE_PLACEHOLDER = '<figure class="pcm-walkthrough">\n\n<figcaption>Interactive walkthrough: coming soon</figcaption>\n\n</figure>\n'
VIDEO_FIGURE = ('<figure>\n\n![Walkthrough](https://vimeo.com/123456789/abcdef12)\n\n'
                '<figcaption class="pcm-video-caption">Watch: the tour</figcaption>\n\n</figure>\n')
TAIL = "Click where the beacon pulses; right-click or double-click when the tooltip says so."


def _with_media(*blocks: str) -> str:
    """GUIDE with ``blocks`` after its banner, a blank line between and after, as the template places them."""
    return GUIDE.replace("> **Warning:** Intro.\n\n", "> **Warning:** Intro.\n\n" + "\n".join(blocks) + "\n", 1)


def _figure(name: str, first: str | None = None) -> list[str]:
    """The walkthrough figure, line by line (the contract with the content manager)."""
    first = first or f"Walkthrough: {name}."
    return ['<figure class="pcm-walkthrough">', "",
            f'<iframe src="walkthrough/index.html" title="Interactive walkthrough: {name}" loading="lazy"></iframe>', "",
            f"<figcaption>{first} {TAIL}</figcaption>", "", "</figure>"]


def _block(wid: str, name: str, first: str | None = None) -> str:
    """The figure between its marker pair, as the guide holds it (no final newline)."""
    return "\n".join([f"<!-- media-studio: walkthrough {wid} player -->", ""] + _figure(name, first) + ["", "<!-- /media-studio -->"])


def _eol(text: str, crlf: bool) -> bytes:
    return (_crlf(text) if crlf else text).encode("utf-8")


@pytest.mark.parametrize("crlf", [True, False])
def test_the_player_figure_replaces_the_interactive_placeholder_and_leaves_the_video_placeholder(rig, monkeypatch, crlf):
    _needs_player()
    s = _setup(rig, monkeypatch, _with_media(VIDEO_PLACEHOLDER, INTERACTIVE_PLACEHOLDER), crlf=crlf)
    c, rec = s["c"], s["rec"]
    wid, name = rec["id"], rec["name"]
    config._config["courses_export"] = True
    plan = _plan(c, wid, player=True)
    assert plan["player_block"] == {"how": "placeholder", "line": 11, "heading": ""}
    assert plan["player_caption"] == f"Walkthrough: {name}"
    assert plan["player_lines"] == _figure(name)
    # The diff: the placeholder's caption goes, the frame comes (the figure's opening line stands in both, so it is context).
    assert '+<iframe src="walkthrough/index.html"' in plan["diff"] and "-<figcaption>Interactive walkthrough: coming soon</figcaption>" in plan["diff"]
    assert _write(c, wid, plan["guide_sha256"], player=True).status_code == 200
    raw = s["guide"].read_bytes()
    assert _without_step_blocks(raw.decode("utf-8")).encode("utf-8") == _eol(_with_media(VIDEO_PLACEHOLDER, _block(wid, name) + "\n"), crlf)
    assert (raw.count(b"\r\n") == raw.count(b"\n")) is crlf
    # Idempotent: the same export again changes nothing, and the plan says the figure is unchanged where it is.
    again = _plan(c, wid, player=True)
    assert again["changed"] is False and again["player_block"] == {"how": "unchanged", "line": 11, "heading": ""}
    # The plan without a caption reads the one in the guide; a caption sent is the one written.
    assert again["player_caption"] == f"Walkthrough: {name}"


@pytest.mark.parametrize("crlf", [True, False])
@pytest.mark.parametrize("video", [VIDEO_PLACEHOLDER, VIDEO_FIGURE])
def test_the_player_figure_goes_below_the_video_figure_when_the_guide_has_no_interactive_placeholder(rig, monkeypatch, crlf, video):
    _needs_player()
    s = _setup(rig, monkeypatch, _with_media(video), crlf=crlf)
    c, rec = s["c"], s["rec"]
    wid, name = rec["id"], rec["name"]
    config._config["courses_export"] = True
    plan = _plan(c, wid, player=True)
    assert plan["player_block"] == {"how": "video", "line": 5, "heading": ""}
    assert _write(c, wid, plan["guide_sha256"], player=True).status_code == 200
    text = _without_step_blocks(s["guide"].read_bytes().decode("utf-8"))
    assert text.encode("utf-8") == _eol(_with_media(video + "\n" + _block(wid, name) + "\n"), crlf)
    assert _plan(c, wid, player=True)["changed"] is False


@pytest.mark.parametrize("crlf", [True, False])
def test_the_player_figure_follows_the_first_section_heading_when_the_guide_has_neither(rig, monkeypatch, crlf):
    _needs_player()
    s = _setup(rig, monkeypatch, crlf=crlf)
    c, rec = s["c"], s["rec"]
    wid, name = rec["id"], rec["name"]
    config._config["courses_export"] = True
    plan = _plan(c, wid, player=True)
    assert plan["player_block"] == {"how": "heading", "line": 5, "heading": "## 1. Open it"}
    assert _write(c, wid, plan["guide_sha256"], player=True).status_code == 200
    text = _without_step_blocks(s["guide"].read_bytes().decode("utf-8"))
    assert text.encode("utf-8") == _eol(GUIDE.replace("## 1. Open it\n\n", "## 1. Open it\n\n" + _block(wid, name) + "\n\n", 1), crlf)
    assert _plan(c, wid, player=True)["changed"] is False


def test_an_older_exports_pointer_line_is_replaced_by_the_figure_where_it_stands(rig, monkeypatch):
    """A guide exported before this release holds the one-line pointer between the player markers at the very end: a
    re-export rewrites that block in place - the figure where the pointer was - and the next one changes nothing."""
    _needs_player()
    s = _setup(rig, monkeypatch, guide=GUIDE)
    c, rec = s["c"], s["rec"]
    wid, name = rec["id"], rec["name"]
    pointer = (f"<!-- media-studio: walkthrough {wid} player -->\n\n**Interactive walkthrough:** open "
               "[walkthrough/index.html](walkthrough/index.html), in this workshop's folder, in a web browser. It runs offline.\n\n"
               "<!-- /media-studio -->\n")
    s["guide"].write_bytes(_crlf(GUIDE + "\n" + pointer).encode("utf-8"))
    config._config["courses_export"] = True
    plan = _plan(c, wid, player=True)
    assert plan["player_block"] == {"how": "replaced", "line": GUIDE.count("\n") + 2, "heading": ""}
    assert "-**Interactive walkthrough:** open [walkthrough/index.html]" in plan["diff"]
    assert _write(c, wid, plan["guide_sha256"], player=True).status_code == 200
    text = _without_step_blocks(s["guide"].read_bytes().decode("utf-8"))
    assert text.encode("utf-8") == _crlf(GUIDE + "\n" + _block(wid, name) + "\n").encode("utf-8")
    assert "[walkthrough/index.html](walkthrough/index.html)" not in text
    assert _plan(c, wid, player=True)["changed"] is False


def test_the_figures_caption_defaults_to_the_name_is_edited_in_the_dialog_and_the_guides_own_wins_a_re_export(rig, monkeypatch):
    _needs_player()
    s = _setup(rig, monkeypatch, _with_media(INTERACTIVE_PLACEHOLDER))
    c, rec = s["c"], s["rec"]
    wid = rec["id"]
    config._config["courses_export"] = True
    # A caption typed: the first sentence, its full stop added; the plan echoes it as typed.
    plan = _plan(c, wid, player=True, caption="  Walkthrough:   the first win ")
    assert plan["player_caption"] == "Walkthrough: the first win"
    assert plan["player_lines"][4] == f"<figcaption>Walkthrough: the first win. {TAIL}</figcaption>"
    assert _write(c, wid, plan["guide_sha256"], player=True, caption="Walkthrough: the first win").status_code == 200
    text = s["guide"].read_bytes().decode("utf-8")
    assert f"<figcaption>Walkthrough: the first win. {TAIL}</figcaption>" in text
    # A re-export without a caption keeps the guide's own; the plan shows it for the dialog to edit.
    again = _plan(c, wid, player=True)
    assert again["player_caption"] == "Walkthrough: the first win" and again["changed"] is False
    # One that ends its own sentence keeps that; an empty one is the default again.
    bang = _plan(c, wid, player=True, caption="Watch the clicks!")
    assert bang["player_lines"][4] == f"<figcaption>Watch the clicks! {TAIL}</figcaption>" and bang["changed"] is True
    assert _plan(c, wid, player=True, caption="")["player_caption"] == "Walkthrough: the first win", "an empty caption is no caption: the guide's own stands"
    # HTML in the name or the caption is escaped in the title and the caption; a 200-character cap on the caption.
    rec = c.put(f"/api/walkthroughs/{wid}", json={"version": 1, "name": "A <b>bold</b> & brave", "revision": rec["revision"], "steps": rec["steps"]}).json()
    nasty = _plan(c, wid, player=True, caption="Tags <i>here</i> & there")
    assert nasty["player_lines"][2] == '<iframe src="walkthrough/index.html" title="Interactive walkthrough: A &lt;b&gt;bold&lt;/b&gt; &amp; brave" loading="lazy"></iframe>'
    assert nasty["player_lines"][4] == f"<figcaption>Tags &lt;i&gt;here&lt;/i&gt; &amp; there. {TAIL}</figcaption>"
    assert c.post(f"/api/walkthroughs/{wid}/export/course/plan", json={"player": True, "caption": "x" * 201}).status_code == 422
    assert c.post(f"/api/walkthroughs/{wid}/export/course", json={"guide_sha256": "0" * 64, "player": True, "caption": "x" * 201}).status_code == 422
    # The zip with the player carries the caption too; a caption without the player is ignored.
    r = c.get(f"/api/walkthroughs/{wid}/export/course.zip?player=1&caption=Zipped")
    assert r.status_code == 200
    guide = zipfile.ZipFile(io.BytesIO(r.content)).read("01-first/guide.md").decode("utf-8")
    assert f"<figcaption>Zipped. {TAIL}</figcaption>" in guide
    assert c.get(f"/api/walkthroughs/{wid}/export/course.zip?caption=" + "x" * 201).status_code == 422
    plain = _plan(c, wid, player=False, caption="Ignored")
    assert plain["player_caption"] == "" and plain["player_lines"] == [] and plain["player_block"] is None


def test_the_pure_figure_rules_find_the_placeholders_and_the_figures_outside_fences():
    wid, name = "a" * 12, "Tour"
    plain = _with_media(VIDEO_PLACEHOLDER, INTERACTIVE_PLACEHOLDER).split("\n")
    assert course_export.fenced_lines(["a", "```", "b", "```", "c"]) == [False, True, True, True, False]
    video = course_export.find_video_figure(plain)
    assert video is not None and plain[video.start] == "<figure>" and plain[video.end] == "</figure>" and (video.start, video.end) == (4, 8)
    walk = course_export.find_walkthrough_figure(plain)
    assert walk is not None and (walk.start, walk.end) == (10, 14)
    assert course_export.find_walkthrough_placeholder(plain) == walk
    assert course_export.walkthrough_figure_anchor(plain) == 10
    # The filled figure is a walkthrough figure, not a placeholder; the video figure of the Vimeo round is found.
    filled = _with_media(VIDEO_FIGURE, "\n".join(_figure(name)) + "\n").split("\n")
    assert course_export.find_walkthrough_placeholder(filled) is None
    assert course_export.find_walkthrough_figure(filled) is not None and filled[course_export.find_walkthrough_figure(filled).start].startswith("<figure class")
    assert course_export.find_video_figure(filled) is not None and filled[course_export.find_video_figure(filled).end] == "</figure>"
    # Wrapped in our markers, the anchor is the opening marker - a video figure never goes inside the block.
    wrapped = GUIDE.replace("## 1. Open it\n\n", "## 1. Open it\n\n" + _block(wid, name) + "\n\n", 1).split("\n")
    assert wrapped[6].startswith("<!-- media-studio: walkthrough") and course_export.walkthrough_figure_anchor(wrapped) == 6
    # Inside a fence nothing counts; a figure without its closing tag is none; a guide without any is None.
    fenced = ["```html", *INTERACTIVE_PLACEHOLDER.split("\n"), "```"]
    assert course_export.find_walkthrough_figure(fenced) is None and course_export.find_video_figure(["```", *VIDEO_PLACEHOLDER.split("\n"), "```"]) is None
    assert course_export.find_walkthrough_figure(['<figure class="pcm-walkthrough">', "", "<figcaption>x</figcaption>", "", "## Next"]) is None
    assert course_export.walkthrough_figure_anchor(GUIDE.split("\n")) is None and course_export.find_video_figure(GUIDE.split("\n")) is None
    # The first section heading, else the title, is the export's and the Vimeo round's one rule.
    assert course_export.first_section_heading(GUIDE.split("\n")) == 4
    assert course_export.first_section_heading(["# Title", "", "text"]) == 0 and course_export.first_section_heading(["text"]) is None
    # The figure's text: the default caption, a typed one with its full stop, one ending its own sentence, the escaping.
    assert course_export.player_caption_text("Tour", "") == f"Walkthrough: Tour. {TAIL}"
    assert course_export.player_caption_text("Tour", " the  first win ") == f"the first win. {TAIL}"
    assert course_export.player_caption_text("Tour", "Go!") == f"Go! {TAIL}"
    assert course_export.player_caption_text("Tour", "Why?") == f"Why? {TAIL}"
    assert course_export.player_caption_text("Tour", "Walkthrough: v1.2.") == f"Walkthrough: v1.2. {TAIL}"
    assert course_export.existing_player_caption(_block(wid, name, "Walkthrough: the first win.").split("\n")) == "Walkthrough: the first win"
    assert course_export.existing_player_caption(_block(wid, name, "Go!").split("\n")) == "Go!"
    assert course_export.existing_player_caption(_block(wid, name, "Tags &lt;i&gt; &amp; there.").split("\n")) == "Tags <i> & there"
    assert course_export.existing_player_caption(["<!-- media-studio: walkthrough aaaaaaaaaaaa player -->", "", "**Interactive walkthrough:** open x", "", "<!-- /media-studio -->"]) is None
    assert course_export.player_figure_lines("Tour", "") == _figure(name)


def test_the_vimeo_rounds_video_figure_goes_above_the_walkthrough_figure_never_inside_or_below_it():
    """V3 learns ``figure.pcm-walkthrough``: with no video placeholder and no video figure of ours, the insertion goes
    ABOVE the walkthrough figure - above our opening marker when the markers wrap it, above the placeholder figure
    when the guide has only that - and it never takes the walkthrough figure for the video one."""
    from services import course_video

    wid, name, link = "a" * 12, "Tour", "https://vimeo.com/123456789/abcdef12"
    video = "\n".join(course_video.figure_block(link, "the tour"))
    guide = GUIDE.replace("## 1. Open it\n\n", "## 1. Open it\n\n" + _block(wid, name) + "\n\n", 1)
    plain = guide.split("\n")
    assert course_video.find_placeholder(plain) is None and course_video.find_figure(plain) is None
    for crlf in (False, True):
        text = _crlf(guide) if crlf else guide
        edit = course_video.plan_guide(text, link, "the tour", insert=True)
        assert (edit.how, edit.above_walkthrough, edit.line, edit.heading) == ("insert", True, 7, "")
        expected = GUIDE.replace("## 1. Open it\n\n", "## 1. Open it\n\n" + video + "\n\n" + _block(wid, name) + "\n\n", 1)
        assert edit.new_text == (_crlf(expected) if crlf else expected)
        # The export's block is untouched: the video figure stands before its opening marker.
        assert edit.new_text.index(_crlf(video) if crlf else video) < edit.new_text.index(f"<!-- media-studio: walkthrough {wid} player -->")
    # Not asked to insert: the plan says so, and where it would go.
    none = course_video.plan_guide(guide, link, "the tour", insert=False)
    assert (none.how, none.above_walkthrough, none.line, none.changed) == ("none", True, 7, False)
    # The interactive placeholder alone (no video placeholder): above it.
    only = _with_media(INTERACTIVE_PLACEHOLDER)
    edit = course_video.plan_guide(only, link, "the tour", insert=True)
    assert (edit.how, edit.above_walkthrough, edit.line) == ("insert", True, 5)
    assert edit.new_text == _with_media(video + "\n", INTERACTIVE_PLACEHOLDER)
    # Both placeholders: the video one is taken, as before; the interactive one is not mistaken for it.
    both = _with_media(VIDEO_PLACEHOLDER, INTERACTIVE_PLACEHOLDER)
    edit = course_video.plan_guide(both, link, "the tour", insert=False)
    assert (edit.how, edit.above_walkthrough) == ("placeholder", False)
    assert edit.new_text == _with_media(video + "\n", INTERACTIVE_PLACEHOLDER)
    # A guide with neither figure: the heading rule as before, and no walkthrough flag.
    bare = course_video.plan_guide(GUIDE, link, "the tour", insert=True)
    assert (bare.how, bare.above_walkthrough, bare.heading, bare.line) == ("insert", False, "## 1. Open it", 5)


def test_the_real_first_win_exports_and_still_parses_the_same(lab, rig, monkeypatch):
    """The course the parser was designed against, COPIED: imported, every step without a picture captured, the
    export written into the copy - every captured step placed after its own item, the parse unchanged."""
    shutil.copytree(lab / LAB, rig["courses"] / LAB)
    c = rig["admin"]
    rec = c.post("/api/walkthroughs/import", json={"course": LAB, "slug": "01-first-win"}).json()
    for step in rec["steps"]:
        if not step["image"]:
            cid = _still(rig, monkeypatch, 1600, 900)
            rec = c.post(f"/api/walkthroughs/{rec['id']}/steps/{step['id']}/picture", json={"capture_id": cid, "hotspot": HOTSPOT}).json()
    guide = rig["courses"] / LAB / "01-first-win" / "guide.md"
    original = guide.read_bytes()
    plan = _plan(c, rec["id"])
    assert len(plan["placed"]) == 8 and plan["not_placed"] == [] and len(plan["skipped"]) == 2
    config._config["courses_export"] = True
    assert _write(c, rec["id"], plan["guide_sha256"]).status_code == 200
    text = guide.read_bytes().decode("utf-8")
    assert guide.read_bytes().count(b"\r\n") == guide.read_bytes().count(b"\n"), "the guide stays CRLF"
    assert _without_blocks(text) == original.decode("utf-8")
    before = workshops.parse_guide(original.decode("utf-8"), {"spoon": "Pentaho Data Integration"})
    after = workshops.parse_guide(text, {"spoon": "Pentaho Data Integration"})
    assert [(st.text, st.instruction, st.chapter, st.note, st.key, st.ordinal) for st in after.steps] == [
        (st.text, st.instruction, st.chapter, st.note, st.key, st.ordinal) for st in before.steps]
    # Each figure sits inside its own step's span - after the step's own lines - and is the step's picture now.
    lines = workshops.guide_lines(text)
    for st in after.steps:
        marker = next((i for i in range(st.start_line, st.end_line) if lines[i].startswith("<!-- media-studio:")), None)
        assert (marker is not None) == ("_assets/images/wt-" in (st.image or "")), st.text
    assert _plan(c, rec["id"])["changed"] is False


# -- W2e (E4): several workshops - a plan per guide, one write under one journal, one zip ---------------------------

TWO_SUMMARY = "# T\n\n## Part\n\n* [First](01-first/guide.md)\n* [Second](02-second/guide.md)\n"
SECOND_GUIDE = (
    "# Second workshop\n\n"
    "## 1. Build\n\n"
    "1. Drag **Text file input** onto the canvas.\n"
    "2. Press **Run**.\n\n"
    "## Lab Files\n\n"
    "[x.ktr](./files/x.ktr)\n"
)
EXPORT_BODY = "/api/walkthroughs/{wid}/export/course"


def _setup_two(rig, monkeypatch, *, picture=(2, 5, 6, 7), hotspots=(2, 6), who="admin") -> dict:
    """The demo course with TWO workshops - ``GUIDE`` as 01-first (five steps, the third with the guide's own picture)
    and ``SECOND_GUIDE`` as 02-second (two steps) - imported together, named in the OTHER order (the course's order
    rules), CRLF as the content manager's guides are; the steps numbered in ``picture`` (over the whole walkthrough)
    given a still of 400 x 300, a hotspot for those in ``hotspots``."""
    folder = _course(rig["courses"], "demo", summary=TWO_SUMMARY)
    _workshop(folder, "01-first", "", images={"authored.png": _png((40, 30), (0, 128, 0))})
    (folder / "01-first" / "guide.md").write_bytes(_crlf(GUIDE).encode("utf-8"))
    _workshop(folder, "02-second", "")
    (folder / "02-second" / "guide.md").write_bytes(_crlf(SECOND_GUIDE).encode("utf-8"))
    c = rig[who]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "workshops": [{"slug": "02-second"}, {"slug": "01-first"}]})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert [st["source"]["slug"] for st in rec["steps"]] == ["01-first"] * 5 + ["02-second"] * 2, "the course's order"
    for number in picture:
        sid = rec["steps"][number - 1]["id"]
        cid = _still(rig, monkeypatch, 400, 300, who=who)
        r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json={"capture_id": cid, "hotspot": HOTSPOT if number in hotspots else None})
        assert r.status_code == 200, r.text
        rec = r.json()
    guides = {slug: folder / slug / "guide.md" for slug in ("01-first", "02-second")}
    return {"rec": rec, "folder": folder, "guides": guides, "images": folder / "_assets" / "images", "c": c}


def _write_guides(c, wid, plan, player=False):
    """The write with every guide's sha256 from the plan (E4's body)."""
    body = {"guides": [{"slug": g["slug"], "sha256": g["guide_sha256"]} for g in plan["guides"]], "player": player}
    return c.post(EXPORT_BODY.format(wid=wid), json=body)


def test_the_plan_is_per_guide_and_each_step_goes_into_its_own_workshop(rig, monkeypatch):
    """E4: one guide read, parsed and planned per workshop of the record; the steps grouped by their own slug; the
    figures, the skipped, the diff per guide; the pictures in the one ``_assets/images``, each named by its slug; the
    flat lists (and the one-guide fields) kept for a page before this; nothing written."""
    s = _setup_two(rig, monkeypatch)
    c, rec = s["c"], s["rec"]
    wid, steps = rec["id"], rec["steps"]
    before = _snapshot(s["folder"])
    plan = _plan(c, wid)
    assert _snapshot(s["folder"]) == before, "the plan writes nothing"
    first, second = plan["guides"]
    assert (first["slug"], first["title"], first["guide"]) == ("01-first", "First", "demo/01-first/guide.md")
    assert (second["slug"], second["title"], second["guide"]) == ("02-second", "Second", "demo/02-second/guide.md")
    assert first["guide_sha256"] == hashlib.sha256(s["guides"]["01-first"].read_bytes()).hexdigest()
    assert second["guide_sha256"] == hashlib.sha256(s["guides"]["02-second"].read_bytes()).hexdigest()
    assert [(p["number"], p["text"], p["image"], p["hotspot"], p["how"]) for p in first["placed"]] == [
        (2, "Right-click Valid rows and select Preview.", f"wt-01-first-{wid}-{steps[1]['id']}.png", {"x": "45.0", "y": "23.3"}, "new"),
        (5, "Select Cancel.", f"wt-01-first-{wid}-{steps[4]['id']}.png", None, "new"),
    ]
    assert [(p["number"], p["text"], p["image"], p["hotspot"], p["how"]) for p in second["placed"]] == [
        (6, "Drag Text file input onto the canvas.", f"wt-02-second-{wid}-{steps[5]['id']}.png", {"x": "45.0", "y": "23.3"}, "new"),
        (7, "Press Run.", f"wt-02-second-{wid}-{steps[6]['id']}.png", None, "new"),
    ]
    assert [(k["number"], k["reason"]) for k in first["skipped"]] == [
        (1, course_export.NO_PICTURE_REASON), (3, course_export.GUIDE_PICTURE_REASON), (4, course_export.NO_PICTURE_REASON)]
    assert second["skipped"] == [] and first["not_placed"] == [] and second["not_placed"] == []
    assert (first["figures"], second["figures"], plan["figures"]) == (2, 2, 4)
    assert plan["placed"] == first["placed"] + second["placed"] and plan["skipped"] == first["skipped"] and plan["not_placed"] == []
    assert plan["workshop"] == {"slug": "01-first", "title": "First"} and plan["guide"] == "demo/01-first/guide.md"
    assert plan["guide_sha256"] == first["guide_sha256"]
    assert first["diff"].startswith("--- a/01-first/guide.md\n+++ b/01-first/guide.md\n@@ ")
    assert second["diff"].startswith("--- a/02-second/guide.md\n+++ b/02-second/guide.md\n@@ ")
    assert plan["diff"] == first["diff"] + "\n" + second["diff"]
    assert first["diff"].count("+<!-- media-studio: walkthrough") == 2 and second["diff"].count("+<!-- media-studio: walkthrough") == 2
    assert "+<figcaption>Second · 1. Build</figcaption>" in second["diff"], "the caption is the step's chapter, named by its workshop"
    assert {f["path"]: f["action"] for f in plan["files"]} == {
        "01-first/guide.md": "write", "02-second/guide.md": "write",
        **{f"_assets/images/{p['image']}": "write" for p in plan["placed"]},
    }
    assert plan["changed"] is True and plan["kept"] == [] and plan["debris"] == [] and plan["allowed"] is False


def test_the_write_puts_each_guide_in_place_under_one_journal_and_the_zip_holds_every_guide(rig, monkeypatch):
    """E4: ``guides: [{slug, sha256}]`` writes every guide (CRLF kept, each step's figure in ITS guide), one audit row
    naming the guides; a second export writes nothing; the old ``sha256`` alone is refused for a record of several
    workshops; the zip holds every guide and the pictures; the player goes into the FIRST workshop's folder with the
    guide line in THAT guide only."""
    s = _setup_two(rig, monkeypatch)
    c, rec, guides = s["c"], s["rec"], s["guides"]
    wid = rec["id"]
    config._config["courses_export"] = True
    originals = {slug: path.read_bytes() for slug, path in guides.items()}
    plan = _plan(c, wid)
    r = _write_guides(c, wid, plan)
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["message"] == "Wrote 4 figures into 2 guides of demo (01-first, 02-second). Review and publish them in the Content Editor."
    assert (result["figures"], result["images"], result["guide_written"], result["player"]) == (4, 4, True, False)
    assert [(g["slug"], g["guide"], g["figures"], g["guide_written"]) for g in result["guides"]] == [
        ("01-first", "demo/01-first/guide.md", 2, True), ("02-second", "demo/02-second/guide.md", 2, True)]
    assert (result["slug"], result["guide"]) == ("01-first", "demo/01-first/guide.md"), "the one-guide fields name the first"
    assert sorted(result["written"]) == sorted(["01-first/guide.md", "02-second/guide.md"] + [f"_assets/images/{p['image']}" for p in plan["placed"]])
    for slug, path in guides.items():
        raw = path.read_bytes()
        assert raw.count(b"\r\n") == raw.count(b"\n") and raw.count(b"\r\n") > originals[slug].count(b"\r\n"), "CRLF stays CRLF, new lines too"
        text = raw.decode("utf-8")
        assert _without_blocks(text) == originals[slug].decode("utf-8"), "the rest of each guide byte for byte"
        other = {"01-first": "wt-02-second-", "02-second": "wt-01-first-"}[slug]
        assert f"wt-{slug}-{wid}-" in text and other not in text, "each step's figure is in ITS workshop's guide"
    assert [g["guide_sha256"] for g in result["guides"]] == [hashlib.sha256(guides[slug].read_bytes()).hexdigest() for slug in ("01-first", "02-second")]
    assert result["guide_sha256"] == result["guides"][0]["guide_sha256"]
    for p in plan["placed"]:
        assert (s["images"] / p["image"]).read_bytes() == (rig["tmp"] / "walkthroughs" / wid / f"step-{p['step_id']}.png").read_bytes()
    rows = auth_store.list_audit(action="walkthrough.export_course")
    assert [(row["entity_id"], row["detail"]) for row in rows] == [(wid, "demo: 4 figures, 4 images into 2 guides (01-first, 02-second)")]
    # Each guide still parses to its own steps, the figure now each step's picture.
    for slug, texts in (("01-first", [st["text"] for st in rec["steps"][:5]]), ("02-second", [st["text"] for st in rec["steps"][5:]])):
        again = workshops.parse_guide(guides[slug].read_bytes().decode("utf-8"), {"spoon": "Pentaho Data Integration"})
        assert [st.text for st in again.steps] == texts
    # A second export: nothing to write in either guide, byte for byte and time for time.
    snap = _snapshot(s["folder"])
    second = _plan(c, wid)
    assert second["changed"] is False and [g["diff"] for g in second["guides"]] == ["", ""] and second["diff"] == ""
    assert [p["how"] for p in second["placed"]] == ["unchanged"] * 4
    r = _write_guides(c, wid, second)
    assert r.status_code == 200 and r.json()["written"] == [] and r.json()["removed"] == []
    assert r.json()["message"] == "Nothing to write: the 2 guides of demo (01-first, 02-second) already hold 4 figures of this walkthrough."
    assert _snapshot(s["folder"]) == snap
    # The body before W2e - one sha256 - cannot vouch for two guides: refused, nothing written.
    r = _write(c, wid, second["guide_sha256"])
    assert r.status_code == 400 and "preview again" in r.json()["detail"] and "02-second" in r.json()["detail"], r.text
    assert _snapshot(s["folder"]) == snap
    # The zip: every guide and the pictures, laid out like the course.
    zf = zipfile.ZipFile(io.BytesIO(c.get(f"/api/walkthroughs/{wid}/export/course.zip").content))
    assert zf.namelist() == sorted(["01-first/guide.md", "02-second/guide.md"] + [f"_assets/images/{p['image']}" for p in plan["placed"]])
    assert zf.read("01-first/guide.md") == guides["01-first"].read_bytes() and zf.read("02-second/guide.md") == guides["02-second"].read_bytes()
    _needs_player()
    names = zipfile.ZipFile(io.BytesIO(c.get(f"/api/walkthroughs/{wid}/export/course.zip?player=1").content)).namelist()
    assert "01-first/walkthrough/index.html" in names and not any(n.startswith("02-second/walkthrough/") for n in names)
    # The player written: into the FIRST workshop's folder, the guide line in THAT guide only.
    third = _plan(c, wid, player=True)
    assert [g["diff"] != "" for g in third["guides"]] == [True, False]
    r = _write_guides(c, wid, third, player=True)
    assert r.status_code == 200, r.text
    assert (s["folder"] / "01-first" / "walkthrough" / "index.html").is_file() and not (s["folder"] / "02-second" / "walkthrough").exists()
    assert f"<!-- media-studio: walkthrough {wid} player -->" in guides["01-first"].read_bytes().decode("utf-8")
    assert "player -->" not in guides["02-second"].read_bytes().decode("utf-8")
    assert auth_store.list_audit(action="walkthrough.export_course")[0]["detail"] == "demo: 4 figures, 0 images into 2 guides (01-first, 02-second), player"


def test_a_changed_guide_among_several_is_refused_with_409_naming_it_and_nothing_is_written(rig, monkeypatch):
    """E4: EVERY guide's sha256 is checked before the first byte - one changed is a 409 naming it, nothing written in
    either guide; a guide left out of the body is refused too; the body takes one form or the other."""
    s = _setup_two(rig, monkeypatch)
    c, rec, guides = s["c"], s["rec"], s["guides"]
    wid = rec["id"]
    config._config["courses_export"] = True
    plan = _plan(c, wid)
    guides["02-second"].write_bytes(guides["02-second"].read_bytes() + b"An edit in the Content Editor.\r\n")
    before = _snapshot(s["folder"])
    r = _write_guides(c, wid, plan)
    assert r.status_code == 409 and r.json()["detail"] == "The guide demo/02-second/guide.md changed since the preview: preview again.", r.text
    assert _snapshot(s["folder"]) == before, "nothing written: not a picture, not the first guide"
    assert auth_store.list_audit(action="walkthrough.export_course") == []
    only_first = {"guides": [{"slug": "01-first", "sha256": plan["guides"][0]["guide_sha256"]}], "player": False}
    r = c.post(EXPORT_BODY.format(wid=wid), json=only_first)
    assert r.status_code == 400 and "02-second" in r.json()["detail"] and "preview again" in r.json()["detail"], r.text
    assert _snapshot(s["folder"]) == before
    sha = plan["guides"][0]["guide_sha256"]
    for bad in (
        {"guide_sha256": sha, "guides": [{"slug": "01-first", "sha256": sha}]},
        {"player": False},
        {"guides": []},
        {"guides": [{"slug": "01-first", "sha256": "nope"}]},
        {"guides": [{"slug": "01-first", "sha256": sha}, {"slug": "01-first", "sha256": sha}]},
        {"guides": [{"slug": "01-first", "sha256": sha, "extra": 1}]},
    ):
        assert c.post(EXPORT_BODY.format(wid=wid), json=bad).status_code == 422, bad
    assert _snapshot(s["folder"]) == before
    # Previewed again, it goes through.
    assert _write_guides(c, wid, _plan(c, wid)).status_code == 200


def test_a_failure_in_the_second_guide_rolls_back_the_first_guide_and_every_picture(rig, monkeypatch):
    """E4: one journal for the whole export - the second guide's write failing part-way puts the first guide and
    every picture back as they were, no backup or temporary file left, no audit row."""
    s = _setup_two(rig, monkeypatch)
    c, rec, folder = s["c"], s["rec"], s["folder"]
    wid = rec["id"]
    config._config["courses_export"] = True
    plan = _plan(c, wid)
    before = _contents(folder)
    real_write = course_export.write_file
    images = sorted(p["image"] for p in plan["placed"])

    def failing(target_folder, file_name, data):
        if file_name == "guide.md" and target_folder.name == "02-second":
            # X3: at this moment the export HAS written - guide A holds the walkthrough's two blocks and every picture
            # is on disk - so the rollback below has something to take back (the test cannot pass by never reaching A).
            text = s["guides"]["01-first"].read_bytes().decode("utf-8")
            assert text.count(f"<!-- media-studio: walkthrough {wid} step ") == 2, "guide A was written before guide B failed"
            assert sorted(p.name for p in s["images"].iterdir() if p.name.startswith("wt-")) == images, "every picture was written first"
            raise PermissionError(13, "Access is denied")
        return real_write(target_folder, file_name, data)

    monkeypatch.setattr(course_export, "write_file", failing)
    r = _write_guides(c, wid, plan)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Could not write demo/02-second/guide.md into the course: Access is denied. Nothing was changed: download the zip instead."
    assert _contents(folder) == before, "the first guide restored, every picture taken back, nothing left behind"
    assert auth_store.list_audit(action="walkthrough.export_course") == []
    monkeypatch.setattr(course_export, "write_file", real_write)
    assert _write_guides(c, wid, plan).status_code == 200
    assert _contents(folder) != before


def test_a_read_only_second_guide_is_refused_before_a_byte_naming_it(rig, monkeypatch):
    """X3: ``check_writable`` covers every guide's target - a read-only ``02-second/guide.md`` is refused with 400
    naming it before anything is written (guide A untouched, no picture, no audit row); writable again, the same
    plan writes both guides."""
    s = _setup_two(rig, monkeypatch)
    c, rec, guides, folder = s["c"], s["rec"], s["guides"], s["folder"]
    wid = rec["id"]
    config._config["courses_export"] = True
    plan = _plan(c, wid)
    before = _contents(folder)
    os.chmod(guides["02-second"], stat.S_IREAD)
    try:
        r = _write_guides(c, wid, plan)
    finally:
        os.chmod(guides["02-second"], stat.S_IREAD | stat.S_IWRITE)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "Could not write demo/02-second/guide.md into the course: it is read-only. Nothing was changed: download the zip instead."
    assert _contents(folder) == before, "nothing written: not guide A, not a picture"
    assert auth_store.list_audit(action="walkthrough.export_course") == []
    r = _write_guides(c, wid, plan)
    assert r.status_code == 200, r.text
    assert {"01-first/guide.md", "02-second/guide.md"} <= set(r.json()["written"])
    assert len(auth_store.list_audit(action="walkthrough.export_course")) == 1


def test_a_step_of_no_workshop_of_the_record_is_not_placed_and_says_why(rig, monkeypatch):
    """E4: a step whose slug names none of the record's workshops is not placed, with the reason; in a walkthrough
    of several workshops a step with no workshop at all (added from a still) is not placed either - nothing is
    guessed - where a walkthrough of one workshop still places it by its text."""
    s = _setup_two(rig, monkeypatch, picture=(2, 6), hotspots=(2, 6))
    c, rec = s["c"], s["rec"]
    wid = rec["id"]
    cid = _still(rig, monkeypatch, 300, 200)
    rec = c.post(f"/api/walkthroughs/{wid}/steps", json={"capture_id": cid}).json()
    assert "slug" not in (rec["steps"][-1]["source"] or {})
    stored_path = rig["tmp"] / "walkthroughs" / wid / "walkthrough.json"
    stored = json.loads(stored_path.read_text(encoding="utf-8"))
    stored["steps"][5]["source"]["slug"] = "09-elsewhere"
    stored_path.write_text(json.dumps(stored), encoding="utf-8")
    plan = _plan(c, wid)
    assert [(n["number"], n["reason"]) for n in plan["not_placed"]] == [
        (6, course_export.FOREIGN_WORKSHOP_REASON), (8, course_export.NO_WORKSHOP_REASON)]
    assert all(g["not_placed"] == [] for g in plan["guides"]), "neither belongs to a guide"
    assert [p["number"] for p in plan["placed"]] == [2] and plan["figures"] == 1
    assert course_export.FOREIGN_WORKSHOP_REASON == "Its workshop is not one this walkthrough was imported from."
    assert "guessed" in course_export.NO_WORKSHOP_REASON or "no workshop" in course_export.NO_WORKSHOP_REASON.lower()
    # The same step without a picture is skipped (no picture), not "not placed".
    rec = c.get(f"/api/walkthroughs/{wid}").json()
    gone = [st for st in rec["steps"] if st["id"] != rec["steps"][-1]["id"]]
    c.put(f"/api/walkthroughs/{wid}", json={"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": gone})
    plan = _plan(c, wid)
    assert [(n["number"], n["reason"]) for n in plan["not_placed"]] == [(6, course_export.FOREIGN_WORKSHOP_REASON)]
    assert (7, course_export.NO_PICTURE_REASON) in [(k["number"], k["reason"]) for k in plan["skipped"]]


def test_a_picture_named_by_one_of_the_walkthroughs_own_other_guides_is_kept(rig, monkeypatch):
    """E4: ``used_elsewhere`` looks across the course's other workshops - one of the walkthrough's OWN other guides
    among them: a picture an author reused there is "used", never removed, and the plan says so."""
    s = _setup_two(rig, monkeypatch, picture=(2, 6), hotspots=(2, 6))
    c, rec, guides, images = s["c"], s["rec"], s["guides"], s["images"]
    wid = rec["id"]
    config._config["courses_export"] = True
    assert _write_guides(c, wid, _plan(c, wid)).status_code == 200
    sid = rec["steps"][5]["id"]
    name = f"wt-02-second-{wid}-{sid}.png"
    first = guides["01-first"]
    first.write_bytes(first.read_bytes() + f'\r\n<img src="../_assets/images/{name}" alt="Reused by an author">\r\n'.encode("utf-8"))
    rec = c.get(f"/api/walkthroughs/{wid}").json()
    rec = c.put(f"/api/walkthroughs/{wid}", json={"version": 1, "name": rec["name"], "revision": rec["revision"],
                                                   "steps": [st for st in rec["steps"] if st["id"] != sid]}).json()
    plan = _plan(c, wid)
    assert [rm["step_id"] for rm in plan["guides"][1]["removed"]] == [sid] and plan["guides"][0]["removed"] == []
    assert plan["kept"] == [{"path": f"_assets/images/{name}", "reason": "still used by 01-first"}] and plan["guides"][1]["kept"] == plan["kept"]
    assert f"_assets/images/{name}" not in {f["path"] for f in plan["files"] if f["action"] == "remove"}
    r = _write_guides(c, wid, plan)
    assert r.status_code == 200 and r.json()["removed"] == [] and (images / name).is_file()
    assert sid not in guides["02-second"].read_bytes().decode("utf-8")


def test_the_real_lab_two_workshops_export_each_into_its_own_guide(lab, rig, monkeypatch):
    """The course the parser was designed against, COPIED: two workshops of one section imported as one walkthrough,
    every step without a picture captured, the export written - each figure after its own item in ITS guide, both
    guides CRLF and parsing as before."""
    shutil.copytree(lab / LAB, rig["courses"] / LAB)
    c = rig["admin"]
    slugs = ["01-first-win", "02-build-the-pipeline"]
    r = c.post("/api/walkthroughs/import", json={"course": LAB, "workshops": [{"slug": slugs[1]}, {"slug": slugs[0]}]})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["name"] == "See It Work" and [w["slug"] for w in rec["source"]["workshops"]] == slugs
    assert len(rec["steps"]) == GUIDES[slugs[0]][0] + GUIDES[slugs[1]][0]
    assert rec["steps"][0]["chapter"] == "Your First Win · 1. Open the pipeline"
    for step in rec["steps"]:
        if not step["image"]:
            cid = _still(rig, monkeypatch, 1600, 900)
            rec = c.post(f"/api/walkthroughs/{rec['id']}/steps/{step['id']}/picture", json={"capture_id": cid, "hotspot": HOTSPOT}).json()
    paths = {slug: rig["courses"] / LAB / slug / "guide.md" for slug in slugs}
    originals = {slug: path.read_bytes() for slug, path in paths.items()}
    plan = _plan(c, rec["id"])
    captured = [GUIDES[slug][0] - GUIDES[slug][2] for slug in slugs]
    assert [g["figures"] for g in plan["guides"]] == captured and plan["not_placed"] == []
    assert len(plan["skipped"]) == GUIDES[slugs[0]][2] + GUIDES[slugs[1]][2]
    config._config["courses_export"] = True
    assert _write_guides(c, rec["id"], plan).status_code == 200
    for slug, path in paths.items():
        raw = path.read_bytes()
        assert raw.count(b"\r\n") == raw.count(b"\n"), "the guide stays CRLF"
        text = raw.decode("utf-8")
        assert _without_blocks(text) == originals[slug].decode("utf-8")
        before = workshops.parse_guide(originals[slug].decode("utf-8"), {"spoon": "Pentaho Data Integration"})
        after = workshops.parse_guide(text, {"spoon": "Pentaho Data Integration"})
        assert [(st.text, st.instruction, st.chapter, st.note, st.key, st.ordinal) for st in after.steps] == [
            (st.text, st.instruction, st.chapter, st.note, st.key, st.ordinal) for st in before.steps]
        lines = workshops.guide_lines(text)
        for st in after.steps:
            marker = next((i for i in range(st.start_line, st.end_line) if lines[i].startswith("<!-- media-studio:")), None)
            assert (marker is not None) == (f"_assets/images/wt-{slug}-" in (st.image or "")), (slug, st.text)
    assert _plan(c, rec["id"])["changed"] is False
