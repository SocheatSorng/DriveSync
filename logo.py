"""The Drive Sync logo: the "DS" monogram (green D, white S) on a dark rounded tile.

The letters come from assets/ds_mark.png, cut from "DS Monogram Logo Concept Sheet.png". The D is
re-colored on the fly, so the tray can show its state (green, amber, red, grey) while the S stays
white. `python logo.py` rewrites assets/drivesync.ico (the window and .exe icon).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

GREEN = "#01f35f"
TILE = "#161b22"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SS = 8  # tile supersampling factor
ASSETS = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
MARK_PATH = ASSETS / "ds_mark.png"


@lru_cache(maxsize=1)
def _mark() -> tuple[Image.Image, Image.Image]:
    """The letters' alpha, split into (D, S). The S is the white part, the D the green part."""
    mark = Image.open(MARK_PATH).convert("RGBA")
    alpha = mark.getchannel("A")
    is_s = mark.getchannel("R").point(lambda v: 255 if v > 128 else 0)
    black = Image.new("L", mark.size, 0)
    return Image.composite(alpha, black, is_s.point(lambda v: 255 - v)), Image.composite(alpha, black, is_s)


def draw(size: int, color: str = GREEN, tile: str | None = TILE) -> Image.Image:
    """The logo as a size x size RGBA image. tile=None draws the letters alone."""
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    if tile:
        ImageDraw.Draw(img).rounded_rectangle((0, 0, big - 1, big - 1), radius=big * 0.22, fill=tile)
    d_alpha, s_alpha = _mark()
    # Small sizes fill more of the tile, so the letters stay readable in the tray.
    width_frac = 0.88 if size <= 24 else 0.76
    if not tile:
        width_frac = 1.0
    w = round(big * width_frac)
    h = round(w * d_alpha.height / d_alpha.width)
    pos = ((big - w) // 2, (big - h) // 2)
    for alpha, fill in ((d_alpha, color), (s_alpha, "#ffffff")):
        layer = Image.new("RGBA", (w, h), fill)
        layer.putalpha(alpha.resize((w, h), Image.LANCZOS))
        img.alpha_composite(layer, pos)
    return img.resize((size, size), Image.LANCZOS)


def write_ico(path: Path) -> None:
    imgs = [draw(n) for n in ICO_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=imgs[:-1])


if __name__ == "__main__":
    out = ASSETS / "drivesync.ico"
    write_ico(out)
    print(f"Wrote {out}")
