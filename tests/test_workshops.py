"""Workshop import (W2): the courses folder, the parser, the routes and the
imported walkthrough.

- the parser on every guide of the ``pdi-2hr-lab`` course (the course is COPIED
  into the test's own folder, its PNGs included - nothing reads the screen):
  the eight step counts, the chapter names, the pictures found, the launchers,
  the notes, and the first and last instruction of ``01-first-win`` spelled out;
- the plain-text rules on inline guides: bold, code and links dropped, the
  continuation lines joined, a bullet a line, the prose paragraph and the code
  block after an item appended, a table a line per row; the figure-to-step
  rule (the first figure after the item; after two items it is the last's; a
  URL is never a picture), for both picture forms - the Markdown image line
  and the house figure's ``<img>`` tag (any attribute order, either quote,
  self-closing or not, unescaped) - and the one path rule (under the course's
  ``_assets/images``); the hints (Note, Tip, Key Takeaways skipped; Under
  the hood kept as the note of the step it follows); the skipped sections; the
  launcher as a step; the caps with a whole-sentence cut;
- nothing outside the courses folder is read: a course id that is a path, a
  course folder that is a junction elsewhere, a workshop folder that is, a
  ``course.json`` that is a link, a guide over 2 MB (refused before it is
  read), an image that walks out of the course; a missing ``_assets`` image
  imports the step without a picture; a course or workshop whose files cannot
  be read is left out of the list (logged) and its preview and import say why;
- the save's rules are the parser's (one rule, one place): control characters
  stripped from every text and the code lines, every field bounded by the
  schema's constants, the belt (``create_from_workshop`` validates the plan
  with ``WalkthroughIn`` before it writes); a code block keeps its fences
  through the parse, the import and the transcript;
- the routes: the list (unset folder, SUMMARY order, counts), the preview, the
  import (the record: the name, ``source``, ``note``, the pictures as copies,
  ``MAX_STEPS``, an empty workshop, the audit row, ownership, a session);
- the courses-folder setting: admin-only, validated as an existing folder,
  cleared with "";
- a step without a picture in the document model, Replace picture with a
  hotspot, the guided capture's audit route, the limits rows.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import api.routers.capture as capture_router
from api import store as auth_store
from services import capture, captures, recordings, studio_settings, walkthroughs, workshops
from services import projects as store
from utils.config import config

PASSWORD = "Owner-pass-12345"
LOOPBACK = ("127.0.0.1", 50000)
REPO_ROOT = Path(__file__).resolve().parent.parent
LIMITS_MD = REPO_ROOT / "docs" / "reference" / "limits.md"
#: The course the parser was designed against: a sibling checkout of the content manager, or wherever
#: ``MEDIA_STUDIO_COURSES_SRC`` points. The guide tests skip when it is not there; the inline ones never do.
COURSES_SRC = Path(os.environ.get("MEDIA_STUDIO_COURSES_SRC") or (REPO_ROOT.parent / "Pentaho-Content-Manager" / "courses"))
LAB = "pdi-2hr-lab"

#: What each guide of the lab parses to: steps, chapter names, pictures found, launchers, notes. Measured on the
#: course as it is now (0.1.25): its pictures are house figures (``<figure>`` + ``<img src alt>``) since the
#: content manager's S303 sweep, its section headings carry their number since "labs 01-04 as steps".
GUIDES = {
    "00-before-you-start": (1, ["Start Pentaho Data Integration"], 1, 1, 0),
    "01-first-win": (10, ["1. Open the pipeline", "2. Preview the valid rows", "3. Preview the rejects", "4. Look inside a step"], 2, 1, 3),
    "02-build-the-pipeline": (23, ["2. Read the file", "3. Validate the keys", "4. Send the rejects to a file", "5. Run it"], 9, 0, 2),
    "03-enrich-and-join": (28, ["1. Read the customer master (JSON)", "2. Read the product catalog", "3. Enrich with two lookups",
                                "4. Compute revenue and margin", "5. Join the region reference (a merge join)", "6. Dummy Step", "7. Run it"], 15, 0, 2),
    "04-track-history-scd": (20, ["1. Connect to the database", "2. Configure the dimension step", "3. First load", "4. Now change history"], 9, 0, 1),
    "05-metadata-injection": (21, ["1. The template", "2. The injector", "3. The driver job", "4. Run and extend"], 13, 0, 2),
    "09-bring-your-own-data": (5, ["Read your file"], 0, 0, 0),
    "10-from-lab-to-production": (7, ["Watch: the production shape", "Where does it hurt?", "Before you go"], 0, 2, 0),
}

FIRST_WIN_FIRST = "Open win_preview.ktr in Pentaho Data Integration."
FIRST_WIN_LAST = "Select Cancel to close the dialog (so nothing changes)."
#: The code block stays between its ``` fences (R6): the narration round skips what is between them.
FIRST_WIN_SECOND = (
    "If the pipeline - win_preview.ktr - has not displayed in Pentaho Data Integration, select File > Open (Ctrl+O) and open:\n\n"
    "Path to Transformation:\n\n"
    "```\nC:\\Workshop\\pdi-2hr\\02-see-it-work\\01-your-first-win\\win_preview.ktr\n```\n\n"
    "You should see three connected steps on the canvas: a file reader, a filter, and two end points — one for valid rows, one for rejects."
)


# -- fixtures ---------------------------------------------------------------------------------------

@pytest.fixture
def rig(tmp_path, monkeypatch):
    """The app with every data folder under the test's own, three signed-in clients and a courses folder set."""
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
    courses = tmp_path / "courses"
    courses.mkdir()
    config._config["courses_folder"] = str(courses)
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
        yield {"admin": admin, "other": other, "owner": owner, "tmp": tmp_path, "app": app, "courses": courses}


