"""The Drive Sync logo: the "DS" monogram in green on a transparent background.

The letters' shape comes from assets/ds_mark.png, cut from "DS Monogram Logo Concept Sheet.png".
Only its outline is used: both letters are drawn in one color, so the logo reads on light and dark
backgrounds alike and the tray can show its state (green, amber, red, grey).
`python logo.py` rewrites assets/drivesync.ico (the window and .exe icon).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

GREEN = "#01f35f"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SS = 8  # supersampling factor
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
MARK_PATH = ASSETS / "ds_mark.png"


@lru_cache(maxsize=1)
def _mark_alpha() -> Image.Image:
    """The letters' outline (alpha only)."""
    return Image.open(MARK_PATH).convert("RGBA").getchannel("A")


def draw(size: int, color: str = GREEN, tile: str | None = None) -> Image.Image:
    """The logo as a size x size RGBA image, transparent unless a tile color is given."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if tile:
        ImageDraw.Draw(img).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=tile)
    alpha = _mark_alpha()
    w = round(big * (0.8 if tile else 1.0))
    h = round(w * alpha.height / alpha.width)
    letters = Image.new("RGBA", (w, h), color)
    letters.putalpha(alpha.resize((w, h), Image.LANCZOS))
    img.alpha_composite(letters, ((big - w) // 2, (big - h) // 2))
    return img.resize((size, size), Image.LANCZOS)


def _badged(size: int, color: str, badge: str, symbol) -> Image.Image:
    """The logo with a round badge in its bottom-right corner; symbol(draw, cx, cy, d) draws on it.
    A transparent ring is cut around the badge so it stays readable at tray size (16 px)."""
    big = size * SS
    img = draw(big, color)
    d = big * 0.58                       # badge diameter
    x0, y0 = big - d, big - d
    gap = big * 0.07
    hole = Image.new("L", (big, big), 0)
    ImageDraw.Draw(hole).ellipse((x0 - gap, y0 - gap, big + gap, big + gap), fill=255)
    a = img.getchannel("A")
    a.paste(0, mask=hole)
    img.putalpha(a)
    dr = ImageDraw.Draw(img)
    dr.ellipse((x0, y0, big - 1, big - 1), fill=badge)
    symbol(dr, x0 + d / 2, y0 + d / 2, d)
    return img.resize((size, size), Image.LANCZOS)


def draw_paused(size: int, color: str = "#8b949e", badge: str = "#5f6368") -> Image.Image:
    """The logo in grey with a pause badge (two white bars)."""
    def bars(dr, cx, cy, d):
        bw, bh = d * 0.16, d * 0.46      # bar width / height
        for bx in (cx - bw * 1.25, cx + bw * 0.25):
            dr.rectangle((bx, cy - bh / 2, bx + bw, cy + bh / 2), fill="white")
    return _badged(size, color, badge, bars)


def draw_syncing_frames(size: int, color: str = "#ffb020", badge: str = "#5f6368",
                        frames: int = 8) -> list[Image.Image]:
    """The logo in amber with a spinning arc on its badge: one image per animation frame."""
    out = []
    for i in range(frames):
        def arc(dr, cx, cy, d, start=i * 360 / frames):
            r = d * 0.30
            dr.arc((cx - r, cy - r, cx + r, cy + r), start, start + 270, fill="white", width=max(1, round(d * 0.15)))
        out.append(_badged(size, color, badge, arc))
    return out


def write_ico(path: Path) -> None:
    imgs = [draw(n) for n in ICO_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=imgs[:-1])


if __name__ == "__main__":
    out = ASSETS / "drivesync.ico"
    write_ico(out)
    print(f"Wrote {out}")
