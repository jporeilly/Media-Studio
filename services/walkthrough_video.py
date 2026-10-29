"""A walkthrough compiled into a narrated video (W3): the stills and the snapshot.

A walkthrough becomes a Media Studio project of kind ``walkthrough``
(``services.projects.create_walkthrough_project``): a directory like any
other, holding a SNAPSHOT of the walkthrough's record as it was when the video
was asked for (``<safe name>.walkthrough.json``, the project's "source file")
and the steps' pictures copied beside it (``steps/``), so the project is
self-contained - deleting or editing the walkthrough afterwards changes
nothing in it. The inner engine project is created at once from the snapshot,
every slide with its still already composed (``materialise``: the ``load_pdf``
pattern, so a generate never reaches PowerPoint), its narration
(``services.walkthroughs.step_narration``), its ``focus`` - the hotspot in the
still's frame pixels, for the render's zoom - and the chapter it starts.

**The still** (``compose_still``) is the step's picture on a ``VIDEO_FRAME``
canvas of ``STAGE_COLOUR`` - the player's stage colour in the light theme,
``[data-theme="light"] .ms-stage`` in ``frontend/src/styles/theme.css`` -
scaled DOWN to fit when larger, never up, centred: ``services.slides.letterbox``
with the scaling written once (``services.slides.fit_in_frame``), the one
routine the deck's "Add to deck" uses too. A transparent still (a Window-kind
run's framed dialog) composes onto the colour. The hotspot RING is drawn into
the still (``draw_ring``: ``HOTSPOT_RING``, 4 px of the app's accent colour,
radius 8, a 1 px darker outline outside it for contrast on any picture), so the
mark is there with or without the zoom; the focus is the hotspot through the
same fit (``focus_in_frame``), None without a hotspot or with Zoom off. A step
without a picture is a text card drawn with Pillow (``text_card``,
``core.fonts``) at the same frame: the step's text large, its chapter small
above it, the walkthrough's name small below, on the stage colour.
"""

from __future__ import annotations

import copy
import functools
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from PIL import Image, ImageDraw

from core.fonts import resolve_font, text_image
from core.project_manager import ProjectManager
from services import walkthroughs
from utils.logger import get_logger

log = get_logger("WALKTHROUGH_VIDEO")

#: The canvas every still is composed on, whatever the output preset: the render scales it to the preset.
VIDEO_FRAME = (1920, 1080)
#: The player's stage colour in the light theme: ``#cbd5e1``, the ``[data-theme="light"] .ms-stage`` rule
#: of ``frontend/src/styles/theme.css`` (``STAGE_COLOUR_TOKEN``). A still that does not fill the frame, a
#: transparent still and the text card sit on it.
STAGE_COLOUR = (203, 213, 225)
STAGE_COLOUR_TOKEN = '[data-theme="light"] .ms-stage'
#: The same colour as ffmpeg's ``color`` source takes it: the pause between two steps (``VideoCreator(pause_colour)``).
STAGE_COLOUR_FFMPEG = "0x%02x%02x%02x" % STAGE_COLOUR


@dataclass(frozen=True)
class Ring:
    """The hotspot ring drawn into a still: ``width`` px of ``colour`` just outside the hotspot, corners of
    ``radius``, and ``outline_width`` px of the darker ``outline`` outside that."""

    colour: tuple[int, int, int]
    width: int
    radius: int
    outline: tuple[int, int, int]
    outline_width: int


#: The ring: the app's accent colour (``--brand`` ``#0d9488`` in theme.css), 4 px, radius 8, a 1 px darker
#: outline (the accent at 55 %) outside it so the mark reads on a light picture as on a dark one.
HOTSPOT_RING = Ring(colour=(13, 148, 136), width=4, radius=8, outline=(7, 81, 75), outline_width=1)

#: The text card's colours and sizes (the sizes at 1080 lines, scaled with the frame's height): the light
#: theme's ``--text`` and ``--text-2``. The text is drawn at ``CARD_TEXT_SIZE``, or smaller - down to
#: ``CARD_TEXT_MIN_SIZE`` - when it would not fit the band between the chapter and the name, and past the
#: band's capacity at the smallest size it is cut at a word with an ellipsis (``fit_text``).
CARD_TEXT_COLOUR = "#0f172a"
CARD_SMALL_COLOUR = "#334155"
CARD_TEXT_SIZE = 56
CARD_TEXT_MIN_SIZE = 28
CARD_SMALL_SIZE = 28
#: The room kept between the text's band and the small lines, at 1080 lines.
CARD_GAP = 16
CARD_ELLIPSIS = "…"
#: Where the card's three lines are centred, as shares of the height: the chapter, the text, the name.
CARD_ABOVE_AT = 0.33
CARD_TEXT_AT = 0.5
CARD_BELOW_AT = 0.67