def _png(size=(8, 8), colour=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _course(root: Path, cid: str = "demo", title: str = "Demo course", summary: str | None = None, launchers: dict | None = None) -> Path:
    """A small course folder: course.json, SUMMARY.md, _assets/images and a workshop made by ``_workshop``."""
    folder = root / cid
    (folder / "_assets" / "images").mkdir(parents=True, exist_ok=True)
    (folder / "course.json").write_text(json.dumps({
        "id": cid, "title": title, "version": "1.2.3", "description": "A demo.",
        "launchers": launchers if launchers is not None else {"spoon": {"label": "Pentaho Data Integration", "action": "Start"}},
    }), encoding="utf-8")
    (folder / "SUMMARY.md").write_text(summary if summary is not None else "# Table of contents\n\n## Part one\n\n* [First](01-first/guide.md)\n", encoding="utf-8")
    return folder


def _workshop(folder: Path, slug: str, guide: str, manifest: dict | None = None, images: dict[str, bytes] | None = None) -> Path:
    (folder / slug).mkdir(parents=True, exist_ok=True)
    (folder / slug / "guide.md").write_text(guide, encoding="utf-8")
    if manifest is not None:
        (folder / slug / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, data in (images or {}).items():
        (folder / "_assets" / "images" / name).write_bytes(data)
    return folder / slug


def _parse(guide: str, launchers: dict | None = None) -> workshops.ParsedGuide:
    return workshops.parse_guide(guide, launchers if launchers is not None else {"spoon": "Pentaho Data Integration"})


@pytest.fixture
def lab(tmp_path):
    """The real course, copied into the test's folder (its PNGs included; the lab files left out)."""
    if not (COURSES_SRC / LAB / "course.json").is_file():
        pytest.skip(f"the {LAB} course is not at {COURSES_SRC}")
    root = tmp_path / "copied-courses"
    shutil.copytree(COURSES_SRC / LAB, root / LAB, ignore=shutil.ignore_patterns("files"))
    return root


def _link_directory(link: Path, target: Path) -> bool:
    """A junction on Windows (no administrator needed), a symlink elsewhere; False when neither can be made."""
    if os.name == "nt":
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        return made.returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


# -- the real course, every guide -------------------------------------------------------------------

@pytest.mark.parametrize("slug", list(GUIDES))
def test_every_guide_of_the_lab_parses_to_its_steps_chapters_pictures_launchers_and_notes(lab, slug):
    steps, chapters, pictures, launchers, notes = GUIDES[slug]
    course, workshop, text, folder = workshops.load_workshop(lab, LAB, slug)
    parsed = workshops.parse_guide(text, course["launchers"])
    assert len(parsed.steps) == steps, [s.text for s in parsed.steps]
    assert parsed.chapters == chapters
    assert [s.chapter for s in parsed.steps if s.chapter] == chapters, "the first step of each section carries the chapter"
    assert not parsed.truncated
    preview = workshops.preview(course, workshop, parsed, folder)
    assert preview["counts"] == {"steps": steps, "chapters": len(chapters), "pictures": pictures, "to_capture": steps - pictures}
    assert sum(1 for s in parsed.steps if s.launcher) == launchers
    assert sum(1 for s in parsed.steps if s.note) == notes
    assert preview["message"] is None, "every picture the guide names is in _assets"
    for step in parsed.steps:
        assert step.instruction.strip() and len(step.instruction) <= walkthroughs.MAX_INSTRUCTION_CHARS
        assert step.text and len(step.text) <= workshops.TOOLTIP_CHARS
        assert len(step.note) <= walkthroughs.MAX_NOTE_CHARS and len(step.chapter) <= walkthroughs.MAX_CHAPTER_CHARS
        assert "**" not in step.instruction and "`" not in step.instruction.replace("```", "") and "](" not in step.instruction, step.instruction
        assert step.instruction.count("```") % 2 == 0, "a code block's fences come in pairs (R6)"
        assert "<" not in step.text and "![" not in step.instruction and "```" not in step.text
        if step.image:
            assert workshops.resolve_image(folder, slug, step.image) is not None


def test_the_lab_lists_its_eight_workshops_in_summary_order(lab):
    courses = workshops.list_courses(lab)
    assert [c["id"] for c in courses] == [LAB]
    course = courses[0]
    assert course["title"] == "PDI in 2 Hours" and course["version"] and "spoon" in course["launchers"]
    assert [s["title"] for s in course["sections"]] == ["Get Ready", "See It Work", "Make It Yours", "See It Scale", "Make It Real"]
    assert [w["slug"] for s in course["sections"] for w in s["workshops"]] == list(GUIDES)
    first = course["sections"][1]["workshops"][0]
    assert first == {"slug": "01-first-win", "title": "Your First Win", "description": first["description"], "estimatedMinutes": 10, "stepCount": 5}
    assert "launchers" not in workshops.public_course(course)


def test_the_first_and_last_instruction_of_first_win_spelled_out(lab):
    course, workshop, text, folder = workshops.load_workshop(lab, LAB, "01-first-win")
    # The guide writes its pictures as house figures only (``<figure>`` + ``<img src alt>``), no Markdown image: its
    # two pictures below are read from ``<img>`` tags (the third figure is in a section with no step).
    assert "![" not in text and text.count("<img ") == 3 and text.count("<figure>") == 4
    parsed = workshops.parse_guide(text, course["launchers"])
    first, second, last = parsed.steps[0], parsed.steps[1], parsed.steps[-1]
    # The launcher button, placed where it stands: the file's name and the launcher's label from course.json.
    assert first.instruction == FIRST_WIN_FIRST and first.text == FIRST_WIN_FIRST
    assert first.launcher == {"id": "spoon", "path": "files/win_preview.ktr", "label": "Open in Pentaho Data Integration"}
    assert first.chapter == "1. Open the pipeline" and first.image is None, "the figure after item 1 is item 1's"
    # Item 1: the bold and code marks dropped, the prose, the code block and the prose after the figure appended.
    assert second.instruction == FIRST_WIN_SECOND
    assert second.text == "If the pipeline - win_preview.ktr - has not displayed in Pentaho Data Integration, select File > Open (Ctrl+O) and open:"
    assert second.image == "../_assets/images/1788268810024.png" and second.alt.startswith("win_preview open in Spoon: Read yesterday's sales")
    # The last step has no hint after it; the Under the hood before it (after item 3 of its section) is that
    # item's note; the Key Takeaways note is no one's.
    assert last.instruction == FIRST_WIN_LAST and last.text == FIRST_WIN_LAST and last.chapter == "" and last.note == ""
    inside = parsed.steps[-2]
    assert inside.text == "Double-click Keys present?."
    assert inside.note.startswith("The Dialog is the Source Code\n\nNothing was generated from what you just looked at.")
    assert "Key Takeaways" not in inside.note and "version-controlled" not in inside.note
    assert inside.note.endswith("the thing you tested is literally the thing that ships.")
    # Step 4 ("Select Quick Launch."): its picture, the prose after the figure, its note.
    fourth = parsed.steps[3]
    assert fourth.text == "Select Quick Launch."
    assert fourth.instruction.startswith("Select Quick Launch.\n\nA grid appears with 37 of yesterday's orders")
    assert fourth.image == "../_assets/images/1788269301694.png"
    assert fourth.note.startswith("Preview is the real engine, not a simulation\n\nThree words first")
    assert parsed.title == "Your First Win"


def test_every_sibling_course_at_least_parses(lab):
    """The other courses in the same folder (when it is there): every guide parses without an error."""
    if not COURSES_SRC.is_dir():
        pytest.skip("no courses folder beside the checkout")
    seen = 0
    for course in workshops.list_courses(COURSES_SRC):
        for section in course["sections"]:
            for item in section["workshops"]:
                loaded, workshop, text, folder = workshops.load_workshop(COURSES_SRC, course["id"], item["slug"])
                parsed = workshops.parse_guide(text, loaded["launchers"])
                workshops.preview(loaded, workshop, parsed, folder)
                seen += 1
    assert seen >= len(GUIDES)


# -- the plain-text rules, on inline guides ----------------------------------------------------------

GUIDE = """# A Guide

> **Warning:**
>
> #### Workshop
>
> **What you'll do**
>
> **Estimated Time:** 5 minutes

<figure>

![Walkthrough](https://vimeo.com/placeholder)

<figcaption class="pcm-video-caption">Watch</figcaption>

</figure>

## Open it

Click the button below.

<button data-launch="spoon" data-path="files/win.ktr">Open in Pentaho Data Integration</button>

1. **Right-click** the step named **Valid rows** and pick [Preview](https://example.com/x) from `the menu`
   that opens, then
   * tick **Header**
   * leave the rest
2. Choose **Preview**, then **Quick Launch**.

<figure>

![The dialog with [*] in it](../_assets/images/one.png#w=400)

<figcaption><em>Preview</em></figcaption>
</figure>

<figure>

![A second figure after the same item](../_assets/images/two.png)

</figure>

A grid appears with 37 rows.

```text
C:\\Workshop\\one.csv
```

| Name | Path | Type |
| --- | --- | --- |
| id | $.a[*].id | String |

> **Under the hood:**
>
> #### Preview is real
>
> It ran the **engine**.

Prose after the hint is not the step's.

3. Close it.

> **Note:**
>
> **Key Takeaways**
>
> * Skipped.

<button data-graph="files/win.ktr">View graph</button>

## Only prose

Nothing numbered here.

## Troubleshooting <!-- no-step -->

<details>

<summary>It fails</summary>

1. Not a step: inside the troubleshooting.

</details>

## Lab Files

[win.ktr](./files/win.ktr) <button data-launch="spoon" data-path="files/win.ktr">Open in Pentaho Data Integration</button>
"""


def test_the_parser_rules_on_an_inline_guide():
    parsed = _parse(GUIDE)
    assert parsed.title == "A Guide"
    assert parsed.chapters == ["Open it"]
    assert [s.chapter for s in parsed.steps] == ["Open it", "", "", ""]
    launcher, one, two, three = parsed.steps
    assert launcher.instruction == "Open win.ktr in Pentaho Data Integration." and launcher.launcher["path"] == "files/win.ktr"
    assert launcher.image is None, "the Vimeo placeholder is never a picture, and the launcher's figure would come after it"
    # Bold, the link's text, the code span, the continuation joined, the bullets as lines.
    assert one.instruction == "Right-click the step named Valid rows and pick Preview from the menu that opens, then\ntick Header\nleave the rest"
    assert one.text == "Right-click the step named Valid rows and pick Preview from the menu that opens, then tick Header leave the rest"
    assert one.image is None, "the figure after two items in a row belongs to the last of them"
    # The FIRST figure after the item is its picture (a second one is not); the prose after it, the code block and the
    # table appended; the hint ends the step's content.
    assert two.image == "../_assets/images/one.png#w=400" and two.alt == "The dialog with [*] in it"
    assert two.instruction == (
        "Choose Preview, then Quick Launch.\n\nA grid appears with 37 rows.\n\n```\nC:\\Workshop\\one.csv\n```\n\n"
        "Name \u00b7 Path \u00b7 Type\nid \u00b7 $.a[*].id \u00b7 String"
    ), "the code block between its fences, the table a line per row"
    assert two.text == "Choose Preview, then Quick Launch."
    assert two.note == "Preview is real\n\nIt ran the engine."
    assert "Prose after the hint" not in two.instruction
    assert three.instruction == "Close it." and three.note == "" and three.image is None
    assert all("Key Takeaways" not in s.note and "Skipped" not in s.note for s in parsed.steps)
    assert len(parsed.steps) == 4, "the section with only prose, the Troubleshooting and the Lab Files make no steps"


#: Both picture forms in one guide: the Markdown image line and the house figure's ``<img>`` tag, by ONE rule.
BOTH_FORMS = """# Both forms

## One

1. Markdown figure.

![Markdown alt](../_assets/images/md.png#w=400)

2. House figure.

<div align="center">
<figure>
<img src="../_assets/images/house.png" alt="House &amp; alt" width="800">
<figcaption>House caption</figcaption>
</figure>
</div>

The prose after the figure is still the step's.

3. One-line figure.

<figure><img alt='One line' src='../_assets/images/line.png' /><figcaption>Cap</figcaption></figure>

4. Two items in a row.
5. The figure is the last one's.

<figure>
<img src="../_assets/images/last.png" alt="Last">
</figure>

6. The first figure wins, whichever its form.

<img src="../_assets/images/first.png" alt="First">

![Second](../_assets/images/second.png)

7. Markdown first, then a house figure.

![Md first](../_assets/images/mdfirst.png)

<figure>
<img src="../_assets/images/notthis.png" alt="Not this">
</figure>

8. A refused figure does not take the place of the next one.

<figure>
<img src="data:image/png;base64,iVBORw0KGgo=" alt="Inline data">
</figure>

<figure>
<img src="../_assets/images/after-refused.png" alt="After the refused one">
</figure>

## Two

<figure>
<img src="../_assets/images/orphan.png" alt="No step before it in this section">
</figure>

1. After an orphan figure.
"""


def test_a_picture_is_a_markdown_image_line_or_an_img_tag_by_one_rule():
    parsed = _parse(BOTH_FORMS)
    assert [(s.image, s.alt) for s in parsed.steps] == [
        ("../_assets/images/md.png#w=400", "Markdown alt"),
        ("../_assets/images/house.png", "House & alt"),
        ("../_assets/images/line.png", "One line"),
        (None, ""),
        ("../_assets/images/last.png", "Last"),
        ("../_assets/images/first.png", "First"),
        ("../_assets/images/mdfirst.png", "Md first"),
        ("../_assets/images/after-refused.png", "After the refused one"),
        (None, ""),
    ], "the first accepted figure after the item, either form; after two items it is the last's; a section's figure before any step is no one's"
    assert parsed.chapters == ["One", "Two"]
    # A figure between the item and its prose does not end the step's content; the caption and the alt are no text.
    assert parsed.steps[1].instruction == "House figure.\n\nThe prose after the figure is still the step's."
    for step in parsed.steps:
        assert "<" not in step.instruction and "Caption" not in step.instruction and "Cap" not in step.instruction
        assert "alt" not in step.instruction.lower(), step.instruction


def _img(tag: str, after: str = "") -> tuple[str | None, str]:
    """The picture and the alt the parser takes from ``tag`` on its own line after one item."""
    step = _parse(f"## S\n\n1. Step.\n\n{tag}\n{after}").steps[0]
    return step.image, step.alt


def test_an_img_tag_is_read_in_any_attribute_order_quoting_and_closing():
    picture = "../_assets/images/a.png"
    for tag in (
        f'<img src="{picture}" alt="A">',
        f'<img alt="A" src="{picture}">',
        f"<img src='{picture}' alt='A'>",
        f'<img src="{picture}" alt="A"/>',
        f'<img src="{picture}" alt="A" />',
        f"<img src={picture} alt=A>",
        f'<IMG SRC="{picture}" ALT="A">',
        f'<img  src = "{picture}"  alt = "A" >',
        # Other attributes are ignored - a data-src, a srcset, and a src= INSIDE another attribute's value are not the src.
        f'<img width="800" data-src="../_assets/images/no.png" title="src=../_assets/images/no.png" src="{picture}" srcset="x.png 2x" alt="A" loading="lazy">',
        # A duplicate attribute: the first wins, as in a browser.
        f'<img src="{picture}" src="../_assets/images/second.png" alt="A" alt="B">',
        # Inside the house figure on one line, and inside its <div>, and as a link's content.
        f'<div align="center"><figure><img src="{picture}" alt="A"><figcaption>Cap</figcaption></figure></div>',
        f'<a href="../_assets/images/big.png"><img src="{picture}" alt="A"></a>',
        f'<p><img src="{picture}" alt="A"></p>',
    ):
        assert _img(tag) == (picture, "A"), tag
    # No alt: the picture with an empty alt.
    assert _img(f'<img src="{picture}">') == (picture, "")
    # The src and the alt are HTML-unescaped; a ">" inside a quoted value does not end the tag.
    assert _img('<img src="../_assets/images/a&amp;b.png" alt="Fish &amp; chips &quot;x&quot; &#8594; File &gt; Open">') == (
        "../_assets/images/a&b.png", 'Fish & chips "x" → File > Open')
    assert _img(f'<img alt="File > Open" src="{picture}">') == (picture, "File > Open")
    # The alt obeys the save's rules: control characters stripped (an entity-encoded one too), spaces collapsed, capped.
    assert _img(f'<img src="{picture}" alt="a&#12;b \x07c   d">') == (picture, "ab c d")
    image, alt = _img(f'<img src="{picture}" alt="{"w" * 2500}">')
    assert image == picture and alt == "w" * walkthroughs.MAX_ALT_CHARS


def test_a_refused_src_and_what_is_never_a_picture():
    for tag in (
        '<img src="pics/x.png" alt="the workshop folder: outside _assets">',
        '<img src="../files/x.png" alt="the course, outside _assets">',
        '<img src="_assets/images/x.png" alt="a workshop-local _assets is not the course\'s">',
        '<img src="../../outside/x.png" alt="outside the course">',
        '<img src="../_assets/images/../../../x.png" alt="walks out through _assets">',
        '<img src="data:image/png;base64,iVBORw0KGgo=" alt="inline data">',
        '<img src="https://example.com/x.png" alt="by URL">',
        '<img src="//example.com/x.png" alt="protocol-relative">',
        '<img src="/etc/x.png" alt="absolute">',
        '<img src="../_assets/images/x.jpg" alt="not a PNG">',
        '<img src="../_assets/images/a&#12;b.png" alt="a control character, entity-encoded">',
        '<img src="../_assets/images/' + "i" * 400 + '.png" alt="over MAX_SOURCE_PATH_CHARS">',
        '<img src="" alt="empty">',
        '<img alt="no src at all">',
        '<video src="../_assets/images/x.png" controls></video>',
        '<iframe src="../_assets/images/x.png"></iframe>',
        '<source src="../_assets/images/x.png">',
        '<image src="../_assets/images/x.png">',
        '<imgx src="../_assets/images/x.png">',
    ):
        assert _img(tag) == (None, ""), tag
    # Where the Markdown form would not be a picture, the tag is not either: inside a code block, inside <details>,
    # in a table cell, inside a prose line.
    fenced = '```html\n<img src="../_assets/images/x.png" alt="code">\n```'
    assert _img(fenced) == (None, "")
    assert _parse(f"## S\n\n1. Step.\n\n{fenced}\n").steps[0].instruction == 'Step.\n\n```\n<img src="../_assets/images/x.png" alt="code">\n```'
    assert _img('<details>\n<summary>More</summary>\n<img src="../_assets/images/x.png" alt="hidden">\n</details>') == (None, "")
    assert _img('| <figure><img src="../_assets/images/x.png" alt="cell"></figure> | Text |') == (None, "")
    assert _img("| ![cell](../_assets/images/x.png) | Text |") == (None, ""), "the Markdown form in a cell: the same"
    prose = _parse('## S\n\n1. Step.\n\nClick <img src="../_assets/images/x.png" alt="icon"> here.\n').steps[0]
    assert (prose.image, prose.instruction) == (None, "Step.\n\nClick here.")


def test_plain_text_rules():
    plain = workshops.plain_text
    assert plain("**bold** and *em* and _under_ and `code` and [a link](http://x) and ![pic](a.png)") == "bold and em and under and code and a link and pic"
    assert plain("<span class=\"pcm-c-ok\">Green</span> &mdash; ok <!-- gone -->") == "Green \u2014 ok"
    assert plain("Double-click **Keys present?*.") == "Double-click Keys present?."
    assert plain("gross | A * B | $.customers[*].id") == "gross | A * B | $.customers[*].id", "an operator and a bracketed star are not marks"
    assert plain("region_code and customer_id") == "region_code and customer_id"
    assert plain("* [ ] 40 rows read") == "40 rows read" or plain("[ ] 40 rows read") == "40 rows read"
    assert plain("  many    spaces  ") == "many spaces"
    # A code span's marks drop; a ``` fence stays (R6: it marks a code block for the narration to skip).
    assert plain("a `span` and\n\n```\nSELECT 1;\n```") == "a span and\n\n```\nSELECT 1;\n```"
    # The save's own control-character rule (R3): a tab and a line break stay (the tab then collapses as a space does).
    assert plain("kept\tand\nkept, \x07\x0c\x1b\x7f gone") == "kept and\nkept, gone"
    assert walkthroughs.strip_control_characters("a\tb\nc\x0cd\x00e") == "a\tb\ncde"
    # An entity-encoded control character is stripped too, after the entities are unescaped; a tab entity is a tab.
    assert plain("a&#12;b &#x0c;c&#9;d") == "ab c d"


def test_the_cuts_are_whole_sentences_and_whole_words():
    sentence = "One sentence here. Two sentences here. Three sentences here."
    assert workshops.cut_at_sentence(sentence, 100) == sentence
    assert workshops.cut_at_sentence(sentence, 45) == "One sentence here. Two sentences here."
    assert workshops.cut_at_sentence("No sentence end at all in this line of words", 20) == "No sentence end at\u2026"
    assert workshops.cut_at_word("short", 10) == "short"
    assert workshops.cut_at_word("the quick brown fox", 12) == "the quick\u2026"
    assert workshops.first_sentence("Right-click the step. Then wait.\n\nMore") == "Right-click the step."
    assert workshops.first_sentence("Open win_preview.ktr in PDI") == "Open win_preview.ktr in PDI", "a dot inside a file name is no sentence end"
    long = "word " * 60
    tip = workshops.first_sentence(long)
    assert len(tip) <= workshops.TOOLTIP_CHARS and tip.endswith("\u2026") and not tip[:-1].endswith(" ")
    # The instruction and the note caps, applied by the parser.
    guide = "## A\n\n1. " + "A sentence of some words. " * 120 + "\n\n> **Under the hood:**\n>\n> " + "Note sentence here. " * 300 + "\n"
    step = _parse(guide).steps[0]
    assert len(step.instruction) <= walkthroughs.MAX_INSTRUCTION_CHARS and step.instruction.endswith("words.")
    assert len(step.note) <= walkthroughs.MAX_NOTE_CHARS and step.note.endswith("here.")
    chapter = "## " + "c" * 100 + "\n\n1. Step.\n"
    assert len(_parse(chapter).steps[0].chapter) == walkthroughs.MAX_CHAPTER_CHARS


def test_launchers_and_hints_and_sections():
    # A launcher without a file: the button's own text as a sentence; the label fills a missing "Open in".
    assert _parse("## S\n\n<button data-launch=\"spoon\">Start Pentaho Data Integration</button>\n").steps[0].instruction == "Start Pentaho Data Integration."
    assert _parse("## S\n\n<button data-launch=\"spoon\" data-path=\"files/a.ktr\">Open</button>\n", {}).steps[0].instruction == "Open a.ktr in Open."
    assert _parse("## S\n\n<button data-launch=\"spoon\" data-path=\"files/a.ktr\">Open in Spoon</button>\n", {}).steps[0].instruction == "Open a.ktr in Spoon."
    assert _parse("## S\n\n<button data-launch=\"contact\">Talk to us</button>\n", {}).steps[0].instruction == "Talk to us."
    # A data-graph button alone is not a step; a section with only one is skipped.
    assert _parse("## S\n\n<button data-graph=\"files/a.ktr\">View graph</button>\n").steps == []
    # Steps before any heading carry no chapter; ### headings are sections too (the tabs guide).
    parsed = _parse("# T\n\n1. First.\n\n### 1. The template\n\n1. Second.\n2. Third.\n\n### Solution <!-- no-step -->\n\n1. Not a step.\n")
    assert [(s.chapter, s.instruction) for s in parsed.steps] == [("", "First."), ("1. The template", "Second."), ("", "Third.")]
    assert parsed.chapters == ["1. The template"]
    # Two Under the hood hints on one step join; one with no step before it is dropped; a Tip is skipped.
    parsed = _parse("> **Under the hood:**\n>\n> Dropped.\n\n## S\n\n1. A.\n\n> **Tip:** nope\n\n> **Under the hood:**\n>\n> One.\n\n> **Under the hood:**\n>\n> Two.\n")
    assert parsed.steps[0].note == "One.\n\nTwo."
    # An item's lazy continuation line, and a numbered item with a parenthesis.
    assert _parse("## S\n\n1. A line\nthat wraps.\n2) B.\n").steps[0].instruction == "A line that wraps."
    assert len(_parse("## S\n\n1. A line\nthat wraps.\n2) B.\n").steps) == 2
    # The parse stops at MAX_PARSE_STEPS.
    many = "## S\n\n" + "".join(f"{i}. Step {i}.\n" for i in range(1, 8))
    big = _parse(many)
    assert len(big.steps) == 7 and not big.truncated


def test_the_parse_is_bounded(monkeypatch):
    monkeypatch.setattr(workshops, "MAX_PARSE_STEPS", 3)
    parsed = _parse("## S\n\n" + "".join(f"{i}. Step {i}.\n" for i in range(1, 8)))
    assert len(parsed.steps) == 3 and parsed.truncated
    assert workshops.MAX_PARSE_STEPS == 3


def test_images_resolve_only_under_the_courses_assets_and_only_png():
    assert workshops.image_relative_path("01-a", "../_assets/images/x.png#w=200") == "_assets/images/x.png"
    assert workshops.image_relative_path("01-a", "../_assets/images/sub/x.png") == "_assets/images/sub/x.png"
    assert workshops.image_relative_path("01-a", "./../_assets/images/x.PNG") == "_assets/images/x.PNG"
    # Inside the course but outside its _assets/images: the workshop's own folder, the lab files, a local _assets.
    assert workshops.image_relative_path("01-a", "pics/x.png") is None
    assert workshops.image_relative_path("01-a", "../files/x.png") is None
    assert workshops.image_relative_path("01-a", "_assets/images/x.png") is None
    assert workshops.image_relative_path("01-a", "../_assets/x.png") is None
    assert workshops.image_relative_path("01-a", "../_assets/images") is None
    assert workshops.image_relative_path("01-a", "https://vimeo.com/x") is None
    assert workshops.image_relative_path("01-a", "data:image/png;base64,iVBORw0KGgo=") is None
    assert workshops.image_relative_path("01-a", "../../outside/x.png") is None
    assert workshops.image_relative_path("01-a", "/etc/x.png") is None
    assert workshops.image_relative_path("01-a", "C:\\x.png") is None
    assert workshops.image_relative_path("01-a", "../_assets/images/x.jpg") is None
    assert workshops.image_relative_path("01-a", "") is None and workshops.image_relative_path("01-a", None) is None


def test_the_summary_parser():
    text = "# Table of contents\n\n* [Loose](00-loose/guide.md)\n\n## Part one\n\n### Sub\n\n<!-- topic -->\n\n* [First](01-first/guide.md#x)\n* [Bad](../outside/guide.md)\n* [Deep](02-second/sub/guide.md)\n"
    assert workshops.parse_summary(text) == [
        {"title": "", "workshops": [{"slug": "00-loose", "title": "Loose"}]},
        {"title": "Part one", "workshops": [{"slug": "01-first", "title": "First"}, {"slug": "02-second", "title": "Deep"}]},
    ]


# -- nothing outside the folder --------------------------------------------------------------------

def test_a_course_id_or_slug_that_is_a_path_names_nothing(rig, tmp_path):
    root = rig["courses"]
    outside = tmp_path / "outside"
    _course(outside, "leak", title="LEAKED")
    _workshop(outside / "leak", "01-first", "## S\n\n1. LEAKED STEP.\n")
    inside = _course(root, "demo")
    _workshop(inside, "01-first", "## S\n\n1. Fine.\n")
    for bad in ("../outside/leak", "..\\outside\\leak", str(outside / "leak"), "..", ".", "", "demo/../demo", "de mo", "x" * 101):
        assert workshops.load_course(root, bad) is None, bad
        assert not workshops.safe_name(bad), bad
    assert workshops.safe_name("demo") and workshops.safe_name("01-first.win_2")
    c = rig["admin"]
    listed = c.get("/api/courses").json()
    assert [x["id"] for x in listed["courses"]] == ["demo"] and "LEAKED" not in json.dumps(listed)
    for course, slug in (("../outside/leak", "01-first"), ("demo", "../../outside/leak/01-first"), ("demo", ".."), ("..%2Foutside%2Fleak", "01-first")):
        # A ``..`` in the URL is normalised by the CLIENT (httpx and every browser), so the request may land on the
        # SPA catch-all (200, index.html) rather than the route: the invariant is "never a preview, never the leak".
        r = c.get(f"/api/courses/{course}/workshops/{slug}/preview")
        assert "LEAKED" not in r.text and "\"steps\"" not in r.text, f"{course}/{slug} -> {r.status_code} {r.text[:80]}"
        assert r.status_code in (200, 404, 400), f"{course}/{slug} -> {r.status_code}"
        r = c.post("/api/walkthroughs/import", json={"course": course, "slug": slug})
        assert r.status_code in (404, 400, 422), f"{course}/{slug} -> {r.status_code}"
        assert "LEAKED" not in r.text
    # The route function itself, with no client in the way to normalise the dots.
    from fastapi import HTTPException

    from api.routers import courses as courses_router
    for course, slug in (("../outside/leak", "01-first"), ("demo", "../../outside/leak/01-first"), ("demo", "..")):
        with pytest.raises(HTTPException) as caught:
            courses_router.preview_workshop(course, slug, user={"id": "u", "role": "admin"})
        assert caught.value.status_code == 404, f"{course}/{slug}"
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == []


def test_a_course_folder_that_is_a_junction_elsewhere_is_not_a_course(rig, tmp_path):
    root = rig["courses"]
    outside = tmp_path / "elsewhere"
    _course(outside, "real", title="LEAKED")
    _workshop(outside / "real", "01-first", "## S\n\n1. LEAKED.\n")
    if not _link_directory(root / "linked", outside / "real"):
        pytest.skip("this account cannot link a directory")
    try:
        assert workshops.list_courses(root) == []
        assert workshops.load_course(root, "linked") is None
        r = rig["admin"].get("/api/courses/linked/workshops/01-first/preview")
        assert r.status_code == 404 and "LEAKED" not in r.text
    finally:
        (root / "linked").rmdir()


def test_a_course_json_that_is_a_link_is_never_read(rig, tmp_path, monkeypatch):
    root = rig["courses"]
    folder = _course(root, "demo", title="LEAKED-TITLE")
    _workshop(folder, "01-first", "## S\n\n1. Fine.\n")
    target = tmp_path / "real-course.json"
    target.write_text((folder / "course.json").read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "course.json").unlink()
    linked = False
    try:
        (folder / "course.json").symlink_to(target)
        linked = True
    except (OSError, NotImplementedError):
        # This account cannot make a symlink: the OS's answer is stood in for, and the rule is still exercised.
        (folder / "course.json").write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        real_is_link = workshops._is_link
        monkeypatch.setattr(workshops, "_is_link", lambda p: p.name == "course.json" or real_is_link(p))
    reads: list[str] = []
    real_read = workshops._read_text
    monkeypatch.setattr(workshops, "_read_text", lambda p: reads.append(p.name) or real_read(p))
    assert workshops.load_course(root, "demo") is None
    assert workshops.list_courses(root) == []
    assert "course.json" not in reads, "a link is refused before it is opened"
    r = rig["admin"].get("/api/courses")
    assert r.json()["courses"] == [] and "LEAKED" not in r.text
    assert rig["admin"].get("/api/courses/demo/workshops/01-first/preview").status_code == 404
    if linked:
        (folder / "course.json").unlink()


def test_a_guide_over_two_megabytes_is_refused_before_it_is_read(rig, monkeypatch):
    root = rig["courses"]
    folder = _course(root, "demo")
    guide = _workshop(folder, "01-first", "## S\n\n1. Fine.\n") / "guide.md"
    with open(guide, "ab") as fh:
        fh.write(b"\n" + b"x" * workshops.MAX_GUIDE_BYTES)
    real_read_text = Path.read_text

    def guarded(self, *args, **kwargs):
        assert self.name != "guide.md", "the guide must not be read"
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    c = rig["admin"]
    r = c.get("/api/courses/demo/workshops/01-first/preview")
    assert r.status_code == 400 and "larger than 2 MB" in r.json()["detail"]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 400 and "larger than 2 MB" in r.json()["detail"]
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == []
    assert workshops.MAX_GUIDE_BYTES == 2 * 1024 * 1024
    r = c.get("/api/courses")
    assert r.status_code == 200 and r.json()["courses"][0]["sections"] == [], "the list stands; the workshop is left out of it"


def test_a_course_or_workshop_whose_files_cannot_be_read_is_left_out_of_the_list(rig, monkeypatch):
    """R4: a ``course.json`` or ``SUMMARY.md`` over the cap, not UTF-8 or not JSON leaves THAT course out of
    the list, a ``manifest.json`` or ``guide.md`` that is leaves THAT workshop out, each with a logged
    warning; the list answers the others with 200; the preview and the import of one left out answer 400
    with the reason, and nothing is written."""
    root = rig["courses"]
    over = b"x" * (workshops.MAX_GUIDE_BYTES + 1)
    folder = _course(root, "demo", summary="# T\n\n## Part\n\n* [Fine](01-fine/guide.md)\n* [Big](02-big/guide.md)\n* [Manifest](03-manifest/guide.md)\n* [Bytes](04-bytes/guide.md)\n")
    _workshop(folder, "01-fine", "## S\n\n1. Fine.\n")
    (_workshop(folder, "02-big", "## S\n\n1. Big.\n") / "guide.md").write_bytes(over)
    (_workshop(folder, "03-manifest", "## S\n\n1. Fine.\n") / "manifest.json").write_text("{not json", encoding="utf-8")
    (_workshop(folder, "04-bytes", "## S\n\n1. Fine.\n") / "guide.md").write_bytes(b"## S\n\n1. Not \xff\xfe UTF-8.\n")
    (_course(root, "big-summary") / "SUMMARY.md").write_bytes(over)
    _workshop(root / "big-summary", "01-first", "## S\n\n1. Fine.\n")
    (_course(root, "big-course") / "course.json").write_bytes(over)
    _workshop(root / "big-course", "01-first", "## S\n\n1. Fine.\n")
    (_course(root, "bytes-course") / "course.json").write_bytes(b'{"id": "\xff"}')
    _workshop(root / "bytes-course", "01-first", "## S\n\n1. Fine.\n")
    warned: list[str] = []
    monkeypatch.setattr(workshops.log, "warning", lambda msg, *args: warned.append(msg % args))
    c = rig["admin"]
    r = c.get("/api/courses")
    assert r.status_code == 200, r.text
    assert [(course["id"], [w["slug"] for s in course["sections"] for w in s["workshops"]]) for course in r.json()["courses"]] == [("demo", ["01-fine"])]
    assert "left_out" not in r.text, "the reasons are logged, never listed"
    for expected in (
        "Course big-course is left out of the list: course.json is larger than 2 MB",
        "Course big-summary is left out of the list: SUMMARY.md is larger than 2 MB",
        "Course bytes-course is left out of the list: course.json cannot be read as UTF-8 text.",
        "Course demo: workshop 02-big is left out of the list: 02-big/guide.md is larger than 2 MB",
        "Course demo: workshop 03-manifest is left out of the list: 03-manifest/manifest.json is not valid JSON.",
        "Course demo: workshop 04-bytes is left out of the list: 04-bytes/guide.md cannot be read as UTF-8 text.",
    ):
        assert any(expected in line for line in warned), (expected, warned)
    for course, slug, reason in (
        ("demo", "02-big", "demo/02-big/guide.md is larger than 2 MB"),
        ("demo", "03-manifest", "demo/03-manifest/manifest.json is not valid JSON."),
        ("demo", "04-bytes", "demo/04-bytes/guide.md cannot be read as UTF-8 text."),
        ("big-summary", "01-first", "big-summary/SUMMARY.md is larger than 2 MB"),
        ("big-course", "01-first", "big-course/course.json is larger than 2 MB"),
        ("bytes-course", "01-first", "bytes-course/course.json cannot be read as UTF-8 text."),
    ):
        r = c.get(f"/api/courses/{course}/workshops/{slug}/preview")
        assert r.status_code == 400 and reason in r.json()["detail"], (course, slug, r.text)
        r = c.post("/api/walkthroughs/import", json={"course": course, "slug": slug})
        assert r.status_code == 400 and reason in r.json()["detail"], (course, slug, r.text)
    assert c.get("/api/courses/demo/workshops/01-fine/preview").status_code == 200
    assert c.get("/api/courses/demo/workshops/05-nope/preview").status_code == 404, "not there is still 404"
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == []
    folder = rig["tmp"] / "walkthroughs"
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_workshop_folder_that_is_a_junction_elsewhere_is_not_a_workshop(rig, tmp_path, monkeypatch):
    """n4: the course is fine; one of its workshop folders is a junction to a folder outside the courses
    folder. The workshop is not listed, its preview and import are 404, and the outside guide is never read."""
    root = rig["courses"]
    folder = _course(root, "demo", summary="# T\n\n## Part\n\n* [First](01-first/guide.md)\n* [Linked](02-linked/guide.md)\n")
    _workshop(folder, "01-first", "## S\n\n1. Fine.\n")
    outside = tmp_path / "elsewhere" / "real"
    outside.mkdir(parents=True)
    (outside / "guide.md").write_text("## S\n\n1. LEAKED.\n", encoding="utf-8")
    if not _link_directory(folder / "02-linked", outside):
        pytest.skip("this account cannot link a directory")
    reads: list[Path] = []
    real_read = workshops._read_text
    monkeypatch.setattr(workshops, "_read_text", lambda p: reads.append(p) or real_read(p))
    try:
        course = workshops.load_course(root, "demo")
        assert [w["slug"] for s in course["sections"] for w in s["workshops"]] == ["01-first"] and course["left_out"] == {}
        c = rig["admin"]
        listed = c.get("/api/courses").json()["courses"]
        assert [w["slug"] for s in listed[0]["sections"] for w in s["workshops"]] == ["01-first"]
        r = c.get("/api/courses/demo/workshops/02-linked/preview")
        assert r.status_code == 404 and "LEAKED" not in r.text
        r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "02-linked"})
        assert r.status_code == 404 and "LEAKED" not in r.text
        assert reads and all("02-linked" not in str(p) and str(p).startswith(str(root)) for p in reads), reads
    finally:
        (folder / "02-linked").rmdir()


