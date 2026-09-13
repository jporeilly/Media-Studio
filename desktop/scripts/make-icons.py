"""Pentaho-branded icon + NSIS installer art for Media Studio Enterprise.

2026 branding: the mark is the WORD Pentaho / a capital P, WHITE on BLACK - no
red, no swirl. The app icon carries a capital P (a word is mush at 24 px) plus a
small neutral "play" badge that says "media" without introducing a brand colour.

Self-contained: generates every size the Tauri Windows bundle needs (PNGs + a
multi-resolution .ico) and the NSIS header/sidebar bitmaps directly with Pillow,
so there is no dependency on `npx @tauri-apps/cli icon` or a network fetch.

    python scripts/make-icons.py

Requires Pillow (in the app's requirements.txt / venv).
"""
import os

from PIL import Image, ImageDraw, ImageFont, ImageFilter

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICONS = os.path.join(REPO, "src-tauri", "icons")
os.makedirs(ICONS, exist_ok=True)

WHITE = (255, 255, 255)
BLACK = (12, 12, 14)
BLACK_DEEP = (0, 0, 0)
# Neutral badge palette (NO red): a slate circle, white play glyph.
BADGE_BG = (38, 44, 54)
BADGE_RING = (230, 237, 243)


def brand_font(px):
    """Segoe UI Semibold - closest system face to the wordmark's weight."""
    for f in (r"C:\Windows\Fonts\seguisb.ttf", r"C:\Windows\Fonts\segoeuib.ttf",
              r"C:\Windows\Fonts\segoeui.ttf"):
        try:
            return ImageFont.truetype(f, px)
        except OSError:
            continue
    return ImageFont.load_default()


def text_mark(text, color, px=512):
    """Render `text` as a tight-cropped RGBA mark."""
    f = brand_font(px)
    scratch = Image.new("L", (px * len(text) + px, px * 2), 0)
    d = ImageDraw.Draw(scratch)
    d.text((px // 4, px // 4), text, font=f, fill=255)
    bb = scratch.getbbox()
    if bb is None:  # font produced nothing - fall back to a plain block
        bb = (0, 0, px, px)
    alpha = scratch.crop(bb)
    out = Image.new("RGBA", alpha.size, color + (0,))
    out.putalpha(alpha)
    return out


def scale_h(img, h):
    return img.resize((max(1, int(img.width * h / img.height)), int(h)), Image.LANCZOS)


def scale_w(img, w):
    return img.resize((int(w), max(1, int(img.height * w / img.width))), Image.LANCZOS)


def draw_play_badge(img, S):
    """A neutral 'play' badge in the lower-right: slate circle, white triangle.

    Pure shapes, no text: the badge must read at 16-24 px. A light ring separates
    the circle from the black tile behind it."""
    cx, cy, r = int(S * 0.735), int(S * 0.735), int(S * 0.205)
    ring = int(S * 0.024)
    d = ImageDraw.Draw(img)
    d.ellipse([cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring],
              fill=BADGE_RING + (255,))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=BADGE_BG + (255,))
    # Play triangle, optically centred (nudged right of the circle centre).
    t = r * 0.52
    ox = int(r * 0.10)
    pts = [(cx - t * 0.7 + ox, cy - t), (cx - t * 0.7 + ox, cy + t), (cx + t + ox, cy)]
    d.polygon(pts, fill=WHITE + (255,))


def make_master(badge=True):
    """The 1024 app icon: black rounded tile, white P, optional play badge."""
    S = 2048
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.19), fill=255)
    bg = Image.new("RGBA", (S, S), BLACK + (255,))
    shade = Image.new("L", (1, S))
    for y in range(S):
        shade.putpixel((0, y), int(140 * (y / S)))
    deep = Image.new("RGBA", (S, S), BLACK_DEEP + (255,))
    bg = Image.composite(deep, bg, shade.resize((S, S)))
    img.paste(bg, (0, 0), mask)

    p = scale_h(text_mark("P", WHITE), S * 0.60)
    px_ = int((S - p.width) / 2 - S * 0.045)
    py_ = int((S - p.height) / 2 - S * 0.055)
    img.alpha_composite(p, (px_, py_))
    if badge:
        draw_play_badge(img, S)
    return img.resize((1024, 1024), Image.LANCZOS)


def main():
    master = make_master(badge=True)
    master.save(os.path.join(ICONS, "icon-source.png"))

    # Windows/Tauri PNG sizes + base icon.png.
    sizes = {
        "32x32.png": 32,
        "64x64.png": 64,
        "128x128.png": 128,
        "128x128@2x.png": 256,
        "icon.png": 512,
    }
    for name, s in sizes.items():
        master.resize((s, s), Image.LANCZOS).save(os.path.join(ICONS, name))

    # Multi-resolution .ico (what tauri-build embeds in the exe and NSIS uses).
    ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(os.path.join(ICONS, "icon.ico"), format="ICO", sizes=ico_sizes)

    # ---- NSIS header 150x57 (black, wordmark only) ----
    SS = 8
    W, H = 150 * SS, 57 * SS
    hdr = Image.new("RGB", (W, H), BLACK)
    wmark = scale_h(text_mark("Pentaho", WHITE), H * 0.42)
    wx, wy = int(W * 0.06), int((H - wmark.height) / 2)
    hdr.paste(wmark, (wx, wy), wmark)
    hdr.resize((150, 57), Image.LANCZOS).save(os.path.join(ICONS, "nsis-header.bmp"), "BMP")

    # ---- NSIS sidebar 164x314 (black, P tile + wordmark + product name) ----
    W, H = 164 * SS, 314 * SS
    side = Image.new("RGB", (W, H), BLACK_DEEP)
    grad = Image.new("L", (1, H))
    for y in range(H):
        grad.putpixel((0, y), int(60 * (1 - y / H)))
    side = Image.composite(Image.new("RGB", (W, H), BLACK), side, grad.resize((W, H)))

    L = int(W * 0.44)
    tile = master.resize((L, L), Image.LANCZOS)
    side.paste(tile.convert("RGB"), (int((W - L) / 2), int(H * 0.10)), tile)

    wmark = scale_w(text_mark("Pentaho", WHITE), W * 0.68)
    side.paste(wmark, (int((W - wmark.width) / 2), int(H * 0.46)), wmark)

    d = ImageDraw.Draw(side)

    def fit_font(text, max_w, size):
        while size > int(W * 0.05):
            f = brand_font(size)
            bb = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox((0, 0), text, font=f)
            if bb[2] - bb[0] <= max_w:
                return f
            size = int(size * 0.92)
        return brand_font(size)

    def centered(y, text, font, fill):
        bb = d.textbbox((0, 0), text, font=font)
        d.text(((W - (bb[2] - bb[0])) / 2 - bb[0], y), text, font=font, fill=fill)

    for i, line in enumerate(["Media Studio", "Enterprise"]):
        f = fit_font(line, int(W * 0.88), int(W * 0.11))
        centered(H * (0.60 + i * 0.065), line, f, WHITE)
    f_str = fit_font("Turn slides into narrated video", int(W * 0.90), int(W * 0.068))
    centered(H * 0.75, "Turn slides into narrated video", f_str, (154, 167, 180))

    side.resize((164, 314), Image.LANCZOS).save(os.path.join(ICONS, "nsis-sidebar.bmp"), "BMP")

    print("icons written to", ICONS)


if __name__ == "__main__":
    main()
