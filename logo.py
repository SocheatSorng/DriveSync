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


def write_ico(path: Path) -> None:
    imgs = [draw(n) for n in ICO_SIZES]
    imgs[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=imgs[:-1])


if __name__ == "__main__":
    out = ASSETS / "drivesync.ico"
    write_ico(out)
    print(f"Wrote {out}")