def test_a_missing_or_outside_image_imports_the_step_without_a_picture(rig, tmp_path):
    root = rig["courses"]
    folder = _course(root, "demo")
    (tmp_path / "secret.png").write_bytes(_png())
    guide = (
        "## S\n\n1. Has one.\n\n![a](../_assets/images/there.png)\n\n2. Missing.\n\n![b](../_assets/images/gone.png)\n\n"
        "3. Outside.\n\n![c](../../../secret.png)\n\n4. Not a png.\n\n![d](../_assets/images/bad.png)\n"
    )
    _workshop(folder, "01-first", guide, images={"there.png": _png((6, 4)), "bad.png": b"GIF89a not a png"})
    c = rig["admin"]
    preview = c.get("/api/courses/demo/workshops/01-first/preview").json()
    assert [s["picture"] for s in preview["steps"]] == [True, False, False, True], "the preview only knows the files are there"
    assert preview["counts"] == {"steps": 4, "chapters": 1, "pictures": 2, "to_capture": 2}
    assert "gone.png" in preview["message"] and "import without a picture" in preview["message"]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    rec = r.json()
    a, b, cc, d = rec["steps"]
    assert a["image"] == f"step-{a['id']}.png" and (a["width"], a["height"]) == (6, 4)
    assert a["source"] == {"course": "demo", "slug": "01-first", "image": "_assets/images/there.png", "alt": "a", "launcher": None}
    for step in (b, cc, d):
        assert step["image"] is None and step["width"] == 0 and step["height"] == 0 and step["hotspot"] is None
        assert step["source"]["image"] is None
    assert sorted(p.name for p in (rig["tmp"] / "walkthroughs" / rec["id"]).iterdir()) == [a["image"], "walkthrough.json"], "no copy of the bad PNG is left behind"
    assert c.get(f"/api/walkthroughs/{rec['id']}/steps/{b['id']}/image").status_code == 404
    assert c.get("/api/walkthroughs").json()["walkthroughs"][0]["first_step_id"] == a["id"]


