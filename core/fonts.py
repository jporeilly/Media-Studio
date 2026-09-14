"""The font the engine draws text with (title cards, the text watermark).

moviepy 2 renders text with Pillow, which needs a font FILE (a ``.ttf`` /
``.otf`` path) - a family name such as ``"Arial"`` is refused with "Invalid
font Arial, pillow failed to use it", and the carried-over engine caught that
and silently produced a black title card and no watermark.

``resolve_font`` finds a usable file once per render (the configured
``title_font`` when it is one, else the host's Arial / Segoe UI / Calibri /
DejaVu / Liberation, else any ``.ttf`` under the platform font directories).
``text_image`` draws text with Pillow directly - with that file, or with
Pillow's built-in font when the host has none - so text is never dropped.
"""

import os
from pathlib import Path
from typing import Iterator, Optional

from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

logger = get_logger("FONTS")

# Space between the lines of a wrapped text, in pixels.
LINE_SPACING = 4


def _windows_fonts_dir() -> Optional[Path]:
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if not windir and os.name == "nt":
        windir = r"C:\Windows"
    return Path(windir) / "Fonts" if windir else None


def _named_candidates() -> list:
    """The well-known sans-serif files, most preferred first: Windows, then
    Linux, then macOS. A path that does not exist on this host is skipped."""
    out = []
    win = _windows_fonts_dir()
    if win:
        out += [win / "arial.ttf", win / "segoeui.ttf", win / "calibri.ttf"]
    out += [
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    return out


def _font_dirs() -> list:
    """The platform font directories, searched for any ``.ttf`` when none of
    the named files exists."""
    home = Path.home()
    dirs = []
    win = _windows_fonts_dir()
    if win:
        dirs.append(win)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    dirs += [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home / ".fonts",
        home / ".local" / "share" / "fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        home / "Library" / "Fonts",
    ]
    return dirs


def _iter_candidates() -> Iterator[Path]:
    """Every font file worth trying, most preferred first; lazy, so the
    directory walk only happens when the named files are all missing."""
    for path in _named_candidates():
        yield path
    for directory in _font_dirs():
        if directory.is_dir():
            yield from sorted(directory.rglob("*.ttf"))


def _loads(path: Path) -> bool:
    """True when Pillow (FreeType) can open ``path`` as a font - the same
    check moviepy's TextClip makes before it draws."""
    try:
        ImageFont.truetype(str(path), 12)
        return True
    except Exception:
        return False


def resolve_font(preferred: Optional[str] = None) -> Optional[str]:
    """A font file Pillow can open, as a path string.

    ``preferred`` (the configured ``title_font``) wins when it is an existing
    font file; a value that is not one is logged and ignored. Otherwise the
    first candidate on this host that exists and loads; None when there is
    none at all - the callers then draw with Pillow's built-in font.
    """
    if preferred:
        path = Path(preferred).expanduser()
        if path.is_file() and _loads(path):
            return str(path)
        logger.warning("Configured title_font %r is not a font file Pillow can open; looking for another", preferred)
    for path in _iter_candidates():
        if path.is_file() and _loads(path):
            return str(path)
    return None


def load_font(font: Optional[str], size: int):
    """A Pillow font object: ``font`` (a file path) at ``size``, else Pillow's
    built-in font at that size (FreeType's Aileron when FreeType is present,
    the bitmap font - whose size is fixed - otherwise)."""
    if font:
        try:
            return ImageFont.truetype(font, size)
        except Exception as exc:
            logger.warning("Font %s could not be loaded (%s); using Pillow's built-in font", font, exc)
    return ImageFont.load_default(size)


def _wrap(text: str, font, max_width: Optional[int], draw) -> str:
    """``text`` re-broken so no line is wider than ``max_width`` pixels (a
    single word wider than that stays whole); paragraph breaks are kept."""
    if not max_width:
        return text
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return "\n".join(lines)


def text_image(
    text: str, font_size: int, color: str = "white",
    font: Optional[str] = None, max_width: Optional[int] = None, align: str = "center",
) -> Image.Image:
    """``text`` drawn on a transparent RGBA image, just large enough for it:
    with the font file ``font``, else Pillow's built-in font. Wrapped to
    ``max_width`` pixels when given. ``color`` is any Pillow colour string
    (``"white"``, ``"#cccccc"``) - the same names moviepy's TextClip takes.
    """
    pil_font = load_font(font, font_size)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    wrapped = _wrap(text, pil_font, max_width, probe)
    left, top, right, bottom = probe.multiline_textbbox((0, 0), wrapped, font=pil_font, spacing=LINE_SPACING, align=align)
    pad = max(2, font_size // 8)
    width = max(1, int(right - left) + 2 * pad)
    height = max(1, int(bottom - top) + 2 * pad)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).multiline_text(
        (pad - left, pad - top), wrapped, font=pil_font, fill=color, spacing=LINE_SPACING, align=align,
    )
    return image
