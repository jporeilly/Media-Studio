"""Title cards and the text watermark are drawn with a real font FILE.

moviepy 2 / Pillow refuse a family name: ``TextClip(font="Arial")`` raised
"Invalid font Arial, pillow failed to use it" on the live rig, the engine
swallowed it, and the intro card rendered solid black with the watermark
dropped. ``core/fonts.py`` resolves a font file once per VideoCreator and,
when the host has none, draws the text with Pillow's built-in font - so text
is never silently lost. moviepy and Pillow are real here (one frame through
``get_frame`` needs no ffmpeg); only the font lookup is redirected.
"""

import logging
from pathlib import Path

import numpy as np
import pytest
from moviepy import ColorClip
from PIL import ImageFont

from core import fonts, video_creator
from core.video_creator import VideoCreator
from utils.config import DEFAULT_CONFIG, config


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)


class _Records(logging.Handler):
    """The engine loggers do not propagate (utils/logger.py), so caplog never
    sees them: collect their records directly."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level):
        return [r.getMessage() for r in self.records if r.levelno == level]


@pytest.fixture
def records():
    handler = _Records()
    names = ("mediastudio.FONTS", "mediastudio.VIDEO")
    for name in names:
        logging.getLogger(name).addHandler(handler)
    yield handler
    for name in names:
        logging.getLogger(name).removeHandler(handler)


SIZE = (640, 480)


def _black(size=SIZE):
    return ColorClip(size=size, color=(0, 0, 0), duration=1.0).with_fps(2)


# -- resolve_font -----------------------------------------------------------------

def test_resolve_font_finds_a_font_file_on_this_host():
    path = fonts.resolve_font()
    assert path, "no font file found - the title cards would use the built-in font"
    assert Path(path).is_file() and path.lower().endswith((".ttf", ".otf"))
    ImageFont.truetype(path, 12)  # what moviepy's TextClip checks before drawing
    assert path == fonts.resolve_font(), "deterministic"


def test_resolve_font_prefers_an_existing_font_file(tmp_path, records):
    found = fonts.resolve_font()
    mine = tmp_path / "brand.ttf"
    mine.write_bytes(Path(found).read_bytes())
    assert fonts.resolve_font(str(mine)) == str(mine)
    assert records.messages(logging.WARNING) == []


def test_resolve_font_ignores_a_preferred_value_that_is_not_a_font(tmp_path, records):
    found = fonts.resolve_font()
    assert fonts.resolve_font(str(tmp_path / "missing.ttf")) == found
    junk = tmp_path / "junk.ttf"
    junk.write_bytes(b"not a font")
    assert fonts.resolve_font(str(junk)) == found
    assert fonts.resolve_font("Arial") == found, "a family name is not a file"
    warnings = records.messages(logging.WARNING)
    assert len(warnings) == 3 and all("title_font" in w for w in warnings)


def test_resolve_font_returns_none_when_nothing_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(fonts, "_iter_candidates", lambda: iter([tmp_path / "gone.ttf"]))
    assert fonts.resolve_font() is None
    assert fonts.resolve_font(str(tmp_path / "also-gone.ttf")) is None


def test_candidates_start_with_the_named_files_then_the_font_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(fonts, "_font_dirs", lambda: [tmp_path])
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "z.ttf").write_bytes(b"")
    (tmp_path / "a.ttf").write_bytes(b"")
    candidates = list(fonts._iter_candidates())
    named = fonts._named_candidates()
    assert candidates[:len(named)] == named
    assert [p.name for p in candidates[len(named):]] == ["a.ttf", "z.ttf"], "the walk finds every .ttf, sorted"
    assert any(p.name == "arial.ttf" for p in named) or not fonts._windows_fonts_dir()
    assert Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf") in named
    assert Path("/System/Library/Fonts/Supplemental/Arial.ttf") in named


# -- text_image: Pillow draws the text, with a file or the built-in font -----------

def test_text_image_draws_with_a_font_file_and_with_the_built_in_font():
    with_file = fonts.text_image("Welcome", 32, "white", font=fonts.resolve_font())
    built_in = fonts.text_image("Welcome", 32, "white", font=None)
    for image in (with_file, built_in):
        assert image.mode == "RGBA"
        alpha = np.array(image)[:, :, 3]
        assert alpha.max() == 255 and alpha.min() == 0, "opaque glyphs on a transparent ground"
        assert np.array(image)[:, :, :3].max() == 255, "white"
    assert fonts.text_image("Welcome", 32, "#cccccc", font=None).getpixel((0, 0)) == (0, 0, 0, 0)


def test_text_image_wraps_to_the_width():
    one_line = fonts.text_image("one two three four five six", 20, "white")
    wrapped = fonts.text_image("one two three four five six", 20, "white", max_width=90)
    assert wrapped.width <= one_line.width and wrapped.width <= 90 + 2 * fonts.LINE_SPACING + 10
    assert wrapped.height > one_line.height * 2, "several lines"
    single = fonts.text_image("Unbreakableword", 20, "white", max_width=10)
    assert single.height < wrapped.height, "a word wider than the width stays whole"


def test_load_font_falls_back_to_the_built_in_font(tmp_path, records):
    junk = tmp_path / "junk.ttf"
    junk.write_bytes(b"not a font")
    assert fonts.load_font(str(junk), 20) is not None
    assert any("built-in font" in m for m in records.messages(logging.WARNING))


# -- the VideoCreator resolves once and draws with it ----------------------------------

def test_video_creator_resolves_the_font_once_and_logs_it(records):
    creator = VideoCreator(resolution=SIZE)
    assert creator.font == fonts.resolve_font()
    assert any(m == f"Text font: {creator.font}" for m in records.messages(logging.INFO))
    assert records.messages(logging.WARNING) == []


def test_video_creator_prefers_the_configured_title_font(tmp_path, records):
    found = fonts.resolve_font()
    mine = tmp_path / "brand.ttf"
    mine.write_bytes(Path(found).read_bytes())
    config._config["title_font"] = str(mine)
    assert VideoCreator(resolution=SIZE).font == str(mine)

    config._config["title_font"] = str(tmp_path / "missing.ttf")
    assert VideoCreator(resolution=SIZE).font == found, "a bad value falls through to the host's fonts"
    assert any("title_font" in m for m in records.messages(logging.WARNING))
    assert DEFAULT_CONFIG["title_font"] == "", "no font configured by default"


def test_video_creator_warns_clearly_when_the_host_has_no_font_file(monkeypatch, records):
    monkeypatch.setattr(video_creator, "resolve_font", lambda preferred=None: None)
    assert VideoCreator(resolution=SIZE).font is None
    assert any("No font file found" in m for m in records.messages(logging.WARNING))


def test_title_card_has_the_title_in_the_centre_band():
    creator = VideoCreator(resolution=SIZE)
    frame = creator._create_title_card("Welcome", "", 1.0).get_frame(0.5)
    h, w = frame.shape[:2]
    assert (h, w) == (480, 640)
    assert frame.max() > 0, "the card is solid black - the title was dropped"
    assert frame[h // 2 - 40 : h // 2 + 40].max() > 0, "the title sits around the centre"
    assert frame[: h // 4].max() == 0 and frame[-h // 4 :].max() == 0, "and only there"
    assert frame[:, : w // 8].max() == 0 and frame[:, -w // 8 :].max() == 0, "centred horizontally"


def test_title_card_with_a_subtitle_draws_both():
    creator = VideoCreator(resolution=SIZE)
    frame = creator._create_title_card("Welcome", "Q3 review", 1.0).get_frame(0.5)
    h = frame.shape[0]
    assert frame[int(h * 0.4) : int(h * 0.4) + 56].max() == 255, "the title at 40 %, white"
    subtitle_band = frame[int(h * 0.55) + 4 : int(h * 0.55) + 34]
    assert 0 < subtitle_band.max() < 255, "the subtitle at 55 %, grey (#cccccc)"
    assert frame[: int(h * 0.3)].max() == 0


def test_watermark_adds_text_pixels_at_its_position():
    creator = VideoCreator(resolution=SIZE, watermark_text="ACME Corp", watermark_position="bottom-right", watermark_opacity=1.0)
    out = creator._apply_watermark(_black())
    frame = out.get_frame(0.5)
    h, w = frame.shape[:2]
    assert frame.max() == 255, "white text"
    assert frame[h // 2 :, w // 2 :].max() > 0, "in the bottom-right quadrant"
    assert frame[: h // 2].max() == 0 and frame[:, : w // 2].max() == 0, "and nowhere else"

    creator = VideoCreator(resolution=SIZE, watermark_text="ACME Corp", watermark_position="top-left", watermark_opacity=0.5)
    frame = creator._apply_watermark(_black()).get_frame(0.5)
    assert frame[: h // 2, : w // 2].max() > 0, "top-left"
    assert frame[h // 2 :].max() == 0 and frame[:, w // 2 :].max() == 0
    assert 100 <= frame.max() <= 140, "half opacity over black"


def test_text_is_still_drawn_with_the_built_in_font_when_no_font_file_exists(monkeypatch):
    monkeypatch.setattr(video_creator, "resolve_font", lambda preferred=None: None)
    creator = VideoCreator(resolution=SIZE, watermark_text="ACME", watermark_position="bottom-right", watermark_opacity=1.0)
    assert creator.font is None

    frame = creator._create_title_card("Welcome", "Q3 review", 1.0).get_frame(0.5)
    h, w = frame.shape[:2]
    assert frame.max() > 0
    assert frame[int(h * 0.4) : int(h * 0.4) + 56].max() > 0 and frame[: int(h * 0.3)].max() == 0

    frame = creator._apply_watermark(_black()).get_frame(0.5)
    assert frame[h // 2 :, w // 2 :].max() > 0 and frame[: h // 2].max() == 0


def _lit_height(frame) -> int:
    lit = np.flatnonzero(frame.max(axis=(1, 2)) > 0)
    return int(lit[-1] - lit[0] + 1) if len(lit) else 0


def test_title_and_watermark_glyphs_are_not_cut_off():
    """moviepy 2.1.2's TextClip allocates an image shorter than the text it
    draws, so every letter loses its bottom rows and descenders are cut flat
    (48 px "Welcome gyp" came out 36 rows tall instead of ~45). The engine
    draws with Pillow directly: capitals plus descenders keep their height."""
    creator = VideoCreator(resolution=SIZE, watermark_text="Agyp", watermark_position="bottom-right", watermark_opacity=1.0)
    card = creator._create_title_card("Welcome gyp", "", 1.0).get_frame(0.5)
    assert _lit_height(card) >= 42, "48 px: capitals plus descenders are ~45 rows; clipped text is ~36"
    watermark = creator._apply_watermark(_black()).get_frame(0.5)
    assert _lit_height(watermark) >= 20, "24 px: ~22 rows; clipped text is ~18"