def test_a_house_figure_previews_and_imports_its_picture_and_a_figure_outside_assets_does_not(rig):
    root = rig["courses"]
    folder = _course(root, "demo")
    guide = (
        '## S\n\n1. Click Run.\n\n<div align="center">\n<figure>\n'
        '<img src="../_assets/images/run.png" alt="The Run &amp; Preview dialog" width="800">\n'
        "<figcaption>Run</figcaption>\n</figure>\n</div>\n\n"
        '2. A picture among the lab files.\n\n<figure>\n<img src="../files/run.png" alt="Not in _assets">\n</figure>\n'
    )
    _workshop(folder, "01-first", guide, images={"run.png": _png((12, 7))})
    (folder / "files").mkdir()
    (folder / "files" / "run.png").write_bytes(_png((5, 5)))
    owner = rig["owner"]
    preview = owner.get("/api/courses/demo/workshops/01-first/preview").json()
    assert preview["counts"] == {"steps": 2, "chapters": 1, "pictures": 1, "to_capture": 1} and preview["message"] is None
    assert [(s["picture"], s["image"], s["alt"]) for s in preview["steps"]] == [
        (True, "run.png", "The Run & Preview dialog"), (False, None, "")]
    r = owner.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    run, among = r.json()["steps"]
    assert (run["image"], run["width"], run["height"]) == (f"step-{run['id']}.png", 12, 7)
    assert run["source"] == {"course": "demo", "slug": "01-first", "image": "_assets/images/run.png", "alt": "The Run & Preview dialog", "launcher": None}
    assert among["image"] is None and among["source"]["image"] is None, "a PNG in the course but outside _assets/images is no picture"


# -- the routes --------------------------------------------------------------------------------------

def test_the_list_says_where_to_set_the_folder_when_it_is_not_set(rig):
    c = rig["admin"]
    config._config["courses_folder"] = ""
    r = c.get("/api/courses")
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["courses"] == []
    assert "Settings" in r.json()["message"]
    assert c.get("/api/courses/demo/workshops/x/preview").status_code == 400
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "x"})
    assert r.status_code == 400 and "Settings" in r.json()["detail"]
    # A folder that has gone is the same as none.
    config._config["courses_folder"] = str(rig["tmp"] / "no-longer-there")
    assert c.get("/api/courses").json()["configured"] is False
    assert studio_settings.courses_folder() is None


def test_the_list_and_the_preview(rig):
    root = rig["courses"]
    folder = _course(root, "demo", summary="# T\n\n## Part one\n\n* [First](01-first/guide.md)\n* [Gone](02-gone/guide.md)\n\n## Part two\n\n* [Second](03-second/guide.md)\n")
    _workshop(folder, "01-first", "# The First\n\n## Open\n\n1. One.\n2. Two.\n\n![p](../_assets/images/p.png)\n", manifest={"title": "First Workshop", "estimatedMinutes": 7, "stepCount": 2, "description": "Desc"}, images={"p.png": _png()})
    _workshop(folder, "03-second", "## S\n\n1. Only.\n")
    (root / "not-a-course").mkdir()
    (root / "not-a-course" / "readme.md").write_text("no course.json", encoding="utf-8")
    (root / "broken").mkdir()
    (root / "broken" / "course.json").write_text("{not json", encoding="utf-8")
    c = rig["admin"]
    r = c.get("/api/courses")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True and body["message"] is None
    assert body["courses"] == [{
        "id": "demo", "title": "Demo course", "version": "1.2.3", "description": "A demo.",
        "sections": [
            {"title": "Part one", "workshops": [{"slug": "01-first", "title": "First Workshop", "description": "Desc", "estimatedMinutes": 7, "stepCount": 2}]},
            {"title": "Part two", "workshops": [{"slug": "03-second", "title": "Second", "description": "", "estimatedMinutes": None, "stepCount": None}]},
        ],
    }], "the SUMMARY order; a workshop with no guide left out; a folder without course.json, or with a broken one, not a course"
    r = c.get("/api/courses/demo/workshops/01-first/preview")
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["course"] == {"id": "demo", "title": "Demo course", "version": "1.2.3"}
    assert preview["workshop"] == {"slug": "01-first", "title": "First Workshop", "section": "Part one", "estimatedMinutes": 7, "stepCount": 2}
    assert preview["name"] == "First Workshop" and preview["chapters"] == ["Open"]
    assert preview["steps"] == [
        {"number": 1, "chapter": "Open", "instruction": "One.", "text": "One.", "picture": False, "image": None, "alt": "", "launcher": None, "note": False, "in_chapter": "Open"},
        {"number": 2, "chapter": "", "instruction": "Two.", "text": "Two.", "picture": True, "image": "p.png", "alt": "p", "launcher": None, "note": False, "in_chapter": "Open"},
    ]
    assert preview["counts"] == {"steps": 2, "chapters": 1, "pictures": 1, "to_capture": 1} and preview["message"] is None
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == [], "a preview writes nothing"
    assert c.get("/api/courses/demo/workshops/02-gone/preview").status_code == 404
    assert c.get("/api/courses/nope/workshops/01-first/preview").status_code == 404
    # Every route needs a session, and the browser edition (another machine) has them.
    anonymous = TestClient(rig["app"], client=LOOPBACK)
    assert anonymous.get("/api/courses").status_code == 401
    assert anonymous.get("/api/courses/demo/workshops/01-first/preview").status_code == 401
    assert anonymous.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"}).status_code == 401
    remote = TestClient(rig["app"], client=("10.20.30.40", 50123))
    assert remote.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
    assert remote.get("/api/courses").status_code == 200
    assert remote.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"}).status_code == 200