#: The snapshot: ``<safe name>.walkthrough.json`` beside the project's record, the pictures in ``steps/``.
SNAPSHOT_VERSION = 1
SNAPSHOT_SUFFIX = ".walkthrough.json"
PICTURES_DIR = "steps"


# -- the fit and the focus --------------------------------------------------------------------------------

def fit_in_frame(width: int, height: int, frame: tuple[int, int]):
    """Where a ``width`` x ``height`` picture goes on ``frame``: scaled DOWN to fit when larger, never
    up, centred - ``services.slides.fit_in_frame`` with ``upscale`` off, the one place the scaling is
    written. Returns ``(scale, x, y, width, height)``."""
    from services.slides import fit_in_frame as fit  # lazy: slides imports the project store, which makes these projects

    return fit(width, height, frame, upscale=False)


def focus_in_frame(hotspot: Optional[dict], width: int, height: int, frame: tuple[int, int]) -> Optional[dict]:
    """The hotspot given in the picture's pixels, where it is on the composed still: through the same
    scale and offset as the picture (``fit_in_frame``), its edges rounded to whole pixels, at least 1 x 1.
    None for no hotspot. Pure. (``place_in_frame`` / ``hotspot_in_frame`` only translate: this is the
    scaling, written once.)"""
    if not hotspot:
        return None
    fit = fit_in_frame(width, height, frame)
    x0 = fit.x + int(round(int(hotspot["x"]) * fit.scale))
    y0 = fit.y + int(round(int(hotspot["y"]) * fit.scale))
    x1 = fit.x + int(round((int(hotspot["x"]) + int(hotspot["width"])) * fit.scale))
    y1 = fit.y + int(round((int(hotspot["y"]) + int(hotspot["height"])) * fit.scale))
    return {"x": x0, "y": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}


def draw_ring(image: Image.Image, focus: dict, ring: Ring = HOTSPOT_RING) -> None:
    """Draw ``ring`` round ``focus`` (frame pixels) into ``image``, in place: the outline first, then the
    ring inside it, both just outside the hotspot so nothing of it is covered."""
    draw = ImageDraw.Draw(image)
    x0, y0 = int(focus["x"]), int(focus["y"])
    x1, y1 = x0 + int(focus["width"]), y0 + int(focus["height"])
    pad = ring.width + ring.outline_width
    draw.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad - 1, y1 + pad - 1), radius=ring.radius + ring.outline_width,
                           outline=ring.outline, width=ring.outline_width)
    pad = ring.width
    draw.rounded_rectangle((x0 - pad, y0 - pad, x1 + pad - 1, y1 + pad - 1), radius=ring.radius,
                           outline=ring.colour, width=ring.width)


# -- the stills -------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=4)
def _font(preferred: Optional[str]) -> Optional[str]:
    """The font file the cards are drawn with, resolved once per process (``core.fonts.resolve_font``)."""
    return resolve_font(preferred)


def _card_font() -> Optional[str]:
    from utils.config import config

    return _font(config._config.get("title_font") or None)


def cut_to_fit(text: str, limit: int) -> str:
    """``text`` within ``limit`` characters: as it is when it fits, else cut at the last word that fits
    (inside the word when there is no earlier one) and ended with an ellipsis. Pure."""
    text = text.strip()
    if len(text) <= limit:
        return text
    room = max(1, limit - len(CARD_ELLIPSIS))
    head = text[:room]
    if room < len(text) and text[room] != " " and " " in head:
        head = head[:head.rfind(" ")]
    return head.rstrip() + CARD_ELLIPSIS


def fit_text(
    text: str, font: Optional[str], max_width: int, max_height: int, size: int, min_size: int, *, colour: str = CARD_TEXT_COLOUR,
) -> tuple[Image.Image, int, bool]:
    """``text`` drawn (``core.fonts.text_image``, wrapped to ``max_width``) no taller than ``max_height``: at
    ``size``, else at each smaller size in steps of 4 px down to ``min_size``, and past that at ``min_size``
    cut at a word with an ellipsis to the longest length that fits (``cut_to_fit``, a binary search). Returns
    the image, the size used and whether the text was cut."""
    text = text.strip()
    sizes = list(range(size, min_size - 1, -4)) or [size]
    if sizes[-1] != min_size:
        sizes.append(min_size)
    drawn = None
    for px in sizes:
        drawn = text_image(text, px, colour, font=font, max_width=max_width)
        if drawn.height <= max_height:
            return drawn, px, False
    # Nothing fits whole: the longest cut that does, at the smallest size.
    low, high = 1, len(text)
    best = text_image(CARD_ELLIPSIS, min_size, colour, font=font, max_width=max_width)
    while low <= high:
        middle = (low + high) // 2
        candidate = text_image(cut_to_fit(text, middle), min_size, colour, font=font, max_width=max_width)
        if candidate.height <= max_height:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best, min_size, True