def test_the_import_makes_the_walkthrough_from_the_parse(rig):
    root = rig["courses"]
    folder = _course(root, "demo", title="Demo course")
    blue = _png((10, 6), (0, 0, 255))
    guide = (
        "# Guide Title\n\n## Open it\n\n<button data-launch=\"spoon\" data-path=\"files/win.ktr\">Open in PDI</button>\n\n"
        "1. **Click** `Run`.\n\n![alt text](../_assets/images/blue.png)\n\n> **Under the hood:**\n>\n> Why it ran.\n\n## Next\n\n1. Done.\n"
    )
    _workshop(folder, "01-first", guide, manifest={"title": "The Workshop", "estimatedMinutes": 3, "stepCount": 2}, images={"blue.png": blue})
    owner = rig["owner"]
    r = owner.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["name"] == "The Workshop" and rec["revision"] == 1 and rec["version"] == 1
    assert rec["source"] == {"course": "demo", "slug": "01-first", "title": "Demo course", "version": "1.2.3",
                             "workshops": [{"slug": "01-first", "title": "The Workshop"}]}
    assert rec["owner_name"] == "Olive Owner"
    launcher, click, done = rec["steps"]
    assert launcher["chapter"] == "Open it" and launcher["instruction"] == "Open win.ktr in Pentaho Data Integration." and launcher["image"] is None
    assert launcher["source"] == {"course": "demo", "slug": "01-first", "image": None, "alt": "", "launcher": "files/win.ktr"}
    assert click == {
        "id": click["id"], "image": f"step-{click['id']}.png", "width": 10, "height": 6, "hotspot": None, "kind": "tooltip",
        "text": "Click Run.", "instruction": "Click Run.", "placement": "auto", "chapter": "", "note": "Why it ran.",
        "source": {"course": "demo", "slug": "01-first", "image": "_assets/images/blue.png", "alt": "alt text", "launcher": None},
        # W2c: where in the guide the step is - its section (no number) and its first sentence, the first with that key.
        "anchor": {"key": "open it :: click run.", "ordinal": 1},
    }
    assert launcher["anchor"] == {"key": "open it :: open win.ktr in pentaho data integration.", "ordinal": 1}
    assert done["chapter"] == "Next" and done["instruction"] == "Done." and done["note"] == ""
    # The picture is a COPY of the course's PNG, through the one copy routine, served by the step route.
    copied = rig["tmp"] / "walkthroughs" / rec["id"] / click["image"]
    assert copied.read_bytes() == blue
    img = owner.get(f"/api/walkthroughs/{rec['id']}/steps/{click['id']}/image")
    assert img.status_code == 200 and Image.open(io.BytesIO(img.content)).convert("RGB").getpixel((0, 0)) == (0, 0, 255)
    assert owner.get(f"/api/walkthroughs/{rec['id']}").json() == rec
    # Deleting the course's image afterwards changes nothing.
    (folder / "_assets" / "images" / "blue.png").unlink()
    assert owner.get(f"/api/walkthroughs/{rec['id']}/steps/{click['id']}/image").status_code == 200
    # The record round-trips through the builder's save as returned: the null image, the note, the workshop source.
    body = {"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": rec["steps"]}
    r = owner.put(f"/api/walkthroughs/{rec['id']}", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert r.json()["steps"][0]["image"] is None and r.json()["steps"][1]["note"] == "Why it ran."
    # The transcript is unchanged by the note: the instruction, else the text; never the note.
    assert owner.get(f"/api/walkthroughs/{rec['id']}/transcript").text == "# The Workshop\n\n## Open it\n\n1. Open win.ktr in Pentaho Data Integration.\n2. Click Run.\n\n## Next\n\n3. Done.\n"
    assert "Why it ran" not in owner.get(f"/api/walkthroughs/{rec['id']}/transcript").text
    # The audit row: the course and the slug with the counts, never a step's text.
    rows = auth_store.list_audit(action="walkthrough.import")
    assert rows[0]["entity_id"] == rec["id"] and rows[0]["detail"] == "demo/01-first: 3 steps, 2 chapters, 1 picture"
    assert "Click Run" not in json.dumps(rows)
    # The walkthrough is the importer's: another editor is refused, an administrator is not; the list is theirs.
    assert rig["other"].get(f"/api/walkthroughs/{rec['id']}").status_code == 403
    assert rig["admin"].get(f"/api/walkthroughs/{rec['id']}").status_code == 200
    assert [w["id"] for w in owner.get("/api/walkthroughs").json()["walkthroughs"]] == [rec["id"]]
    assert owner.get("/api/walkthroughs").json()["walkthroughs"][0]["first_step_id"] == click["id"], "the thumbnail is the first step WITH a picture"
    # A name of the caller's own, and a duplicate keeps the pictureless steps and the source.
    named = owner.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first", "name": "  My name "}).json()
    assert named["name"] == "My name"
    copy = owner.post(f"/api/walkthroughs/{rec['id']}/duplicate").json()
    assert len(copy["steps"]) == 3 and copy["steps"][0]["image"] is None and copy["source"] == rec["source"]
    assert owner.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first", "extra": 1}).status_code == 422
    assert owner.post("/api/walkthroughs/import", json={"course": "demo"}).status_code == 422


def test_control_characters_and_long_paths_are_stripped_and_bounded_so_the_import_saves(rig):
    """R3, ONE rule in ONE place: the parser strips the control characters the save refuses (the code lines
    too) and bounds every field by the schema's own constants, so the imported record saves (200) as
    returned: a form feed in a code block, a 500-character ``data-path``, an alt text over 2,000, an image
    path over 400."""
    root = rig["courses"]
    folder = _course(root, "demo")
    long_path = "files/" + "p" * 494  # 500 characters
    long_image = "../_assets/images/" + "i" * 400 + ".png"  # over MAX_SOURCE_PATH_CHARS
    guide = (
        "## Chap\x07ter\n\n"
        f"<button data-launch=\"spoon\" data-path=\"{long_path}\">Open in PDI</button>\n\n"
        "1. Type \x1bthis:\n\n```sql\nSELECT\x0c 1;\n\x00DROP x;\n```\n\n* a bul\x08let\n\n"
        f"![{'a' * 2500}](../_assets/images/p.png)\n\n"
        "> **Under the hood:**\n>\n> Why\x0c it ran.\n\n"
        f"2. Long picture.\n\n![far]({long_image})\n"
    )
    _workshop(folder, "01-first", guide, images={"p.png": _png()})
    owner = rig["owner"]
    r = owner.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    rec = r.json()
    launcher, typed, far = rec["steps"]
    assert launcher["chapter"] == "Chapter"
    assert launcher["instruction"] == f"Open {'p' * 494} in Pentaho Data Integration." and len(launcher["text"]) <= workshops.TOOLTIP_CHARS
    assert launcher["source"]["launcher"] is None, "a path over MAX_SOURCE_PATH_CHARS is no real path: the step keeps the instruction, not the path"
    assert typed["instruction"] == "Type this:\n\n```\nSELECT 1;\nDROP x;\n```\n\na bullet" and typed["text"] == "Type this:"
    assert typed["note"] == "Why it ran."
    assert typed["source"] == {"course": "demo", "slug": "01-first", "image": "_assets/images/p.png", "alt": "a" * walkthroughs.MAX_ALT_CHARS, "launcher": None}
    assert far["instruction"] == "Long picture." and far["image"] is None and far["source"]["image"] is None
    for step in rec["steps"]:
        for field in ("chapter", "instruction", "text", "note"):
            assert not walkthroughs.TEXT_CONTROL_RE.search(step[field]), (field, step[field])
    # The record saves as returned: the builder's PUT is the schema's own validation.
    body = {"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": rec["steps"]}
    r = owner.put(f"/api/walkthroughs/{rec['id']}", content=json.dumps(body), headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    # The schema's bounds ARE the constants the parser reads - not copies.
    from api.schemas import WorkshopSourceIn

    assert walkthroughs.MAX_SOURCE_PATH_CHARS == 400 and walkthroughs.MAX_ALT_CHARS == 2000
    WorkshopSourceIn.model_validate({"course": "c", "slug": "s", "image": "i" * 400, "alt": "a" * 2000, "launcher": "p" * 400})
    for bad in ({"launcher": "p" * 401}, {"image": "i" * 401}, {"alt": "a" * 2001}):
        with pytest.raises(ValueError):
            WorkshopSourceIn.model_validate({"course": "c", "slug": "s", "image": None, "alt": "", "launcher": None, **bad})
    assert workshops.source_path("p" * 400) == "p" * 400 and workshops.source_path("p" * 401) is None
    assert workshops.source_path("a\x00b") == "ab" and workshops.source_path("") is None
    assert workshops.image_relative_path("s", "../_assets/images/" + "i" * 400 + ".png") is None
    assert workshops.image_relative_path("s", "../_assets/images/a\x0c.png") is None


def test_the_belt_refuses_a_plan_the_save_would_refuse_and_writes_nothing(rig, monkeypatch):
    """R3's belt: ``create_from_workshop`` validates the planned record with the save's own model
    (``WalkthroughIn``) before it writes; a plan the parser should never make is a ValueError naming the
    reason - the route's 400 - with no folder left behind."""
    planned = {"chapter": "", "instruction": "Press\x00 it.", "text": "Press it.", "note": "", "picture": None,
               "source": {"course": "demo", "slug": "01-first", "image": None, "alt": "", "launcher": None}}
    source = {"course": "demo", "slug": "01-first", "title": "Demo course", "version": "1.2.3"}
    with pytest.raises(ValueError, match="not valid.*control"):
        walkthroughs.create_from_workshop("Name", owner_id="u1", owner_name="U", source=source, steps=[planned])
    too_long = dict(planned, instruction="x" * (walkthroughs.MAX_INSTRUCTION_CHARS + 1))
    with pytest.raises(ValueError, match="not valid"):
        walkthroughs.create_from_workshop("Name", owner_id="u1", owner_name="U", source=source, steps=[too_long])
    folder = rig["tmp"] / "walkthroughs"
    assert not folder.exists() or list(folder.iterdir()) == []
    # Through the route, the plan stood in for (the parser never makes one): 400 with the reason, nothing written.
    _workshop(_course(rig["courses"], "demo"), "01-first", "## S\n\n1. Fine.\n")
    monkeypatch.setattr(workshops, "import_plan", lambda *a, **k: [planned])
    r = rig["owner"].post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 400 and "not valid" in r.json()["detail"] and "control" in r.json()["detail"], r.text
    assert rig["owner"].get("/api/walkthroughs").json()["walkthroughs"] == []
    assert not folder.exists() or list(folder.iterdir()) == []


def test_a_code_block_keeps_its_fences_through_the_parse_the_import_and_the_transcript(rig):
    """R6: a code block stays in the instruction BETWEEN its ``` fences (the narration round skips what is
    between them); a block that does not fit the instruction's cap is left out whole - fences paired - and
    nothing follows it, where a paragraph is cut after its last whole sentence."""
    step = _parse("## S\n\n1. Type this:\n\n```sql\nSELECT 1;\n```\n\nThen run it.\n").steps[0]
    assert step.instruction == "Type this:\n\n```\nSELECT 1;\n```\n\nThen run it." and step.text == "Type this:"
    prose = ("A sentence of words. " * 90).strip()  # 1,889 characters
    code = "```\n" + "x" * 300 + "\n```"
    assert workshops.join_within([prose, code, "After."], walkthroughs.MAX_INSTRUCTION_CHARS) == prose
    assert workshops.join_within(["Short.", code, "After."], walkthroughs.MAX_INSTRUCTION_CHARS) == f"Short.\n\n{code}\n\nAfter."
    assert workshops.join_within(["Short.", code], 8) == "Short."
    assert workshops.join_within(["One. Two.", "Three. Four.", "Five."], 18) == "One. Two.\n\nThree."
    assert workshops.join_within(["", "A.", ""], 10) == "A."
    capped = _parse("## S\n\n1. " + prose + "\n\n" + code + "\n\nAfter.\n").steps[0]
    assert capped.instruction == prose, "the block that does not fit is left out whole; nothing follows it"
    # Through the import and the transcript: the fences travel with the instruction, a line under the number each.
    rec = _imported(rig, "## S\n\n1. Type this:\n\n```sql\nSELECT 1;\n```\n\n2. Done.\n")
    assert rec["steps"][0]["instruction"] == "Type this:\n\n```\nSELECT 1;\n```"
    text = rig["admin"].get(f"/api/walkthroughs/{rec['id']}/transcript").text
    assert text == "# First\n\n## S\n\n1. Type this:\n   \n   ```\n   SELECT 1;\n   ```\n2. Done.\n"


def test_an_empty_workshop_imports_as_an_empty_walkthrough_and_the_cap_refuses(rig, monkeypatch):
    root = rig["courses"]
    folder = _course(root, "demo", summary="# T\n\n## Part\n\n* [First](01-first/guide.md)\n* [Many](02-many/guide.md)\n")
    _workshop(folder, "01-first", "# Empty\n\nJust prose.\n\n## Troubleshooting\n\n1. Not a step.\n")
    _workshop(folder, "02-many", "## S\n\n" + "".join(f"{i}. Step {i}.\n" for i in range(1, 6)))
    c = rig["admin"]
    preview = c.get("/api/courses/demo/workshops/01-first/preview").json()
    assert preview["counts"]["steps"] == 0 and "No steps were found" in preview["message"]
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == [] and r.json()["name"] == "First"
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 4)
    r = c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "02-many"})
    assert r.status_code == 400 and r.json()["detail"] == "The workshop has more steps than a walkthrough holds (5 of 4)."
    assert len(c.get("/api/walkthroughs").json()["walkthroughs"]) == 1, "nothing written past the cap"
    assert len(list((rig["tmp"] / "walkthroughs").iterdir())) == 1
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 5)
    assert c.post("/api/walkthroughs/import", json={"course": "demo", "slug": "02-many"}).status_code == 200


# -- the setting -------------------------------------------------------------------------------------

def test_the_courses_folder_setting_is_admin_only_and_validated(rig, tmp_path):
    admin, other = rig["admin"], rig["other"]
    good = tmp_path / "my-courses"
    good.mkdir()
    assert admin.get("/api/settings/studio").json()["settings"]["courses_folder"] == str(rig["courses"])
    assert other.put("/api/settings/studio", json={"courses_folder": str(good)}).status_code == 403
    assert config._config["courses_folder"] == str(rig["courses"])
    for bad, said in (("relative/courses", "absolute"), (str(tmp_path / "missing"), "not found"), (str(tmp_path / "missing" / "deeper"), "not found")):
        r = admin.put("/api/settings/studio", json={"courses_folder": bad})
        assert r.status_code == 400 and said in r.json()["detail"], f"{bad}: {r.text}"
        assert config._config["courses_folder"] == str(rig["courses"]), "refused as a whole"
    a_file = tmp_path / "a-file.txt"
    a_file.write_text("x", encoding="utf-8")
    assert admin.put("/api/settings/studio", json={"courses_folder": str(a_file)}).status_code == 400
    assert admin.put("/api/settings/studio", json={"courses_folder": 5}).status_code == 422
    r = admin.put("/api/settings/studio", json={"courses_folder": f"  {good}  "})
    assert r.status_code == 200 and r.json()["settings"]["courses_folder"] == str(good)
    assert studio_settings.courses_folder() == good and not any(good.iterdir()), "nothing is created in it"
    r = admin.put("/api/settings/studio", json={"courses_folder": ""})
    assert r.status_code == 200 and r.json()["settings"]["courses_folder"] == "" and studio_settings.courses_folder() is None
    assert admin.get("/api/courses").json()["configured"] is False
    rows = auth_store.list_audit(action="settings.update")
    assert rows[0]["detail"] == "courses_folder" and str(good) not in json.dumps(rows)


# -- the document model with a step that has no picture, the hotspot on a replaced picture, the guided capture row --

def _fake_grab(colour=(200, 30, 30)):
    def grab(x, y, width, height, *, cursor, out_dir):
        capture.validate_region(x, y, width, height)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        out = Path(out_dir) / f"still-fake-{width}x{height}.png"
        Image.new("RGB", (width, height), colour).save(out)
        return out
    return grab


def _still(rig, monkeypatch, width, height, who="admin") -> str:
    monkeypatch.setattr(captures.capture, "take_still", _fake_grab())
    r = rig[who].post("/api/captures", json={"x": 0, "y": 0, "width": width, "height": height, "name": "Still"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _imported(rig, guide="## S\n\n1. One.\n2. Two.\n") -> dict:
    folder = _course(rig["courses"], "demo")
    _workshop(folder, "01-first", guide)
    r = rig["admin"].post("/api/walkthroughs/import", json={"course": "demo", "slug": "01-first"})
    assert r.status_code == 200, r.text
    return r.json()


def _put(c, wid, body):
    return c.put(f"/api/walkthroughs/{wid}", content=json.dumps(body), headers={"Content-Type": "application/json"})


def _body(rec, **changes):
    out = {"version": 1, "name": rec["name"], "revision": rec["revision"], "steps": [dict(s) for s in rec["steps"]]}
    out.update(changes)
    return out


def test_a_step_without_a_picture_is_refused_a_size_or_a_hotspot_and_a_note_is_bounded(rig):
    c = rig["admin"]
    rec = _imported(rig)
    one = rec["steps"][0]
    cases = [
        ("a size without a picture", {**one, "width": 10}, "0 x 0"),
        ("a hotspot without a picture", {**one, "hotspot": {"x": 0, "y": 0, "width": 10, "height": 10}}, "cannot have a hotspot"),
        ("a note too long", {**one, "note": "n" * (walkthroughs.MAX_NOTE_CHARS + 1)}, "note"),
        ("a note with a control character", {**one, "note": "a\x00b"}, "control"),
        ("a bad workshop source", {**one, "source": {"course": "demo", "slug": "x", "image": None, "alt": "", "launcher": None, "extra": 1}}, "source"),
        ("a picture name without a file", {**one, "image": f"step-{one['id']}.png", "width": 1, "height": 1}, "does not hold"),
    ]
    for label, step, said in cases:
        r = _put(c, rec["id"], _body(rec, steps=[step, rec["steps"][1]]))
        assert r.status_code == 400, f"{label}: {r.status_code} {r.text}"
        assert said.lower() in r.json()["detail"].lower(), f"{label}: {r.text}"
    assert c.get(f"/api/walkthroughs/{rec['id']}").json()["revision"] == 1, "nothing written"
    noted = {**one, "note": "A note\r\nwith lines"}
    r = _put(c, rec["id"], _body(rec, steps=[noted, rec["steps"][1]]))
    assert r.status_code == 200 and r.json()["steps"][0]["note"] == "A note\nwith lines"
    assert "note" not in c.get("/api/walkthroughs").json()["walkthroughs"][0], "the list carries no notes"
    # A record from before the note existed reads as "".
    folder = rig["tmp"] / "walkthroughs" / rec["id"]
    stored = json.loads((folder / "walkthrough.json").read_text(encoding="utf-8"))
    for s in stored["steps"]:
        s.pop("note", None)
    (folder / "walkthrough.json").write_text(json.dumps(stored), encoding="utf-8")
    assert all(s["note"] == "" for s in c.get(f"/api/walkthroughs/{rec['id']}").json()["steps"])


def test_the_first_picture_of_a_pictureless_step_and_the_hotspot_rule(rig, monkeypatch):
    c = rig["admin"]
    rec = _imported(rig)
    sid = rec["steps"][0]["id"]
    cid = _still(rig, monkeypatch, 400, 300)
    # The guided capture: the picture AND the hotspot, clamped into it.
    r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json={"capture_id": cid, "hotspot": {"x": 350, "y": 280, "width": 160, "height": 60}})
    assert r.status_code == 200, r.text
    step = r.json()["steps"][0]
    assert step["image"] == f"step-{sid}.png" and (step["width"], step["height"]) == (400, 300)
    assert step["hotspot"] == {"x": 240, "y": 240, "width": 160, "height": 60}, "moved in to fit"
    # W2c (C1): the imported step keeps where it came from, the capture merged in, the guide's image gone.
    assert step["source"] == {"course": "demo", "slug": "01-first", "image": None, "alt": "", "launcher": None, "capture_id": cid, "annotated": False}
    assert step["anchor"] == {"key": "s :: one.", "ordinal": 1}
    assert c.get(f"/api/walkthroughs/{rec['id']}/steps/{sid}/image").status_code == 200
    # Left out: the step's own hotspot is kept; null: removed.
    r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json={"capture_id": cid})
    assert r.json()["steps"][0]["hotspot"] == {"x": 240, "y": 240, "width": 160, "height": 60}
    r = c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json={"capture_id": cid, "hotspot": None})
    assert r.status_code == 200 and r.json()["steps"][0]["hotspot"] is None
    assert c.post(f"/api/walkthroughs/{rec['id']}/steps/{sid}/picture", json={"capture_id": cid, "hotspot": {"x": 0, "y": 0, "width": 2, "height": 2}}).status_code == 422
    rows = auth_store.list_audit(action="walkthrough.replace_picture")
    assert [row["detail"] for row in rows] == [f"step {sid}: capture {cid}, no hotspot", f"step {sid}: capture {cid}", f"step {sid}: capture {cid}, hotspot set"]
    # The transcript and the list: the step now has a picture; the other still has none, and the list's thumbnail is the first with one.
    assert c.get("/api/walkthroughs").json()["walkthroughs"][0]["first_step_id"] == sid


def test_the_guided_capture_audit_route(rig):
    admin, other = rig["admin"], rig["other"]
    rec = _imported(rig)
    wid = rec["id"]
    r = admin.post(f"/api/walkthroughs/{wid}/guided-capture", json={"event": "start", "kind": "window", "cursor": True})
    assert r.status_code == 204, r.text
    assert admin.post(f"/api/walkthroughs/{wid}/guided-capture", json={"event": "done", "captured": 3, "skipped": 1}).status_code == 204
    rows = auth_store.list_audit(action="walkthrough.guided_capture")
    assert [(row["entity_id"], row["detail"]) for row in rows] == [(wid, "done: 3 captured, 1 skipped"), (wid, "start: window with pointer")]
    assert admin.post(f"/api/walkthroughs/{wid}/guided-capture", json={"event": "start"}).status_code == 204
    assert auth_store.list_audit(action="walkthrough.guided_capture")[0]["detail"] == "start: unknown", "no kind given: no kind guessed"
    assert admin.post(f"/api/walkthroughs/{wid}/guided-capture", json={"event": "pause"}).status_code == 422
    assert admin.post(f"/api/walkthroughs/{wid}/guided-capture", json={"event": "done", "captured": -1}).status_code == 422
    assert other.post(f"/api/walkthroughs/{wid}/guided-capture", json={"event": "start"}).status_code == 403
    assert admin.post("/api/walkthroughs/aabbccddeeff/guided-capture", json={"event": "start"}).status_code == 404
    assert admin.get(f"/api/walkthroughs/{wid}").json()["revision"] == 1, "the row writes nothing to the walkthrough"


def test_the_guided_capture_rows_say_on_click_and_count_the_captures(rig):
    """W2b: the start row says ", on click" when every click is a step; the done row counts the DISTINCT steps
    pictured, adds the captures made when they differ (a retake after Back) and how many were by click. A page
    before W2b sends none of these and its rows read as they did (the test above)."""
    from api.routers.walkthroughs import guided_done_detail, guided_start_detail

    admin = rig["admin"]
    wid = _imported(rig)["id"]
    url = f"/api/walkthroughs/{wid}/guided-capture"
    assert admin.post(url, json={"event": "start", "kind": "region", "cursor": False, "clicks": True}).status_code == 204
    assert admin.post(url, json={"event": "start", "kind": "window", "cursor": True, "clicks": False}).status_code == 204
    assert admin.post(url, json={"event": "start", "kind": "screen", "cursor": True, "clicks": True}).status_code == 204
    assert admin.post(url, json={"event": "done", "captured": 3, "skipped": 1, "captures": 3, "by_click": 2}).status_code == 204
    assert admin.post(url, json={"event": "done", "captured": 1, "skipped": 0, "captures": 2, "by_click": 2}).status_code == 204
    assert admin.post(url, json={"event": "done", "captured": 2, "skipped": 0, "captures": 2}).status_code == 204
    rows = [row["detail"] for row in auth_store.list_audit(action="walkthrough.guided_capture")]
    assert rows == [
        "done: 2 captured, 0 skipped",
        "done: 1 captured, 2 captures (2 by click), 0 skipped",
        "done: 3 captured (2 by click), 1 skipped",
        "start: screen with pointer, on click",
        "start: window with pointer",
        "start: region, on click",
    ]
    assert admin.post(url, json={"event": "done", "captured": 1, "by_click": -1}).status_code == 422
    assert admin.post(url, json={"event": "done", "captured": 1, "captures": 10_001}).status_code == 422
    assert admin.post(url, json={"event": "start", "clicks": "yes"}).status_code == 422
    # The two rules, pure.
    assert guided_start_detail(None, False, None) == "start: unknown"
    assert guided_start_detail("window", True, True) == "start: window with pointer, on click"
    assert guided_done_detail(3, 1, None, None) == "done: 3 captured, 1 skipped"
    assert guided_done_detail(3, 1, 3, 0) == "done: 3 captured (0 by click), 1 skipped"
    assert guided_done_detail(0, 2, 0, 0) == "done: 0 captured (0 by click), 2 skipped"
    assert guided_done_detail(3, 1, 5, None) == "done: 3 captured, 5 captures, 1 skipped"