def text_card(text: str, above: str, below: str, *, frame: tuple[int, int] = VIDEO_FRAME) -> Image.Image:
    """A card on the stage colour: ``text`` large at the middle, ``above`` small above it, ``below`` small
    under it (either left out when empty), each wrapped to 80 % of the width - the intro card's look, on
    the stage colour. The chapter and the name keep their lines; the text is fitted into the band between
    them (``fit_text``: a smaller size past some length, then cut at a word with an ellipsis), so no row
    carries both. Text that cannot be drawn leaves the card plain rather than failing the render."""
    width, height = frame
    card = Image.new("RGB", frame, STAGE_COLOUR)
    scale = height / 1080
    font = _card_font()
    max_width = int(width * 0.8)
    gap = max(4, round(CARD_GAP * scale))
    small = max(8, round(CARD_SMALL_SIZE * scale))
    try:
        placed = []
        band_top, band_bottom = gap, height - gap
        if (above or "").strip():
            drawn = text_image(above.strip(), small, CARD_SMALL_COLOUR, font=font, max_width=max_width)
            y = int(height * CARD_ABOVE_AT) - drawn.height // 2
            placed.append((drawn, y))
            band_top = y + drawn.height + gap
        if (below or "").strip():
            drawn = text_image(below.strip(), small, CARD_SMALL_COLOUR, font=font, max_width=max_width)
            y = int(height * CARD_BELOW_AT) - drawn.height // 2
            placed.append((drawn, y))
            band_bottom = y - gap
        if (text or "").strip():
            band = max(1, band_bottom - band_top)
            drawn, _, _ = fit_text(text, font, max_width, band, max(8, round(CARD_TEXT_SIZE * scale)), max(8, round(CARD_TEXT_MIN_SIZE * scale)))
            y = int(height * CARD_TEXT_AT) - drawn.height // 2
            y = max(band_top, min(y, band_bottom - drawn.height))
            placed.append((drawn, y))
        for drawn, y in placed:
            card.paste(drawn, ((width - drawn.width) // 2, y), drawn)
    except Exception as exc:  # noqa: BLE001 - a card is worth a warning, not a failed render
        log.warning("The step card's text could not be drawn (%s); the card is plain", exc)
    return card


def card_text(step: dict, number: Optional[int] = None) -> str:
    """What the card of a step without a picture says: the step's text (what the viewer reads), else its
    narration's words, else "Step N"."""
    text = (step.get("text") or "").strip()
    if text:
        return text
    spoken = walkthroughs.spoken_text(walkthroughs.narration_of(step))
    if spoken:
        return spoken
    return f"Step {number}" if number else ""


def compose_still(
    picture: Optional[Path], step: dict, name: str, *,
    frame: tuple[int, int] = VIDEO_FRAME, zoom: bool = True, number: Optional[int] = None, chapter: Optional[str] = None,
) -> tuple[Image.Image, Optional[dict]]:
    """The still of one step and its focus. With a picture: the picture fitted onto the stage colour
    (down only, centred), the hotspot ring drawn in when the step has a hotspot, the focus the hotspot
    in frame pixels when ``zoom`` is on (None otherwise). Without one - or one that is not there or cannot
    be read - a text card (``chapter``, else the step's own, above; ``name`` below); no focus."""
    chapter = chapter if chapter is not None else (step.get("chapter") or "")
    path = Path(picture) if picture else None
    if path is None or not path.is_file():
        return text_card(card_text(step, number), chapter, name, frame=frame), None
    try:
        with Image.open(path) as source:
            source.load()
            src = source.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - a picture Pillow cannot read is a card, not a failed project
        log.warning("The step's picture %s cannot be read (%s); the video shows a card for it", path.name, exc)
        return text_card(card_text(step, number), chapter, name, frame=frame), None
    from services.slides import letterbox  # lazy, as fit_in_frame's import is

    still = letterbox(src, frame, STAGE_COLOUR, upscale=False)
    focus = focus_in_frame(step.get("hotspot"), src.width, src.height, frame)
    if focus:
        draw_ring(still, focus)
    return still, (focus if zoom else None)


# -- the snapshot -----------------------------------------------------------------------------------------

def make_snapshot(record: dict, *, name: str, notes: bool, zoom: bool) -> dict:
    """The walkthrough's record as it is now, with the video's options, as the project's source file."""
    return {
        "version": SNAPSHOT_VERSION,
        "walkthrough_id": record.get("id"),
        "walkthrough_name": record.get("name") or "",
        "name": name,
        "notes": bool(notes),
        "zoom": bool(zoom),
        "made_at": datetime.now(timezone.utc).isoformat(),
        "record": copy.deepcopy(record),
    }


def load_snapshot(path: Path) -> dict:
    """The snapshot at ``path``; ``ValueError`` when it cannot be read or is not one."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"The walkthrough snapshot {path.name} could not be read: {exc}") from None
    record = data.get("record") if isinstance(data, dict) else None
    if not isinstance(record, dict) or not isinstance(record.get("steps"), list):
        raise ValueError(f"The walkthrough snapshot {path.name} does not have the shape of one.")
    return data


def plan_slides(snapshot: dict) -> list[dict]:
    """One entry per step: its index, its id, its picture's name (None without one), its size, its
    hotspot, its text, its ``narration`` (``step_narration`` with the snapshot's ``notes``), the
    ``chapter`` the slide STARTS - the step's own; the walkthrough's name on the first slide when the
    first step starts none, so the steps before the first chapter are one chapter named after the
    walkthrough - and ``chapter_at``, the chapter it belongs to (for its card)."""
    record = snapshot["record"]
    name = (record.get("name") or snapshot.get("walkthrough_name") or "Walkthrough").strip() or "Walkthrough"
    notes = bool(snapshot.get("notes"))
    out: list[dict] = []
    current: Optional[str] = None
    for index, step in enumerate(record.get("steps") or []):
        own = (step.get("chapter") or "").strip()
        starts = own or (name if index == 0 else None)
        if starts:
            current = starts
        out.append({
            "index": index, "id": step.get("id"), "image": step.get("image") or None,
            "width": int(step.get("width") or 0), "height": int(step.get("height") or 0),
            "hotspot": step.get("hotspot") or None, "text": step.get("text") or "", "instruction": step.get("instruction") or "",
            "narration": walkthroughs.step_narration(step, notes=notes), "chapter": starts, "chapter_at": current,
        })
    return out


def source_info(snapshot: dict) -> list:
    """What the slide editor shows as the "deck's own" text of each slide (``services.slides._deck_info``):
    the snapshot's narration as the original notes a Reset goes back to, "Step N" as the title and the
    step's text as the body."""
    return [SimpleNamespace(index=p["index"], speaker_notes=p["narration"], title=f"Step {p['index'] + 1}", body_text=p["text"] or None)
            for p in plan_slides(snapshot)]


def materialise(snapshot_path: Path, pm: ProjectManager) -> int:
    """The inner project of a walkthrough project from its snapshot, the ``load_pdf`` pattern: an
    existing project (``pm.state``) with the snapshot's number of slides is KEPT - its narration,
    overrides and undo history are what the slide editor writes - and only a still that is missing is
    composed again; the project is created fresh when there is none or the count differs, every slide
    with its narration, its still (``images/step_NNN.png``), its focus and its chapter. Returns the number
    of slides. ``ValueError`` for a snapshot that cannot be read."""
    from services import slides  # lazy: slides imports the store, which imports this module lazily

    snapshot_path = Path(snapshot_path)
    snapshot = load_snapshot(snapshot_path)
    plan = plan_slides(snapshot)
    pictures = snapshot_path.parent / PICTURES_DIR
    name = (snapshot["record"].get("name") or snapshot.get("walkthrough_name") or "Walkthrough").strip() or "Walkthrough"
    zoom = bool(snapshot.get("zoom"))
    fresh = pm.state is None or len(pm.state.slides) != len(plan)
    if fresh:
        pm.create_project(pptx_path=snapshot_path, slide_notes=[p["narration"] for p in plan], voice_id="")
    pm.images_dir.mkdir(parents=True, exist_ok=True)
    for p in plan:
        slide = pm.state.slides[p["index"]]
        target = pm.images_dir / slides.image_name("walkthrough", p["index"])
        if not fresh and target.is_file() and slide.image_path and Path(slide.image_path).is_file():
            continue
        picture = pictures / p["image"] if p["image"] else None
        still, focus = compose_still(picture, p, name, zoom=zoom, number=p["index"] + 1, chapter=p["chapter_at"] or "")
        still.save(target, compress_level=1)
        slide.image_path = str(target)
        slide.focus = focus
        slide.chapter = p["chapter"]
    pm.save()
    return len(plan)