def test_a_window_still_of_another_size_is_drawn_centred_on_the_first_stills_frame(rig, monkeypatch):
    """W2b, R7 as the fix rounds left it (F6, F7, G5): with ``frame`` (the run's frame - the widest width x the
    tallest height of its stills so far, as the page keeps it) a smaller still is drawn centred on a TRANSPARENT
    canvas of that size (alpha 0 outside it, the still opaque) and its hotspot moves with it; a still that does not
    fit the frame is refused with nothing written (the page never sends one: a larger still grows the frame first);
    one of the frame's size is left as it is. The placement is pinned."""
    c = rig["admin"]
    rec = _imported(rig)
    wid, sid = rec["id"], rec["steps"][1]["id"]
    folder = rig["tmp"] / "walkthroughs" / wid
    frame = {"width": 800, "height": 600}
    red = (200, 30, 30, 255)
    clear = (0, 0, 0, 0)
    # A smaller still (a dialog) on the frame: 400 x 300 centred at (200, 150); outside it the canvas is transparent.
    small = _still(rig, monkeypatch, 400, 300)
    r = c.post(f"/api/walkthroughs/{wid}/steps/{sid}/picture", json={"capture_id": small, "hotspot": {"x": 0, "y": 0, "width": 160, "height": 60}, "frame": frame})
    assert r.status_code == 200, r.text
    step = r.json()["steps"][1]
    assert (step["width"], step["height"]) == (800, 600)
    assert step["hotspot"] == {"x": 200, "y": 150, "width": 160, "height": 60}, "moved with the still"
    with Image.open(folder / f"step-{sid}.png") as img:
        assert img.mode == "RGBA"
        picture = img.convert("RGBA")
    assert picture.size == (800, 600)
    assert [picture.getpixel(p) for p in [(0, 0), (799, 599), (199, 150), (200, 149), (600, 450)]] == [clear] * 5, "transparent outside the still"
    assert [picture.getpixel(p) for p in [(200, 150), (599, 449), (400, 300)]] == [red] * 3, "the still itself opaque"
    # A still that does not fit the frame is refused, nothing written (F7: the page attaches it at its own size instead).
    big = _still(rig, monkeypatch, 1600, 600)
    before = c.get(f"/api/walkthroughs/{wid}").json()["revision"]
    r = c.post(f"/api/walkthroughs/{wid}/steps/{sid}/picture", json={"capture_id": big, "hotspot": {"x": 800, "y": 300, "width": 160, "height": 60}, "frame": frame})
    assert r.status_code == 400 and "smaller than the still" in r.json()["detail"], r.text
    assert c.get(f"/api/walkthroughs/{wid}").json()["revision"] == before
    assert sorted(p.name for p in folder.iterdir()) == [f"step-{sid}.png", "walkthrough.json"], "no temporary file left"
    with Image.open(folder / f"step-{sid}.png") as img:
        assert img.size == (800, 600) and img.convert("RGBA").getpixel((400, 300)) == red, "the step's picture as it was"
    # The frame's own size: the still as it is, nothing padded, opaque to the corner; no hotspot given keeps the step's own.
    same = _still(rig, monkeypatch, 800, 600)
    r = c.post(f"/api/walkthroughs/{wid}/steps/{sid}/picture", json={"capture_id": same, "frame": frame})
    assert r.status_code == 200 and r.json()["steps"][1]["hotspot"] == {"x": 200, "y": 150, "width": 160, "height": 60}
    with Image.open(folder / f"step-{sid}.png") as img:
        assert img.convert("RGBA").getpixel((0, 0)) == red
    # Without a frame, a still of another size is its own size, as before W2b.
    r = c.post(f"/api/walkthroughs/{wid}/steps/{sid}/picture", json={"capture_id": small, "hotspot": None})
    assert (r.json()["steps"][1]["width"], r.json()["steps"][1]["height"]) == (400, 300)
    # The rows say when a frame was asked for.
    rows = [row["detail"] for row in auth_store.list_audit(action="walkthrough.replace_picture")]
    assert rows[0] == f"step {sid}: capture {small}, no hotspot"
    assert rows[1] == f"step {sid}: capture {same}, framed 800x600"
    assert rows[2] == f"step {sid}: capture {small}, hotspot set, framed 800x600"
    # The placement, pure: centred by whole pixels, never scaled; a still that does not fit is refused.
    assert walkthroughs.place_in_frame(400, 300, 800, 600) == (200, 150)
    assert walkthroughs.place_in_frame(800, 600, 800, 600) == (0, 0)
    assert walkthroughs.place_in_frame(799, 599, 800, 600) == (0, 0)
    with pytest.raises(ValueError):
        walkthroughs.place_in_frame(1600, 600, 800, 600)
    with pytest.raises(ValueError):
        walkthroughs.place_in_frame(800, 601, 800, 600)
    assert walkthroughs.hotspot_in_frame({"x": 10, "y": 20, "width": 160, "height": 60}, (200, 150)) == {"x": 210, "y": 170, "width": 160, "height": 60}
    assert walkthroughs.hotspot_in_frame(None, (200, 150)) is None
    assert walkthroughs.hotspot_in_frame({"x": 1, "y": 2, "width": 8, "height": 8}, None) == {"x": 1, "y": 2, "width": 8, "height": 8}
    assert not hasattr(walkthroughs, "FRAME_BACKGROUND"), "the padding is transparent: no colour to name"


def test_a_frame_is_bounded_by_pixels_not_by_a_side(rig, monkeypatch):
    """W2b fix round, F2: a frame of more than ``MAX_FRAME_PIXELS`` (the annotation decode's own cap, 100 M) is
    refused with the reason and nothing written, whatever its sides; one AT the cap is drawn; a side outside
    1..``MAX_FRAME_SIDE`` is refused as before. The rule is one pure function, read by the request model."""
    c = rig["admin"]
    rec = _imported(rig)
    wid, sid = rec["id"], rec["steps"][1]["id"]
    folder = rig["tmp"] / "walkthroughs" / wid
    tiny = _still(rig, monkeypatch, 8, 8)
    url = f"/api/walkthroughs/{wid}/steps/{sid}/picture"
    assert walkthroughs.MAX_FRAME_PIXELS == captures.MAX_ANNOTATE_PIXELS == 100_000_000
    # Over the cap by one pixel, with sides well inside the per-side bound: refused, readable, nothing written.
    over = {"width": 10_001, "height": 10_000}
    r = c.post(url, json={"capture_id": tiny, "frame": over})
    assert r.status_code == 422, r.text
    assert "at most 100,000,000 pixels" in r.text and "10001 x 10000 is 100,010,000" in r.text
    assert c.get(f"/api/walkthroughs/{wid}").json()["revision"] == 1
    assert sorted(p.name for p in folder.iterdir()) == ["walkthrough.json"]
    # The old shape of the hole: 32,000 x 32,000 is inside the per-side bound and a 1,024 M-pixel canvas - refused now.
    assert c.post(url, json={"capture_id": tiny, "frame": {"width": walkthroughs.MAX_FRAME_SIDE, "height": walkthroughs.MAX_FRAME_SIDE}}).status_code == 422
    # A side outside the bound: refused as before.
    assert c.post(url, json={"capture_id": tiny, "frame": {"width": 0, "height": 600}}).status_code == 422
    assert c.post(url, json={"capture_id": tiny, "frame": {"width": 800, "height": walkthroughs.MAX_FRAME_SIDE + 1}}).status_code == 422
    # AT the cap: drawn - 100 M transparent pixels with the 8 x 8 still at the centre.
    at = {"width": 12_500, "height": 8_000}
    r = c.post(url, json={"capture_id": tiny, "hotspot": None, "frame": at})
    assert r.status_code == 200, r.text
    step = r.json()["steps"][1]
    assert (step["width"], step["height"]) == (12_500, 8_000)
    # Reading the picture back: Pillow's own bomb limit (89.5 M) is below the frame's cap, so this test reads it at the cap
    # (the server never opens the canvas - only the still it draws on it).
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", walkthroughs.MAX_FRAME_PIXELS)
    with Image.open(folder / f"step-{sid}.png") as img:
        assert img.size == (12_500, 8_000)
        rgba = img.convert("RGBA")
        assert rgba.getpixel((0, 0)) == (0, 0, 0, 0) and rgba.getpixel((6_246, 3_996)) == (200, 30, 30, 255)
    # The rule itself, pure.
    assert walkthroughs.frame_refusal(12_500, 8_000) is None
    assert walkthroughs.frame_refusal(1, 1) is None
    assert walkthroughs.frame_refusal(10_001, 10_000) == "A frame may hold at most 100,000,000 pixels: 10001 x 10000 is 100,010,000."
    assert walkthroughs.frame_refusal(32_000, 32_000) is not None
    assert walkthroughs.frame_refusal(0, 10) == "A frame's sides must be 1-32,000 pixels: 0 x 10 is not."
    assert walkthroughs.frame_refusal(10, 32_001) is not None


# -- the limits rows ---------------------------------------------------------------------------------

def test_the_limits_rows_name_the_workshop_constants():
    """The rows (``tests/test_docs.py`` checks every row's line references)."""
    text = LIMITS_MD.read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| Workshop") or line.startswith("| Walkthrough guided")}
    assert rows, "no Workshop rows in docs/reference/limits.md"
    # Each row names its number as the code holds it, and the constant behind it.
    expected = {
        "Workshop guide": (f"{workshops.MAX_GUIDE_BYTES // 1024 ** 2} MB", "MAX_GUIDE_BYTES"),
        "Workshop parse": (f"{workshops.MAX_PARSE_STEPS} steps", "MAX_PARSE_STEPS"),
        "Workshop step text": (f"{workshops.TOOLTIP_CHARS} characters", "TOOLTIP_CHARS"),
        "Workshop step note": (f"{walkthroughs.MAX_NOTE_CHARS:,} characters", "MAX_NOTE_CHARS"),
        "Workshop step source": (f"{walkthroughs.MAX_SOURCE_PATH_CHARS} characters", "MAX_SOURCE_PATH_CHARS"),
        "Walkthrough guided capture": ("160 × 60", "HOTSPOT_DEFAULT"),
    }
    for what, (number, name) in expected.items():
        assert what in rows, f"no '{what}' row in docs/reference/limits.md"
        assert number in rows[what], f"{what}: {number!r} not in the row"
        assert f"`{name}`" in rows[what], f"{what}: `{name}` not in the row"
    assert f"{walkthroughs.MAX_ALT_CHARS:,} of the figure's alt text (`MAX_ALT_CHARS`)" in rows["Workshop step source"]
    assert "`TEXT_CONTROL_RE`" in rows["Workshop step source"]
    assert "`MAX_GUIDE_BYTES`, `MAX_PARSE_STEPS` and `TOOLTIP_CHARS`" in text, "the constants sentence"
    # W2e: the workshops one import takes, and the walkthrough's own bound over their steps together.
    assert "Workshop import" in rows, "no 'Workshop import' row in docs/reference/limits.md"
    assert f"{workshops.MAX_WORKSHOPS} workshops" in rows["Workshop import"] and "`MAX_WORKSHOPS`" in rows["Workshop import"]
    assert f"{walkthroughs.MAX_STEPS}" in rows["Workshop import"] and "`MAX_STEPS`" in rows["Workshop import"]
    assert "`MAX_WORKSHOPS`" in text.split("The constants behind", 1)[1], "the constants sentence names it"


# -- W2e: several workshops, the guide's sections, the import body and the record --------------------

THREE_SUMMARY = "# T\n\n## Part one\n\n* [First](01-first/guide.md)\n* [Second](02-second/guide.md)\n\n## Part two\n\n* [Third](03-third/guide.md)\n"
FIRST_GUIDE = "# One\n\n1. Before any heading.\n\n## Open it\n\n1. Open.\n2. Click.\n\n## Troubleshooting a bad file\n\n1. Fix it.\n\n## Run it\n\n1. Run.\n"
IMPORT = "/api/walkthroughs/import"


def _three(rig) -> Path:
    """A course of three workshops in two sections: the first with a step before its first heading and a chapter to
    leave out, the second with a picture, the third one step."""
    folder = _course(rig["courses"], "demo", summary=THREE_SUMMARY)
    _workshop(folder, "01-first", FIRST_GUIDE, manifest={"title": "Your First Win", "estimatedMinutes": 10, "stepCount": 4})
    _workshop(folder, "02-second", "## Build\n\n1. Build it.\n\n![p](../_assets/images/p.png)\n\n2. Save it.\n",
              manifest={"title": "Build the Pipeline Yourself"}, images={"p.png": _png()})
    _workshop(folder, "03-third", "## Join\n\n1. Join.\n", manifest={"title": "Enrich and Join"})
    return folder


def test_the_chapter_filter_leaves_out_named_chapters_and_keeps_the_anchors_of_the_full_guide():
    """E2: a step is IN the chapter of the latest step at or before it that starts one ("" before the first heading);
    ``import_plan`` leaves out the steps of the chapters named - "" names the steps before the first heading, a name
    the guide no longer has is ignored - and the kept steps' anchors are those of the FULL guide (the export finds
    them by their place in the guide as it is)."""
    parsed = _parse(FIRST_GUIDE + "2. Run again.\n")
    assert workshops.running_chapters(parsed.steps) == ["", "Open it", "Open it", "Troubleshooting a bad file", "Run it", "Run it"]
    course = {"id": "demo", "title": "Demo", "version": "1", "launchers": {}}
    workshop = {"slug": "01-first", "title": "First"}
    full = workshops.import_plan(course, workshop, parsed, Path("."))
    assert [s["text"] for s in full] == ["Before any heading.", "Open.", "Click.", "Fix it.", "Run.", "Run again."]
    kept = workshops.import_plan(course, workshop, parsed, Path("."), skip_chapters=["Troubleshooting a bad file", "", "Not a chapter"])
    assert [s["text"] for s in kept] == ["Open.", "Click.", "Run.", "Run again."]
    assert [s["chapter"] for s in kept] == ["Open it", "", "Run it", ""]
    by_text = {s["text"]: s["anchor"] for s in full}
    assert [s["anchor"] for s in kept] == [by_text[s["text"]] for s in kept], "the anchors are the full guide's"
    assert kept[2]["anchor"] == {"key": "run it :: run.", "ordinal": 1}
    assert [s["source"]["slug"] for s in kept] == ["01-first"] * 4
    assert workshops.import_plan(course, workshop, parsed, Path("."), skip_chapters=["", "Open it", "Troubleshooting a bad file", "Run it"]) == []
    assert workshops.running_chapters([]) == []


def test_chapters_across_workshops_name_the_workshop_and_one_workshop_is_as_today():
    """E3: with several workshops a step's chapter is "<workshop title> · <section>" cut at a word to
    ``MAX_CHAPTER_CHARS``, and the first step of a workshop that starts no chapter carries the workshop's title, so
    every workshop starts a chapter; with one workshop nothing changes."""
    assert workshops.chapter_across("Your First Win", "2. Preview the valid rows") == "Your First Win · 2. Preview the valid rows"
    assert workshops.chapter_across("Your First Win", "") == "Your First Win"
    long = workshops.chapter_across("Your First Win", "a very long section name " * 6)
    assert len(long) <= walkthroughs.MAX_CHAPTER_CHARS and long.endswith("…") and not long[:-1].endswith(" ")
    assert len(workshops.chapter_across("w" * 100, "")) == walkthroughs.MAX_CHAPTER_CHARS

    def step(chapter, text):
        return {"chapter": chapter, "text": text, "instruction": text}

    a = ({"slug": "a", "title": "A"}, [step("", "x"), step("S", "y"), step("", "z")])
    b = ({"slug": "b", "title": "B"}, [step("", "p"), step("", "q"), step("T", "r")])
    c = ({"slug": "c", "title": "C"}, [step("U", "s")])
    assert workshops.combine_steps([a]) == a[1], "one workshop: as today"
    combined = workshops.combine_steps([a, b, c])
    assert [(s["chapter"], s["text"]) for s in combined] == [
        ("A", "x"), ("A · S", "y"), ("", "z"), ("B", "p"), ("", "q"), ("B · T", "r"), ("C · U", "s")]
    assert a[1][0]["chapter"] == "", "the parts are not changed in place"
    assert workshops.combine_steps([]) == []


def test_the_default_name_rule():
    """E1: one workshop -> its title; every workshop of the course -> the course's title; every workshop of exactly one
    section -> the section's title; otherwise "<first ticked workshop's title> + N more" - the first in the COURSE's
    order, whatever the order they were named in."""
    course = {"title": "PDI in 2 Hours", "sections": [
        {"title": "Get Ready", "workshops": [{"slug": "00", "title": "Before You Start"}]},
        {"title": "See It Work", "workshops": [{"slug": "01", "title": "Your First Win"}, {"slug": "02", "title": "Build the Pipeline Yourself"}]},
        {"title": "", "workshops": [{"slug": "09", "title": "Bring Your Own Data"}]},
    ]}
    name = workshops.default_name
    assert name(course, ["01"]) == "Your First Win"
    assert name(course, ["02", "01"]) == "See It Work"
    assert name(course, ["09", "02", "01", "00"]) == "PDI in 2 Hours"
    assert name(course, ["01", "00"]) == "Before You Start + 1 more", "the course's order, not the tick order"
    assert name(course, ["09"]) == "Bring Your Own Data"
    assert name(course, ["00", "09"]) == "Before You Start + 1 more", "a section without a title cannot name the walkthrough"
    # A course of one section holding every workshop: the course's title, not the section's.
    small = {"title": "Small", "sections": [{"title": "All", "workshops": [{"slug": "a", "title": "A"}, {"slug": "b", "title": "B"}]}]}
    assert name(small, ["a", "b"]) == "Small" and name(small, ["a"]) == "A"
    assert name(course, []) == "" and name(course, ["nope"]) == ""
    # The step bound's two wordings (``services.walkthroughs``).
    assert walkthroughs.too_many_steps_message(250, 1) == "The workshop has more steps than a walkthrough holds (250 of 200)."
    assert walkthroughs.too_many_steps_message(250, 3) == "The 3 workshops together have more steps than a walkthrough holds (250 of 200)."


def test_the_import_of_several_workshops_in_course_order_with_one_source_and_chapters_named_by_workshop(rig):
    """E3: ``workshops: [{slug, skip_chapters?}]`` in any order makes ONE walkthrough in the COURSE's order; the record's
    ``source`` keeps ``{course, title, version, slug}`` (the first workshop's) and gains ``workshops``; each step's own
    ``source.slug`` is its workshop's; the audit row names the workshops; the old one-workshop body still works and
    gains ``workshops`` too; a save (which never sends ``source``) and a duplicate keep it; the name follows the rule."""
    _three(rig)
    c = rig["owner"]
    # The old body: one workshop, as before - its source now says which workshops, one.
    r = c.post(IMPORT, json={"course": "demo", "slug": "02-second"})
    assert r.status_code == 200, r.text
    one = r.json()
    assert one["name"] == "Build the Pipeline Yourself"
    assert one["source"] == {"course": "demo", "slug": "02-second", "title": "Demo course", "version": "1.2.3",
                             "workshops": [{"slug": "02-second", "title": "Build the Pipeline Yourself"}]}
    assert [s["chapter"] for s in one["steps"]] == ["Build", ""], "one workshop: the chapters as today"
    # The new body with one workshop: the same walkthrough.
    r = c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "02-second"}]})
    assert r.status_code == 200 and r.json()["source"] == one["source"] and [s["chapter"] for s in r.json()["steps"]] == ["Build", ""]
    # Three, named in any order, one with a chapter left out (and a name the guide does not have, ignored).
    body = {"course": "demo", "workshops": [{"slug": "03-third"}, {"slug": "01-first", "skip_chapters": ["Troubleshooting a bad file", "Gone"]}, {"slug": "02-second"}]}
    r = c.post(IMPORT, json=body)
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["name"] == "Demo course", "every workshop of the course: the course's title"
    assert rec["source"] == {"course": "demo", "slug": "01-first", "title": "Demo course", "version": "1.2.3", "workshops": [
        {"slug": "01-first", "title": "Your First Win"}, {"slug": "02-second", "title": "Build the Pipeline Yourself"}, {"slug": "03-third", "title": "Enrich and Join"}]}
    assert [(s["text"], s["chapter"], s["source"]["slug"]) for s in rec["steps"]] == [
        ("Before any heading.", "Your First Win", "01-first"),
        ("Open.", "Your First Win · Open it", "01-first"),
        ("Click.", "", "01-first"),
        ("Run.", "Your First Win · Run it", "01-first"),
        ("Build it.", "Build the Pipeline Yourself · Build", "02-second"),
        ("Save it.", "", "02-second"),
        ("Join.", "Enrich and Join · Join", "03-third"),
    ], "the course's order; every workshop starts a chapter; the chapter left out is gone"
    assert rec["steps"][3]["anchor"] == {"key": "run it :: run.", "ordinal": 1}, "the anchor is the full guide's"
    build = rec["steps"][4]
    assert build["image"] == f"step-{build['id']}.png" and build["source"]["image"] == "_assets/images/p.png"
    assert build["anchor"] == {"key": "build :: build it.", "ordinal": 1}
    rows = auth_store.list_audit(action="walkthrough.import")
    assert [row["detail"] for row in rows] == [
        "demo: 3 workshops (01-first, 02-second, 03-third): 7 steps, 5 chapters, 1 picture",
        "demo/02-second: 2 steps, 1 chapter, 1 picture",
        "demo/02-second: 2 steps, 1 chapter, 1 picture",
    ]
    # The builder's save sends no source: the record's own stays, workshops and all; a duplicate keeps it.
    saved = _put(c, rec["id"], _body(rec)).json()
    assert saved["source"] == rec["source"] and saved["revision"] == 2
    assert c.post(f"/api/walkthroughs/{rec['id']}/duplicate").json()["source"] == rec["source"]
    # The name rule on the server, the same as the dialog's: a section's workshops, a pair across sections, a name given.
    assert c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "02-second"}, {"slug": "01-first"}]}).json()["name"] == "Part one"
    assert c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "03-third"}, {"slug": "01-first"}]}).json()["name"] == "Your First Win + 1 more"
    assert c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "03-third"}, {"slug": "01-first"}], "name": " Mine "}).json()["name"] == "Mine"
    # The preview says which chapter each step is in, so the dialog can offer the ticks.
    preview = c.get("/api/courses/demo/workshops/01-first/preview").json()
    assert [s["in_chapter"] for s in preview["steps"]] == ["", "Open it", "Open it", "Troubleshooting a bad file", "Run it"]


def test_an_unreadable_missing_or_doubled_workshop_refuses_the_whole_import_and_writes_nothing(rig):
    """E3: an unreadable workshop -> 400 naming it; a missing one -> 404 naming it; a body with both forms, neither,
    no workshop, a workshop twice, an unknown key or more than ``MAX_WORKSHOPS`` -> 422; nothing written in any."""
    folder = _three(rig)
    (folder / "02-second" / "guide.md").write_bytes(b"## S\n\n1. Not \xff\xfe UTF-8.\n")
    c = rig["owner"]
    r = c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "01-first"}, {"slug": "02-second"}, {"slug": "03-third"}]})
    assert r.status_code == 400 and r.json()["detail"] == "demo/02-second/guide.md cannot be read as UTF-8 text.", r.text
    r = c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "01-first"}, {"slug": "09-nope"}]})
    assert r.status_code == 404 and "09-nope" in r.json()["detail"], r.text
    assert c.post(IMPORT, json={"course": "nope", "workshops": [{"slug": "01-first"}]}).status_code == 404
    many = [{"slug": f"{i:02d}-w"} for i in range(workshops.MAX_WORKSHOPS + 1)]
    for bad in (
        {"course": "demo"},
        {"course": "demo", "slug": "01-first", "workshops": [{"slug": "01-first"}]},
        {"course": "demo", "workshops": []},
        {"course": "demo", "workshops": [{"slug": "01-first"}, {"slug": "01-first"}]},
        {"course": "demo", "workshops": [{"slug": "01-first", "skip": []}]},
        {"course": "demo", "workshops": [{"slug": "01-first", "skip_chapters": "Open it"}]},
        {"course": "demo", "workshops": [{"slug": "01-first", "skip_chapters": ["c" * (walkthroughs.MAX_CHAPTER_CHARS + 1)]}]},
        {"course": "demo", "workshops": many},
    ):
        assert c.post(IMPORT, json=bad).status_code == 422, bad
    assert workshops.MAX_WORKSHOPS == 50
    assert c.get("/api/walkthroughs").json()["walkthroughs"] == []
    made = rig["tmp"] / "walkthroughs"
    assert not made.exists() or list(made.iterdir()) == []


def test_the_step_bound_applies_to_the_workshops_together_before_anything_is_written(rig, monkeypatch):
    """E3: ``MAX_STEPS`` bounds the steps of every chosen workshop together - 400 "… has more steps than a walkthrough
    holds (N of 200)" with nothing written; a chapter left out counts no more; one workshop is worded for one."""
    folder = _course(rig["courses"], "demo", summary="# T\n\n## P\n\n* [A](01-a/guide.md)\n* [B](02-b/guide.md)\n")
    _workshop(folder, "01-a", "## S\n\n1. One.\n2. Two.\n3. Three.\n")
    _workshop(folder, "02-b", "## S\n\n1. Four.\n2. Five.\n")
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 4)
    c = rig["admin"]
    r = c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "01-a"}, {"slug": "02-b"}]})
    assert r.status_code == 400 and r.json()["detail"] == "The 2 workshops together have more steps than a walkthrough holds (5 of 4).", r.text
    made = rig["tmp"] / "walkthroughs"
    assert not made.exists() or list(made.iterdir()) == []
    monkeypatch.setattr(walkthroughs, "MAX_STEPS", 2)
    r = c.post(IMPORT, json={"course": "demo", "slug": "01-a"})
    assert r.status_code == 400 and r.json()["detail"] == "The workshop has more steps than a walkthrough holds (3 of 2).", r.text
    r = c.post(IMPORT, json={"course": "demo", "workshops": [{"slug": "01-a", "skip_chapters": ["S"]}, {"slug": "02-b"}]})
    assert r.status_code == 200, r.text
    assert [s["text"] for s in r.json()["steps"]] == ["Four.", "Five."], "the chapter left out counts no more"
    assert r.json()["source"]["workshops"] == [{"slug": "01-a", "title": "A"}, {"slug": "02-b", "title": "B"}]
